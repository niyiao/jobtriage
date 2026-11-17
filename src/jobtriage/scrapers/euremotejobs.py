"""EuroRemoteJobs scraper.

euremotejobs.com is a WordPress site with WP Job Manager bolted on. The
REST route for listings answers 401, and the homepage render pins
featured jobs above the fresh ones — the real surface is the AJAX feed
the homepage's own JS calls:

	POST /wp-admin/admin-ajax.php
	action=erj_ajax_search&nonce=<from the homepage>&page=N
	[&search_region[]=<slug>...]

It serves 60 cards a page, newest first, with a `has_more` flag — a
cleaner feed than the homepage itself manages. The nonce sits in a
`var ERJ = {...}` blob in the homepage's inline JS and expires roughly
daily, and a stale one is answered with HTTP 403 over a success:false
JSON body (urllib raises before the JSON is ever read), so a refused
page is retried once after a fresh homepage fetch. Every run therefore
starts by fetching the homepage, and that same HTML carries the
location filter's checkboxes, whose slugs we read to map location
names; note the two SEO-suffixed oddities, 'norway-remote-jobs-europe'
and 'turkey-remote-jobs-europe'. The `website` POST field is a honeypot;
it is sent, empty, exactly the way the site's own JS sends it.

The location filter is server-side: `search_region[]` takes those
checkbox slugs ('remote-jobs-worldwide', 'remote-jobs-emea', country
slugs). WORLDWIDE/REGIONS below are the board's own Worldwide + EMEA
pair, so the default filter costs one param pair. Cards also carry a
free-text location line ('Worldwide', 'EMEA, US', 'UK'), which is what
client-side fallback filtering matches — the region buckets and the
card text are two taxonomies and do not always agree (a UK-only job can
sit in the EMEA bucket; a Europe-tagged one may sit in neither).

Traps: Cloudflare 1010 bans the default urllib UA and anything with
parentheses in it, so this module sends a bare 'Mozilla/5.0' (which has
passed from every egress tried). The www host redirects to the bare
domain. And the board's terms explicitly prohibit automated collection
— noted here; keep a run to one homepage fetch plus a few pages.

Card fields: a.job-card-link (url), h2.job-title, div.company-name,
div.meta-item.meta-location (comma-separated tags), a bare
div.meta-item whose text carries a currency (salary, ~20% of cards),
and time[datetime] — date only. Detail pages hold JobPosting JSON-LD
with the exact datePosted, but that costs one request per job and the
date alone sorts just as well.
"""

import json
import re
import sys
from collections.abc import Sequence
from html.parser import HTMLParser

from jobtriage import geo
from jobtriage.plugin import http

NAME = 'euremotejobs'
ALIASES = ('erj',)
TITLE = 'EuroRemoteJobs'
HOMEPAGE = 'https://euremotejobs.com'
# The board says so itself, and the shipped config disables this board
# for that reason; `sources info euremotejobs` is the reminder before a
# user enables it.
TERMS = 'The terms ban automated collection outright; use this board only if you accept them.'
# One homepage fetch plus a page or two, with a real gap between them.
POLITE_DELAY = 1.0

BASE_URL = 'https://euremotejobs.com'  # www redirects here; skip the hop
AJAX_URL = f'{BASE_URL}/wp-admin/admin-ajax.php'
# Plain on purpose: Cloudflare 1010 bans the default urllib UA and any
# UA with parentheses, so no 'Mozilla/5.0 (Macintosh; ...)'.
USER_AGENT = 'Mozilla/5.0'

# The nonce, inside the homepage's inline `var ERJ = {...}` object.
_NONCE_RE = re.compile(r"\bnonce:\s*'([^']+)'")
# Location filter checkboxes: value is the region slug, <span> its label.
_REGION_RE = re.compile(
	r'<input type="checkbox" class="erj-filter" data-param="search_region" '
	r'value="([^"]+)"[^>]*><span>([^<]+)</span>'
)
# A salary meta-item always carries a currency symbol or code.
_SALARY_RE = re.compile(r'[$€£]|\b(?:USD|EUR|GBP|CAD|CHF|SEK|NOK|DKK|PLN|AUD|NZD|ILS|ZAR)\b')

# The board's location vocabulary, translated by geo.board_tags: the two
# region buckets its own checkbox filter sends as `search_region[]`. They
# are server-side params, not client-side tags, which is why this board
# also declares `server_locations` below. Country-level buckets (and the
# third `remote-jobs-europe` bucket, which is mostly country-restricted)
# stay unmodelled: the region buckets are the board's own default. A
# country preference expands upward to the Worldwide bucket plus every
# region bucket that contains it (EMEA for Nigeria), which is exactly
# the server's own OR-of-regions filter; a country outside the modelled
# regions still gets the Worldwide bucket.
WORLDWIDE = ('remote-jobs-worldwide',)
REGIONS = {'emea': ('remote-jobs-emea',)}
COUNTRY_STYLE = 'none'

# The board has no category filter — it is a tech-jobs board — so the
# software family is the same request as every other. Declared anyway so
# step 4 can match families against the board instead of guessing.
FAMILIES = {'software': {}}


def server_locations(prefs) -> dict:
	"""The AJAX query params for the user's prefs, server-side.

	Unlike the client-side boards, this one filters location in the
	request, and the values it wants are exactly the slugs
	`geo.board_tags` produces. Returning them as a param dict gives the
	pipeline one documented seam instead of making it infer that a
	location tag here is a `search_region[]` value.
	"""
	return {'search_region[]': list(geo.board_tags(sys.modules[__name__], prefs))}


class _CardParser(HTMLParser):
	"""Pull job cards out of one page of the AJAX feed's HTML."""

	_VOID = frozenset({'img', 'br', 'hr', 'input', 'meta', 'link', 'source'})

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.jobs = []
		self._card = None
		self._capture = None  # (field, tag, [chunks]) while inside a target

	def handle_starttag(self, tag, attrs):
		if tag in self._VOID:
			return
		attrs = dict(attrs)
		classes = set(attrs.get('class', '').split())
		if tag == 'a' and 'job-card-link' in classes:
			self._card = {'url': attrs.get('href', '')}
		elif self._card is None:
			return
		elif tag == 'time':
			self._card['published'] = attrs.get('datetime', '')
		elif tag == 'h2' and 'job-title' in classes:
			self._capture = ('title', tag, [])
		elif tag == 'div' and 'company-name' in classes:
			self._capture = ('company', tag, [])
		elif tag == 'div' and 'meta-location' in classes:
			self._capture = ('location', tag, [])
		elif tag == 'div' and classes == {'meta-item'}:
			self._capture = ('salary', tag, [])

	def handle_data(self, data):
		if self._capture is not None:
			self._capture[2].append(data)

	def handle_endtag(self, tag):
		if tag in self._VOID:
			return
		if tag == 'a' and self._card is not None:
			self._finish_card()
		elif self._capture is not None and tag == self._capture[1]:
			field, _, chunks = self._capture
			self._capture = None
			if self._card is not None:
				self._card.setdefault(field, ''.join(chunks).strip())

	def _finish_card(self):
		card, self._card = self._card, None
		if card.get('title') and card.get('url') and card.get('published'):
			self.jobs.append(card)


def _nonce_and_regions(home: str) -> tuple[str, list[tuple[str, str]]]:
	"""The AJAX nonce and the location-filter (slug, label) pairs."""
	match = _NONCE_RE.search(home)
	if not match:
		raise ValueError('no AJAX nonce on the euremotejobs homepage; layout changed?')
	return match.group(1), _REGION_RE.findall(home)


def _ajax(page: int, nonce: str, regions: Sequence[str] = ()) -> dict:
	"""POST one page of the AJAX feed; return the parsed JSON."""
	fields = {
		'action': 'erj_ajax_search',
		'nonce': nonce,
		'page': str(page),
		'website': '',  # honeypot, sent empty like the site's own JS
		'search_region[]': list(regions),
	}
	try:
		return json.loads(http.post_form(AJAX_URL, fields, ua=USER_AGENT))
	except http.HttpError as error:
		# A dead nonce comes back as HTTP 403 with a success:false JSON
		# body, not as a 200 — surface the body so the caller can
		# refresh the nonce and retry. Anything else (a Cloudflare
		# block page, say) stays an error.
		if error.status == 403:
			try:
				return json.loads(error.body)
			except ValueError:
				pass
		raise


def _resolve_filter(locations, region_pairs):
	"""Turn `locations` into (region slugs, client-side tag set).

	Names the board knows — a region slug or a checkbox label — go to
	the server as `search_region[]` params. Anything it doesn't know
	switches the whole filter client-side, matching the card's own
	location tags instead (slugs still count via their labels).
	"""
	if not locations:
		return [], None  # no filter: --all mode
	slug_by_word = {}
	for slug, label in region_pairs:
		slug_by_word[label.casefold()] = slug
		slug_by_word[slug] = slug
	regions, unmapped = [], False
	for location in locations:
		slug = slug_by_word.get(str(location).casefold())
		if slug is None:
			unmapped = True
		elif slug not in regions:
			regions.append(slug)
	if not unmapped:
		return regions, None
	wanted = {str(location).casefold() for location in locations}
	wanted |= {label.casefold() for slug, label in region_pairs if slug in regions}
	return [], wanted


def recent_jobs(
	locations: Sequence[str] | None = None,
	limit: int = 50,
	max_pages: int = 5,
) -> list[dict]:
	"""Return the most recent jobs on EuroRemoteJobs, newest first.

	locations: region slugs or checkbox labels from the board's location
	filter, e.g. ('remote-jobs-worldwide', 'remote-jobs-emea') as
	`geo.board_tags` computes them, or ('Germany', 'UK'); names the board
	knows filter server-side, any other name switches to a client-side
	match on the card's location tags. None (or empty) applies no
	location filter.
	limit: stop once this many matching jobs are collected.
	max_pages: hard cap on AJAX pages per run (60 cards each; the board
	adds ~130 jobs a day, so five pages span a couple of days).

	published is date-precision (the cards only carry a date); the feed
	itself is newest-first, so same-day order is the board's own.
	"""
	if limit <= 0:
		return []
	return parse(fetch(locations, limit, max_pages), locations=locations, limit=limit)


def fetch(
	locations: Sequence[str] | None = None,
	limit: int = 50,
	max_pages: int = 5,
) -> dict:
	"""Fetch the homepage nonce and up to `max_pages` AJAX pages.

	Returns `{'region_pairs': [...], 'pages': [html, ...]}` so `parse`
	can repeat the filter resolution without a request. A stale nonce is
	answered with a 403 whose JSON body says success:false; refreshing
	the homepage and retrying once is the documented recovery.
	"""
	home = http.get_text(f'{BASE_URL}/', ua=USER_AGENT)
	nonce, region_pairs = _nonce_and_regions(home)
	regions, _wanted = _resolve_filter(locations, region_pairs)

	page_cap = max(1, max_pages)
	pages = []
	seen = 0
	for page in range(1, page_cap + 1):
		data = _ajax(page, nonce, regions)
		if not data.get('success'):
			# Nonce expired — a stale one arrives as a 403 whose JSON
			# body says success:false. Refetch the homepage for a fresh
			# one and retry this page once.
			home = http.get_text(f'{BASE_URL}/', ua=USER_AGENT)
			nonce, region_pairs = _nonce_and_regions(home)
			data = _ajax(page, nonce, regions)
			if not data.get('success'):
				raise ValueError('euremotejobs refuses the AJAX nonce even after refresh')
		payload = data.get('data') or {}
		document = payload.get('html') or ''
		pages.append(document)
		# Count cards, not matches: a page is 60 candidates and the
		# location filter runs in `parse`. Stopping once the page's own
		# population covers `limit` keeps the default run to one request
		# while letting a `max_pages` override reach deeper.
		counted = _CardParser()
		counted.feed(document)
		seen += len(counted.jobs)
		if not payload.get('has_more') or seen >= limit:
			break
	return {'region_pairs': region_pairs, 'pages': pages}


def parse(raw: dict, *, locations: Sequence[str] | None = None, limit: int = 50) -> list[dict]:
	"""Turn the AJAX pages into job dicts, newest first.

	Pure: the card text is the fallback filter, used when `locations`
	names something the board's own filter does not know.
	"""
	_regions, wanted = _resolve_filter(locations, raw.get('region_pairs') or [])

	jobs, seen = [], set()
	for document in raw.get('pages') or []:
		parser = _CardParser()
		parser.feed(document)
		for card in parser.jobs:
			if card['url'] in seen:  # pages can drift while new jobs land
				continue
			seen.add(card['url'])
			tags = [tag.strip() for tag in card.get('location', '').split(',') if tag.strip()]
			if wanted is not None and not wanted & {tag.casefold() for tag in tags}:
				continue
			salary = card.get('salary')
			jobs.append(
				{
					'title': card['title'],
					'company': card.get('company', ''),
					'url': card['url'],
					'published': card['published'],
					'locations': tags,
					'salary': salary if salary and _SALARY_RE.search(salary) else None,
				}
			)
	# The feed is already newest-first; a stable re-sort is insurance
	# against any pinned card, keeping same-day order as served.
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]
