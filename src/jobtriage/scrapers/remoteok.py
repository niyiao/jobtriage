"""Remote OK job scraper.

Remote OK is the rare board that just hands you the file: one GET to
https://remoteok.com/api returns a 100-element array — [0] is feed
metadata (last_updated, legal), the rest are the newest ~99 jobs,
newest first, reaching back about three months. No params, no
pagination; the window is the window.

The traps are all cheap to clear:

- Cloudflare bans urllib's default User-Agent (error 1010); any
  ordinary browser UA walks straight through — we send the shared one.
- About one record in twenty is double-encoded UTF-8 ("MÃ¼nchen",
  "Sothebyâs") — a latin-1 -> utf-8 round-trip repairs them — and a
  few titles carry raw HTML entities ("&amp;"), which we unescape.
- salary_min/salary_max are unitless ints and 0 means missing. Under a
  million they read as USD per year ("$190k - $220k"; a sub-$1k value
  is hourly, presumably). One employer posts nine-figure
  local-currency values, which we carry raw rather than dress up as
  dollars.
- The board's `dev` tag is decorative: employers spray it on
  accountants and tax managers while DuckDuckGo's senior backend
  engineer ships without it. Dev-ness is decided the greenhouse way —
  a title battery (engineer / developer / devops / SRE / the stack
  words) minus the sales-engineering family (sales, solutions,
  support, implementation) and federal titles. dev_only=False turns
  the battery off.

Location vocabulary: `location` is free text, blank on half the feed —
blank means worldwide and is normalised to "Worldwide"; anything else
is kept verbatim as the tag ("Remote", "EMEA", "Warsaw",
"Remote - US"). The declarations below are the worldwide wordings plus
the EMEA/Europe wordings the feed uses, matched case-insensitively.
Employer-typed city wordings ("Warsaw", "München") are deliberately
not modelled: a city has no canonical area, and under containment a
city tag cannot cover a country or region preference anyway. Because
employers type this field themselves, a fresh city wording ("Lisbon")
won't match the default filter; locations=None shows everything. The
JSON-LD on the job pages hardcodes "Anywhere" for every job, so filter
on this field, not that one.

The feed's `legal` asks for a link back (follow, not nofollow) to the
job's remoteok.com URL and a mention of Remote OK as the source;
`url` below is that link — name the board wherever you show its jobs.
"""

import html
import re
from collections.abc import Sequence

from jobtriage import geo
from jobtriage.plugin import http

NAME = 'remoteok'
ALIASES = ('rok',)
TITLE = 'Remote OK'
HOMEPAGE = 'https://remoteok.com'
# The feed's `legal` asks for both: a follow link to the job's
# remoteok.com URL and Remote OK named as the source.
TERMS = 'Link back (follow) to the canonical Remote OK URL and credit Remote OK as the source.'
# The whole board is one GET.
POLITE_DELAY = 0

API_URL = 'https://remoteok.com/api'

# Location wordings meaning "open worldwide / EMEA", translated by
# geo.board_tags. Blank locations (half the feed) are normalised to
# "Worldwide"; the rest of the tags are the employer's own words, kept
# verbatim and matched case-insensitively.
#
# The awkward shape here is why the declarations are worth it: two real
# country wordings (Germany, Ireland) and the board's own EMEA/Europe
# buckets. The countries ride COUNTRY_STYLE='name' with every other EMEA
# code suppressed by the empty alias, so a solo ['DE'] preference emits
# one country wording ("Germany") and never 129 countries. City wordings
# the feed sometimes carries ("Warsaw", "Copenhagen", "Lausanne",
# "München") are not declared: a city has no canonical area, and a city
# tag cannot cover a country or region preference under containment.
_WORDING = {'DE': 'Germany', 'IE': 'Ireland'}

WORLDWIDE = (
	'Worldwide',
	'Remote',
	'Anywhere',
	'Anywhere in the World',
	'Global',
)
REGIONS = {
	'emea': ('EMEA',),
	'europe': ('Europe', 'Europe only'),
}
COUNTRY_STYLE = 'name'
COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}

# Remote OK has no category filter; "software" is the title battery's
# dev-only switch, which is what the default scrape has always used.
FAMILIES = {'software': {'dev_only': True}}

# Dev-ness by title, since the `dev` tag can't be trusted in either
# direction: the build words, minus the sales-engineering family and
# US-persons-only (federal) roles.
_IS_DEV_TITLE = re.compile(
	r'\b('
	r'engineer|engineering|developer|devops|sre|software'
	r'|full[ -]?stack|front[ -]?end|back[ -]?end|tech lead'
	r')\b',
	re.IGNORECASE,
)
_NOT_DEV_TITLE = re.compile(
	r'\b(sales|solutions|support|implementation)[ -]engineer\b|\bfederal\b',
	re.IGNORECASE,
)


def _clean(text: str) -> str:
	"""Repair double-encoded UTF-8, unescape HTML entities, trim."""
	try:
		text = text.encode('latin-1').decode('utf-8')
	except (UnicodeEncodeError, UnicodeDecodeError):
		pass  # not mojibake; leave it as served
	return html.unescape(text).strip()


def _money(value: int) -> str:
	"""190000 -> '$190k'; sub-$1k values (hourly, presumably) stay as-is."""
	if value >= 1000:
		return f'${value // 1000}k'
	return f'${value}'


def _salary(record: dict) -> str | None:
	"""Build a display string from the unitless salary ints, or None."""
	low = record.get('salary_min') or 0
	high = record.get('salary_max') or 0
	if low >= 10_000_000 or high >= 10_000_000:
		# One employer posts nine-figure local-currency values; carry
		# the raw digits rather than pretend they're dollars.
		return ' - '.join(str(value) for value in (low, high) if value) + ' (non-USD?)'
	if not low and not high:
		return None
	if not low:
		return _money(high)
	if not high or low == high:
		return _money(low)
	return f'{_money(low)} - {_money(high)}'


def recent_jobs(
	dev_only: bool = FAMILIES['software']['dev_only'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent jobs on Remote OK, newest first.

	dev_only: keep only developer jobs, decided by title — the board's
	own `dev` tag is decorative (see the module docstring).
	locations: keep only jobs whose location tag is one of these,
	e.g. ("Worldwide", "EMEA", "Germany"), matched case-insensitively;
	None applies no location filter. The pipeline computes the tags from
	the user's config with `geo.board_tags`.
	"""
	return parse(fetch(), dev_only=dev_only, locations=locations, limit=limit)


def fetch() -> list[dict]:
	"""GET the whole feed: a meta record first, then the newest ~99 jobs."""
	return http.get_json(API_URL)


def parse(
	raw: list[dict],
	*,
	dev_only: bool = FAMILIES['software']['dev_only'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Turn the feed into job dicts, newest first.

	Pure: dev-ness and location are client-side decisions because the
	feed has no working filter for either.
	"""
	wanted = {tag.casefold() for tag in locations} if locations else None
	jobs = []
	for record in raw:
		if not record.get('position'):  # [0] is the feed's meta record
			continue
		title = _clean(record['position'])
		is_dev = _IS_DEV_TITLE.search(title) and not _NOT_DEV_TITLE.search(title)
		if dev_only and not is_dev:
			continue
		location = _clean(record.get('location') or '')
		tag = location or 'Worldwide'  # blank location = open anywhere
		if wanted and tag.casefold() not in wanted:
			continue
		jobs.append(
			{
				'title': title,
				'company': _clean(record['company']),
				'url': record['url'],
				'published': record['date'],
				'locations': [tag],
				'salary': _salary(record),
			}
		)
	# The feed arrives newest-first; the sort is cheap insurance against
	# the day a sponsored pin lands in the middle.
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]
