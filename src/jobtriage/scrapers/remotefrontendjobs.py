"""Remote Frontend Jobs scraper.

The rare job board that documents a real API and means it: free, no
key, no signup (advertised at /developers), asking only that we link
to its pages and credit it by name. One endpoint serves the whole
three-board network — board=frontend (this site), backend
(remotebackendjobs.com) or java (remotejavajobs.com):

    GET https://www.remotefrontendjobs.com/api/jobs
        ?board=frontend&regions=europe,africa,middle-east,worldwide&limit=100

The regions param does the location filter server-side; limit/offset
page through at most 250 rows of the last 30 days. Per job: title,
author (the company), isoDate, geoRegions tags, and
salaryMinYearly/salaryMaxYearly in USD with a formatted salary string.

Traps, all met live:

- Cloudflare 403s the default urllib User-Agent. One browser header
  and it is polite again.
- The feed is not date-ordered: featured listings ride on top, so we
  re-sort by isoDate client-side.
- Nothing is fresher than about a day. Jobs picked up from career
  pages are held back 24 hours for Pro members first (postedWithin=24h
  returns 0 by design); only jobs employers post directly show up at
  once.
- The board aggregates the aggregators, and they cross-post per
  country: one Jobgether listing arrived four times, Grafana via
  three 4dayweek mirrors. We dedupe on title+company, keeping the
  newest copy.
- The salary parser occasionally reports '1k' or '30' a year; a
  yearly figure under 10,000 USD is treated as bogus and dropped.

Usage terms require linking the board's canonical /<id> page — that is
the url below, never the employer's ATS link.

Location vocabulary: geoRegions tags from the site's own region
filter — 'europe', 'africa', 'middle-east', 'worldwide' (plus
'north-america', 'latin-america', 'asia', 'oceania'). A handful of
jobs carry no tags at all; in --all mode they surface with
locations=[].

There is no single EMEA tag here, only the three regions: a job open
across EMEA carries europe+africa+middle-east. Under containment an
African preference (Nigeria) still finds it through the africa tag,
because a job's tags are alternatives (OR): being open in Africa is
being open to an African worker, whatever else it is also open to.
"""

import sys
from collections.abc import Sequence

from jobtriage import geo
from jobtriage.plugin import dates, http

NAME = 'remotefrontendjobs'
ALIASES = ('rfj',)
TITLE = 'Remote Frontend Jobs'
HOMEPAGE = 'https://www.remotefrontendjobs.com'
# Up to three API pages per run; a friendly public API still deserves
# a gap.
POLITE_DELAY = 0.5

API_URL = 'https://www.remotefrontendjobs.com/api/jobs'

# One API, three boards; each board's canonical job pages live on its
# own site, every job at /<id>.
BOARD_SITES = {
	'frontend': 'www.remotefrontendjobs.com',
	'backend': 'www.remotebackendjobs.com',
	'java': 'www.remotejavajobs.com',
}

# geoRegions tags meaning "open worldwide / Europe / Africa / the Middle
# East" — the board's own region-filter vocabulary, as the API words it,
# translated by geo.board_tags. The API has no country parameter at all,
# so a country preference maps onto the worldwide tag plus every declared
# region that contains it: COUNTRY_STYLE='none' and the region tags are
# the whole vocabulary. Each tag sits under its true area and there is no
# invented EMEA key: a job open across all three of europe, africa and
# middle-east carries all three tags, and an African preference (Nigeria)
# matches it through the africa tag by OR semantics — the same way any
# multi-tag job matches.
WORLDWIDE = ('worldwide',)
REGIONS = {
	'europe': ('europe',),
	'africa': ('africa',),
	'middle-east': ('middle-east',),
}
COUNTRY_STYLE = 'none'

# The API is one endpoint serving three boards; `board` selects the
# family's site. "software" is the frontend board the default scrape has
# always read (backend and java are the sister boards).
FAMILIES = {'software': {'board': 'frontend'}}

# The `regions` param's own vocabulary: a requested tuple inside it can
# ride the server-side filter instead of the client-side tag match.
_SERVER_TAGS = frozenset((*WORLDWIDE, *(tag for tags in REGIONS.values() for tag in tags)))


def server_locations(prefs) -> dict:
	"""The API query params for the user's prefs, server-side.

	The `regions` param is a set to the API (order is not significant),
	so the values are the tags `geo.board_tags` computed, joined — the
	old code compared tuples by order, which silently stopped matching
	once geo reordered the same four tags. A country outside the board's
	regions still contributes the worldwide tag — a job open anywhere is
	open to it — so only an empty tuple yields no param.
	"""
	tags = geo.board_tags(sys.modules[__name__], prefs)
	return {'regions': ','.join(tags)} if tags else {}


_PAGE_SIZE = 100  # rows per request
_MAX_WINDOW = 250  # API hard ceiling: offset + limit <= 250
_MIN_SANE_YEARLY = 10_000  # a real yearly salary (USD) has five figures


def _salary(job: dict) -> str | None:
	"""Return a sane salary string, or None when the parser slipped.

	salaryMinYearly/salaryMaxYearly are USD-converted ints and `salary`
	is the board's formatting of the same numbers ('€108k - €145k per
	year'). When the parse went wrong the numbers say so: '1k' a year
	parses to 1000, '30' to 360 — under any real yearly salary.
	"""
	lo = job.get('salaryMinYearly')
	hi = job.get('salaryMaxYearly')
	bogus = (isinstance(lo, int) and lo < _MIN_SANE_YEARLY) or (
		isinstance(hi, int) and hi < _MIN_SANE_YEARLY
	)
	if bogus:
		return None
	if job.get('salary'):
		return job['salary']
	if isinstance(lo, int) and isinstance(hi, int):
		return f'${lo:,} - ${hi:,} yearly'  # formatted string missing
	return None


def _parse_job(job: dict, site: str) -> dict:
	return {
		'title': job['title'].strip(),
		'company': job['author'].strip(),
		# Usage terms: link the board's canonical /<id> page on its own
		# site, not the employer's ATS link.
		'url': f'https://{site}/{job["id"]}',
		'published': dates.iso(job['isoDate']),
		# geoRegions is a comma-padded string (',europe,'); sometimes
		# null instead.
		'locations': [tag for tag in (job.get('geoRegions') or '').split(',') if tag],
		'salary': _salary(job),
	}


def recent_jobs(
	board: str = FAMILIES['software']['board'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent jobs on the board, newest first.

	board: 'frontend' (default, the software family), 'backend' or
		'java' — the three boards sharing this API
		(remotebackendjobs.com, remotejavajobs.com).
	locations: keep only jobs open to at least one of these geoRegions
		tags. A tuple inside the board's own four-region vocabulary is
		filtered server-side via the regions param; any other non-empty
		sequence is matched client-side against each job's tags; None
		applies no location filter. The pipeline computes the tags from
		the user's config with `geo.board_tags`.
	"""
	return parse(fetch(board, locations, limit), board=board, locations=locations, limit=limit)


def fetch(
	board: str = FAMILIES['software']['board'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[list[dict]]:
	"""Page through the API, one row batch per request.

	A tuple inside the board's own region vocabulary rides the
	server-side `regions` param; anything else is fetched whole and
	filtered in `parse`.
	"""
	if board not in BOARD_SITES:
		raise ValueError(f'unknown board {board!r}; expected one of {sorted(BOARD_SITES)}')
	# Set membership, not tuple equality: the API treats `regions` as a
	# set, and geo orders the same tags differently from the old tuple.
	server_filter = bool(locations) and {str(tag) for tag in locations} <= _SERVER_TAGS
	region_params = list(dict.fromkeys(locations)) if server_filter else []

	# One page covers the CLI's default 50 even after dedupe; fetch
	# more pages only for bigger limits, never past the 250-row window.
	pages = max(1, -(-limit // _PAGE_SIZE))  # ceil
	target = min(_MAX_WINDOW, pages * _PAGE_SIZE)

	batches = []
	for offset in range(0, target, _PAGE_SIZE):
		size = min(_PAGE_SIZE, target - offset)
		params = [
			('board', board),
			('limit', str(size)),
			('offset', str(offset)),
		]
		if server_filter:
			params.append(('regions', ','.join(region_params)))
		query = '&'.join(f'{key}={value}' for key, value in params)
		batch = http.get_json(f'{API_URL}?{query}')
		batches.append(batch)
		if len(batch) < size:
			break  # the 30-day listing window is exhausted
	return batches


def parse(
	raw: Sequence[Sequence[dict]],
	*,
	board: str = FAMILIES['software']['board'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Turn API batches into job dicts, newest first.

	Pure: a custom tag set is matched client-side here; then the
	aggregator's cross-posts collapse by (title, company), keeping the
	newest copy.
	"""
	if board not in BOARD_SITES:
		raise ValueError(f'unknown board {board!r}; expected one of {sorted(BOARD_SITES)}')
	site = BOARD_SITES[board]
	server_filter = bool(locations) and {str(tag) for tag in locations} <= _SERVER_TAGS
	jobs = [_parse_job(row, site) for batch in raw for row in batch]

	if locations and not server_filter:  # a custom tag set: filter here
		wanted = set(locations)
		jobs = [job for job in jobs if wanted & set(job['locations'])]

	# Featured listings ship first, not newest; restore date order
	# (stable, so same-second posts keep feed order).
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)

	# Aggregator cross-posts: keep the newest copy of each
	# title+company pair. On heavy cross-post days this can leave the
	# result a few short of limit.
	seen: set[tuple[str, str]] = set()
	unique: list[dict] = []
	for job in jobs:
		pair = (job['title'], job['company'])
		if pair not in seen:
			seen.add(pair)
			unique.append(job)
	return unique[:limit]
