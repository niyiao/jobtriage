"""Demo Jobs — the worked plugin example for docs/scrapers.md.

This is a complete board, not a sketch: the test suite copies it into a
temporary op folder's `scrapers/`, lists it with `jobtriage sources`,
runs `jobtriage sources test demo` and scrapes it, all through the CLI.
Its host is fake, so the suite substitutes `jobtriage.plugin.http.get_json`
and no request leaves the machine.

Read it beside `_example.py`, the template `jobtriage sources new NAME`
writes. The template spells out every optional hook; this file is the
same contract written as one board would write it, with the choices
made and the reasons beside them. Every declaration below is optional
except `NAME` and `recent_jobs`.
"""

from jobtriage.plugin import dates, geo, http

# The id the registry, `-s` and jobs.source use. Lowercase, digits and
# hyphens; unique across every board the registry finds.
NAME = 'demo'

# Short names `-s` also accepts. Not stored; the canonical NAME is.
ALIASES = ('dm',)

TITLE = 'Demo Jobs'
HOMEPAGE = 'https://demo-jobs.test'

# The contract this file targets. Omit it (or write 1) unless you are
# deliberately writing against a newer `jobtriage.plugin`.
API_VERSION = 1

# Shown by `jobtriage sources info`. A real board's terms belong here:
# a link back, a credit, a request rate. It is the reason to keep
# POLITE_DELAY honest.
TERMS = 'A demonstration board: no terms, and no real requests.'

# Seconds between requests, enforced by `jobtriage.plugin.http` across
# the whole `recent_jobs` call — the plugin never sleeps itself.
POLITE_DELAY = 1.0


# --- The location vocabulary ----------------------------------------------
#
# `geo.board_tags` turns the user's `[search].locations` into the tags
# this board accepts, using these declarations. A board whose API returns
# every region may skip them all and filter centrally instead; declaring
# them is what makes `limit` mean "the newest matching jobs".

# Tags meaning "open to anywhere", in the board's own wording.
WORLDWIDE = ('Worldwide', 'Anywhere')

# Canonical region token -> the tags this board uses for it.
REGIONS = {'emea': ('Europe', 'EMEA'), 'apac': ('Asia Pacific',)}

# 'name' ("Germany"), 'iso2' ("DE") or 'none' (no per-country tag).
COUNTRY_STYLE = 'iso2'

# The board's exact wording per ISO code. Every country it lacks maps to
# '' and is suppressed, so the filter stays exact instead of inventing a
# tag the board would not recognise.
_WORDING = {'DE': 'DE', 'GB': 'UK', 'NL': 'NL'}
COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}

# The one region a single-region board serves; empty means everywhere.
COVERS = ()

# The board's category parameter per job family. The pipeline passes the
# entry for the requested family to `recent_jobs`, so the family is
# filtered server-side. `{}` means the board has no category parameter.
FAMILIES = {'software': {'category': 'engineering'}}


# --- The request ----------------------------------------------------------

API_URL = 'https://api.demo-jobs.test/v1/jobs'


def fetch(limit: int, *, category: str = 'engineering', locations=None) -> dict:
	"""One request: the newest page, as the API returns it.

	Split from `parse` on purpose: this is the only part that needs the
	network, so a test can hand `parse` a recorded response and cover
	the parsing offline. Replace the URL and params here; everything
	below stays pure.

	The location tags go to the API because this board filters them
	server-side. A board that cannot filter server-side leaves them out
	and lets `parse` do it, the way the template does.
	"""
	params = {'limit': limit, 'category': category}
	if locations:
		params['region'] = ','.join(locations)
	return http.get_json(API_URL, params=params)


def parse(raw: dict, *, locations=None, limit: int = 50) -> list[dict]:
	"""Turn the API's answer into contract-shaped jobs, newest first.

	Pure: no network, so a fixture can drive it. A row the board's data
	cannot fill honestly is still emitted — empty url, empty title — and
	the registry drops and counts it; one bad record must not sink the
	whole page.
	"""
	jobs = []
	for entry in raw.get('jobs', []):
		# A missing tag means open anywhere, the board's own shorthand.
		tags = list(entry.get('locations') or (WORLDWIDE[0],))
		# Defensive: the API was asked for these tags, but matching them
		# again means a server that ignores the param cannot smuggle a
		# wrong-region job into a filtered run.
		if locations and not set(locations) & set(tags):
			continue

		published = entry.get('published_at')
		salary = entry.get('salary')
		jobs.append(
			{
				'title': entry.get('title', ''),
				'company': entry.get('company', ''),
				'url': entry.get('url') or '',
				'published': dates.iso(published) if published else None,
				'locations': tags,
				'salary': str(salary) if salary is not None else None,
			}
		)

	# "Newest first" is part of the contract; sort by the board's true
	# posting time when the API's order is really "featured first".
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]


def recent_jobs(
	*, locations=None, limit: int = 50, category: str = 'engineering', **options
) -> list[dict]:
	"""The only required function: at most `limit` jobs, newest first.

	`locations` is the tuple `geo.board_tags` computed for this board,
	or None for `--all`. `category` is the family parameter the pipeline
	read from FAMILIES. `**options` receives anything else in
	`[sources.demo]`, so a knob added here is configurable without a
	release; `jobtriage sources info demo` prints this signature.
	"""
	return parse(
		fetch(limit, category=category, locations=locations), locations=locations, limit=limit
	)


class _Declarations:
	"""This module's declarations, as `geo.board_tags` reads them.

	`server_locations` receives the user's preferences, not the module,
	and `__name__` is only a string; this is the smallest object with
	the four attributes the helper looks for.
	"""

	WORLDWIDE = WORLDWIDE
	REGIONS = REGIONS
	COUNTRY_STYLE = COUNTRY_STYLE
	COUNTRY_ALIASES = COUNTRY_ALIASES


def server_locations(prefs) -> dict:
	"""The API's location params, in the board's own vocabulary.

	Declarative today: the pipeline passes the same tags to
	`recent_jobs` as `locations`, and `fetch` builds the query from
	them. This hook is what `sources info` reports and what keeps the
	server-side story written down beside `fetch`; a board that changes
	one should change the other.
	"""
	tags = geo.board_tags(_Declarations, prefs)
	return {'region': ','.join(tags)} if tags else {}
