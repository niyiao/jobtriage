"""Remocate job scraper.

Remocate.app is an aggregator board on Next.js, fully server-rendered:
the HTML urllib gets is the HTML a browser gets, cards included — no
API to find, no browser to drive, and robots plus terms that don't mind
us asking. The listing endpoint is

    GET https://www.remocate.app/?workType=remote&category=development
        [&page=N]                                 (20 cards per page)

strictly newest first, no sponsored pins to unwind, and a 404 once past
the last page — which doubles as the stop signal while paginating. A
`fresh=week` param exists but is deliberately unused: the board picks
up only ~2 jobs a day, so a week of fresh is ~14 jobs and would starve
a 50-job default run. We walk the plain listing instead and stop as soon
as `limit` matching jobs are in hand, capped at MAX_PAGES pages — every
request is cache-control: no-store, so a run should stay a handful of
hits to origin.

Traps the markup sets for the unwary:

- The page is React server-component soup: <script> chunks are
  interleaved WITH the markup, not appended after it, so cutting the
  document at the first </script> discards most of the cards. Parse
  with html.parser end to end.
- The badge row on each card is the real data model: an optional salary
  span (title="Salary from the job post"), the work type ("Remote" or
  "Relocation"), one category badge whose label is wrapped in a nested
  span, and zero or one country badge whose label sits directly in the
  span after a flag emoji. A card with no country badge is open
  worldwide — verified against the job page's JSON-LD
  (applicantLocationRequirements: "World").
- <time datetime> is remocate's own first-seen stamp, not the
  employer's posting date: sources re-sync daily around 05:30 UTC. We
  normalise it to a naive UTC ISO string at second resolution.
- Salary is the card's display string verbatim, in whatever currency
  the post used (€/£/$ ranges appear).

Location vocabulary: the badge text matches the site's "Filter by
country" select verbatim, flags aside. "World" is the open-anywhere
marker and "Europe" the region badge; note the board's own spellings —
"Great Britain", not "UK"; "USA", not "United States".
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from html.parser import HTMLParser

from jobtriage import geo
from jobtriage.plugin import http

NAME = 'remocate'
ALIASES = ('rc',)
TITLE = 'Remocate'
HOMEPAGE = 'https://www.remocate.app'
# Up to five listing pages, 20 cards each; every request hits origin.
POLITE_DELAY = 0.5

BASE_URL = 'https://www.remocate.app'

# Gentle-guest cap: the listing is 20 cards a page and every request
# hits origin (no-store), so a run fetches at most this many pages.
MAX_PAGES = 5
# Cards per listing page; the page count for a limit follows from it.
PAGE_SIZE = 20

# Elements that show up self-closed (or bare) in this markup and carry
# no text we read; they must not unbalance the card depth count.
_VOID = frozenset(
	{
		'img',
		'br',
		'hr',
		'input',
		'meta',
		'link',
		'source',
		'path',
		'circle',
		'rect',
		'line',
		'polyline',
		'polygon',
		'use',
	}
)

# Badge strings that mark the card's work type rather than a country.
_WORK_TYPES = ('Remote', 'Relocation')

# Tooltip on the card's salary span.
_SALARY_TITLE = 'Salary from the job post'

# Country-badge values as the board words them, translated by
# geo.board_tags: "World" for open-anywhere, "Europe" for the region
# badge, then every EMEA country in the board's 64-value filter
# vocabulary. `_WORDING` lists the 40 it carries and every other EMEA
# code is suppressed by the empty alias — no country is invented. The
# board's own spellings are kept: "Great Britain", not "UK";
# "Russian Federation"; "Turkey"; "Czechia" is the display name.
#
# Left out on purpose: "LatAm", "USA" and the other Americas/Asia-Pacific
# entries, and Central Asia (Kazakhstan, Uzbekistan), which is outside
# the Europe/Middle East/Africa scope the other modules use.
_WORDING = {
	'AE': 'United Arab Emirates',
	'AM': 'Armenia',
	'AT': 'Austria',
	'BE': 'Belgium',
	'BG': 'Bulgaria',
	'BY': 'Belarus',
	'CH': 'Switzerland',
	'CY': 'Cyprus',
	'CZ': 'Czechia',
	'DE': 'Germany',
	'DK': 'Denmark',
	'EE': 'Estonia',
	'EG': 'Egypt',
	'ES': 'Spain',
	'FR': 'France',
	'GB': 'Great Britain',
	'GE': 'Georgia',
	'GR': 'Greece',
	'HR': 'Croatia',
	'HU': 'Hungary',
	'IE': 'Ireland',
	'IL': 'Israel',
	'IT': 'Italy',
	'LT': 'Lithuania',
	'LU': 'Luxembourg',
	'MD': 'Moldova',
	'ME': 'Montenegro',
	'MT': 'Malta',
	'NL': 'Netherlands',
	'PL': 'Poland',
	'PT': 'Portugal',
	'QA': 'Qatar',
	'RO': 'Romania',
	'RS': 'Serbia',
	'RU': 'Russian Federation',
	'SE': 'Sweden',
	'SI': 'Slovenia',
	'TR': 'Turkey',
	'UA': 'Ukraine',
	'ZA': 'South Africa',
}

WORLDWIDE = ('World',)
# The board has one area tag, "Europe", so it sits under `europe`; an
# EMEA or Africa preference asks for no region tag here (a Europe-only
# job does not cover them) and keeps the worldwide wording.
REGIONS = {'europe': ('Europe',)}
COUNTRY_STYLE = 'name'
COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}

# The board's categories are functions; development is its largest and
# the one the default scrape has always read.
FAMILIES = {'software': {'category': 'development'}}


class _ListingParser(HTMLParser):
	"""Pull job cards out of one rendered listing page.

	A card is one <a href="/jobs/..."> block. Inside it we read the
	<h2> as the title, the lone <p> as the company, <time>'s datetime
	as the published stamp, and the badge row — the card's only
	"flex-wrap" div — as structured data. RSC script chunks interleaved
	with the markup are just elements we walk past on the way.
	"""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.cards = []  # finished, validated cards
		self.badge_count = 0  # badges of any kind, for the layout guard
		self._card = None
		self._depth = 0  # open non-void elements inside the current card
		self._stack = []  # (tag, attrs) of those elements
		self._row_depth = None  # depth of the open badge row, else None
		self._badge = None  # [attrs, direct-text-parts] of the open badge

	def handle_starttag(self, tag, attrs):
		attrs = dict(attrs)
		if self._card is None:
			if tag == 'a' and attrs.get('href', '').startswith('/jobs/'):
				self._card = {
					'url': BASE_URL + attrs['href'],
					'title': [],
					'company': [],
					'stamp': None,
					'salary': None,
					'countries': [],
				}
				self._depth = 0
				self._stack = []
			return
		if tag in _VOID:
			return
		self._stack.append((tag, attrs))
		self._depth += 1
		if tag == 'time' and attrs.get('datetime'):
			# html.parser lower-cases attribute names, so React's
			# dateTime arrives as datetime.
			self._card['stamp'] = attrs['datetime']
		elif tag == 'div' and self._row_depth is None and 'flex-wrap' in attrs.get('class', ''):
			self._row_depth = self._depth
		elif tag == 'span' and self._row_depth is not None and self._depth == self._row_depth + 1:
			self._badge = [attrs, []]

	def handle_endtag(self, tag):
		if self._card is None or tag in _VOID:
			return
		if tag == 'a' and self._depth == 0:
			self._close_card()
			return
		if tag == 'span' and self._badge is not None and self._depth == self._row_depth + 1:
			self._close_badge()
		elif tag == 'div' and self._depth == self._row_depth:
			self._row_depth = None
		if self._stack:
			self._stack.pop()
		self._depth -= 1

	def handle_data(self, data):
		if self._card is None or not self._stack:
			return
		tags = [tag for tag, _ in self._stack]
		if 'h2' in tags:
			self._card['title'].append(data)
		elif 'p' in tags:
			self._card['company'].append(data)
		elif self._badge is not None and self._depth == self._row_depth + 1:
			# Text directly inside the badge span; text in nested spans
			# (flag emojis, category labels) never reaches here.
			self._badge[1].append(data)

	def _close_badge(self):
		attrs, parts = self._badge
		self._badge = None
		self.badge_count += 1
		text = ''.join(parts).strip()
		if attrs.get('title') == _SALARY_TITLE:
			self._card['salary'] = text or None
		elif text and text not in _WORK_TYPES:
			self._card['countries'].append(text)

	def _close_card(self):
		card, self._card = self._card, None
		self._row_depth = None
		self._badge = None
		self._stack = []
		self._depth = 0
		title = ''.join(card['title']).strip()
		company = ''.join(card['company']).strip()
		if not (title and company and card['stamp']):
			return  # a stray /jobs/ link, not a card
		try:
			published = datetime.fromisoformat(card['stamp'])
		except ValueError:
			return  # malformed stamp; drop the card, not the run
		if published.tzinfo:
			published = published.astimezone(UTC).replace(tzinfo=None)
		self.cards.append(
			{
				'title': title,
				'company': company,
				'url': card['url'],
				'published': published.replace(microsecond=0).isoformat(),
				'countries': card['countries'],
				'salary': card['salary'],
			}
		)


def recent_jobs(
	category: str = FAMILIES['software']['category'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent remote jobs on Remocate, newest first.

	category: a Remocate category slug (default: the software family's
	development category).
	locations: keep only jobs whose country badges include one of these
	values, e.g. ("World", "Europe", "Poland"); None applies no location
	filter. Jobs with no country badge count as "World". The pipeline
	computes the tags from the user's config with `geo.board_tags`.
	limit: stop paginating once this many matching jobs are in hand; the
	MAX_PAGES cap means very large limits can come up short.
	"""
	if limit <= 0:
		return []
	pages = min(MAX_PAGES, max(1, -(-limit // PAGE_SIZE)))
	return parse(fetch(category, pages), locations=locations, limit=limit)


def fetch(category: str = FAMILIES['software']['category'], pages: int = MAX_PAGES) -> list[str]:
	"""GET the listing pages; a 404 past the first page ends pagination."""
	documents = []
	for page in range(1, pages + 1):
		query = f'/?workType=remote&category={category}'
		if page > 1:
			query += f'&page={page}'
		try:
			documents.append(http.get_text(BASE_URL + query))
		except http.HttpError as error:
			# Filtered listings 404 once past the last page.
			if error.status == 404 and page > 1:
				break
			raise
	return documents


def parse(
	raw: Sequence[str], *, locations: Sequence[str] | None = None, limit: int = 50
) -> list[dict]:
	"""Turn the listing pages into job dicts, newest first.

	Pure: the first page's guards make a markup change loud instead of
	looking like a quiet day; later pages are merged and deduped.
	"""
	if not raw:
		return []
	jobs = []
	seen = set()
	first = _ListingParser()
	first.feed(raw[0])
	if not first.cards:
		raise ValueError('no job cards on the Remocate listing; page layout changed?')
	if not first.badge_count:
		raise ValueError('job cards found but no badges parsed; card layout changed?')
	if locations and not any(card['countries'] for card in first.cards):
		raise ValueError('no country badges parsed with a location filter active; card layout changed?')
	for document in raw:
		parser = _ListingParser()
		parser.feed(document)
		for card in parser.cards:
			if card['url'] in seen:
				continue  # the board clamped us back to an earlier page
			seen.add(card['url'])
			countries = card['countries'] or ['World']
			if locations and not set(locations) & set(countries):
				continue
			jobs.append(
				{
					'title': card['title'],
					'company': card['company'],
					'url': card['url'],
					'published': card['published'],
					'locations': countries,
					'salary': card['salary'],
				}
			)
	# The listing is already newest-first; this just irons the seams
	# between pages. Uniform ISO strings sort chronologically.
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]
