"""BruntWork job scraper.

bruntworkcareers.co is a single-employer board — every job is BruntWork's,
an outsourcing company hiring remote contractors — built on Next.js with a
private HubSpot backend. There is no public jobs API: robots.txt disallows
/api/ (job alerts and HubSpot forms live there), and the jobs themselves
are fetched server-side only. What the scraper can reach is the
server-rendered pages, whose data rides as React flight rows inside the
`self.__next_f.push([1,"..."])` script tags:

    /search?category=<name>  the whole category in one request — no
                             pagination (an unfiltered /search ships all
                             ~660 open jobs the same way). Each card has
                             the title, the job id, and exactly one badge:
                             the employment type, or — for the few jobs
                             that carry one — a location tag.
    /jobs/<id>               the "Published on" date in the sidebar
                             ("Nov 09 2026" — date-only, no time).

The listing carries no dates, so we fetch the detail page for each job we
return: one request per job, only for the jobs that survive the location
filter and the limit.

The listing order is job id descending; the ids are HubSpot record ids,
minted in creation order, so that order is publication order (true for
every job checked). We still stable-sort by the fetched dates, so
same-day ties stay in board order and any drift sinks to the bottom.

Traps: the flight pushes are plain JSON, so json.loads beats eval; the
card badge is type-or-location, so a location-tagged job shows the tag
where others show "Full Time (35 hours or more per week)" — the type is
simply empty on those; and the site's own filter knobs (title, jobType,
timezone) key off the client's timezone, not the worker's — "Work
Schedule and Timezone" on a detail page is where the CLIENT is ("CA",
"UK TIme"). There is no salary data on the board at all; pay is
"established based on your performance in the application process".

Location vocabulary: the board has exactly one location tag —
"Australia, New Zealand and Asia", on a handful of older jobs. Every
other job states no location restriction, and BruntWork markets its
roles as work-from-anywhere for contractors across ~50 countries, so we
tag those jobs "Worldwide" ourselves (the justremote "Anywhere" move).
The ANZ/Asia tag is a regional restriction that is neither worldwide
nor EMEA, so it stays out of the declarations; --all is the way to see
it.
"""

import json
import re
import urllib.parse
from collections.abc import Sequence

from jobtriage.plugin import dates, http

NAME = 'bruntwork'
ALIASES = ('bw',)
TITLE = 'BruntWork'
HOMEPAGE = 'https://www.bruntworkcareers.co'
# One listing request plus one detail page per returned job; the board
# is a single employer, so a dozen requests is a default run.
POLITE_DELAY = 0.5

BASE_URL = 'https://www.bruntworkcareers.co'

# Pseudo-tag for jobs that carry no location restriction; the module
# synthesises it (the justremote "Anywhere" move).
ANYWHERE = 'Worldwide'

# Location declarations, translated by geo.board_tags. The pseudo-tag is
# the board's only worldwide wording and there are no region tags. The
# one real tag, "Australia, New Zealand and Asia", stays out: it
# restricts hiring to APAC, which is neither worldwide nor EMEA, so an
# APAC-only preference gets () and the pipeline passes locations=None.
# That is acceptable — --all is the way to see those jobs.
WORLDWIDE = (ANYWHERE,)
REGIONS = {}
COUNTRY_STYLE = 'none'

# The board's spelling of the developer category, spaces and all. It is
# the category the default scrape has always read.
FAMILIES = {'software': {'category': 'Website and Software Development'}}

# Flight rows are pushed as JSON arrays; the payload cannot contain a
# literal "</script>", so the lazy match stops at the real tag end.
_PUSH_RE = re.compile(r'self\.__next_f\.push\((\[.*?\])\)</script>', re.DOTALL)

# A listing card: ["$","$L17","N",{"className":"w-full","href":"/jobs/<id>"...
_CARD_RE = re.compile(
	r'\["\$","\$L17","\d+",\{"className":"w-full","href":"(/jobs/\d+)","children":\['
)
_TITLE_RE = re.compile(r'font-normal","children":"((?:[^"\\]|\\.)*)"')
# A card's single badge: an svg icon path (clock = employment type,
# globe = location tag) followed by its text.
_BADGE_RE = re.compile(r'"d":"(M[^"]*)"}]}]," ","((?:[^"\\]|\\.)*)"')
# The sidebar value after the "Published on" label — inline on new
# pages, on its own flight row on old ones.
_PUBLISHED_RE = re.compile(
	r'"Published on"\}.{0,400}?"className":"text-sm mb-4","children":"((?:[^"\\]|\\.)*)"',
	re.DOTALL,
)
_LOCATION_ICON = 'M2 12h20'  # globe; any other icon is the clock (job type)


def _flight_rows(html: str) -> str:
	"""Concatenate the flight payloads from a page's __next_f pushes."""
	rows = ''
	for match in _PUSH_RE.finditer(html):
		try:
			rows += json.loads(match.group(1))[1]
		except (ValueError, IndexError):
			pass  # a push we cannot decode carries no cards we need
	return rows


def _cards(rows: str) -> list[tuple[str, str, list[str]]]:
	"""Return (path, title, location tags) for every card in flight rows.

	An empty badge location means the job states no location
	restriction; we tag it ANYWHERE.
	"""
	found = []
	matches = list(_CARD_RE.finditer(rows))
	for position, match in enumerate(matches):
		end = matches[position + 1].start() if position + 1 < len(matches) else len(rows)
		segment = rows[match.end() : end]
		title = _TITLE_RE.search(segment)
		if not title:
			continue
		tags = [ANYWHERE]
		badge = _BADGE_RE.search(segment)
		if badge and badge.group(1).startswith(_LOCATION_ICON):
			tags = [badge.group(2)]
		found.append((match.group(1), title.group(1), tags))
	return found


def _select(
	cards: Sequence[tuple[str, str, list[str]]],
	locations: Sequence[str] | None,
	limit: int,
) -> list[tuple[str, str, list[str]]]:
	"""The cards open to `locations`, capped at `limit` (the detail budget)."""
	kept = []
	for card in cards:
		if locations and not set(locations) & set(card[2]):
			continue
		kept.append(card)
	return kept[:limit]


def _published(rows: str) -> str | None:
	"""A detail page's "Published on" as an ISO date, or None.

	The board's dates are date-only, so the ISO string carries no real
	time. A missing or unparseable date comes back as None — such a job
	sorts last rather than being dropped.
	"""
	match = _PUBLISHED_RE.search(rows)
	if not match:
		return None
	try:
		return dates.iso(match.group(1))
	except ValueError:
		return None


def recent_jobs(
	category: str | None = FAMILIES['software']['category'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent BruntWork jobs in a category, newest first.

	category: a BruntWork category name with the board's own spelling
	(default: the software family's Website and Software Development);
	None lists the whole board.
	locations: keep only jobs whose location tags include one of these;
	jobs with no location restriction count as ANYWHERE ("Worldwide");
	None applies no location filter. The pipeline computes the tags from
	the user's config with `geo.board_tags`.
	limit: cap on the number of jobs returned. Detail pages are fetched
	only for the jobs returned, so a smaller limit means fewer requests.
	"""
	return parse(fetch(category, locations, limit), locations=locations, limit=limit)


def fetch(
	category: str | None = FAMILIES['software']['category'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> dict:
	"""Fetch the listing and the detail page of every job that survives.

	Returns `{'listing': html, 'details': {path: html}}`. The selection
	runs here too, purely so the run does not pay for detail requests
	the location filter or the limit would discard.
	"""
	if category:
		path = f'/search?category={urllib.parse.quote(category)}'
	else:
		path = '/search'  # the whole board, same page, no pagination
	listing = http.get_text(f'{BASE_URL}{path}')
	kept = _select(_cards(_flight_rows(listing)), locations, limit)
	details = {job_path: http.get_text(f'{BASE_URL}{job_path}') for job_path, _, _ in kept}
	return {'listing': listing, 'details': details}


def parse(raw: dict, *, locations: Sequence[str] | None = None, limit: int = 50) -> list[dict]:
	"""Turn the listing and its detail pages into job dicts, newest first.

	Pure: missing detail pages or dates become `published=None`, which
	the contract allows.
	"""
	details = raw.get('details') or {}
	kept = _select(_cards(_flight_rows(raw.get('listing') or '')), locations, limit)
	jobs = [
		{
			'title': title,
			'company': 'BruntWork',
			'url': f'{BASE_URL}{job_path}',
			'published': _published(_flight_rows(details.get(job_path) or '')),
			'locations': tags,
			'salary': None,
		}
		for job_path, title, tags in kept
	]
	# Board order is id-descending, which is publication order; the
	# stable sort by the fetched dates keeps same-day ties that way.
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs
