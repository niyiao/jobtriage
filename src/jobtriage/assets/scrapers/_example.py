"""Example Board — the template `jobtriage sources new NAME` starts from.

This is a working plugin for a made-up JSON-API board, and the map for
writing a real one. The command copies it to `<op>/scrapers/NAME.py`
with `NAME` filled in; replace the board's particulars (`API_URL`, the
request params and `parse()`) and it is a board.

Before code, research the board. These are the questions the built-ins'
notes in docs/scrapers.md answer one by one, and the comments below say
where each answer lands in this file:

1. Where is the data? `curl` the listing page first. A public JSON API
   is the easiest board to keep alive; SSR state that ships the first
   page (`__NEXT_DATA__`, `__NUXT__`, `__PINIA_STATE__`) is the next
   best, and `jobtriage.plugin.parse` reads those. Only then reach for
   the browser (docs/scrapers.md, "Research playbook").
2. How does it paginate? A `limit`/`offset` pair, a cursor, a page
   number, or — the nicest — everything in one request. A page size
   that silently caps at 25 is common: check the count that comes back.
3. How does it sort? "Newest first" is often a lie; sponsored or
   featured rows ride on top. Find the true posting timestamp and
   re-sort by it, because the contract asks for newest first.
4. What is the location vocabulary? Every board words "open worldwide
   / EMEA" differently. Decide whether the API filters location
   server-side (`server_locations` below) or whether each job carries
   tags matched client-side (`parse` below).
5. How is the date written? ISO, "2 days ago", "10 Nov" with no year —
   `jobtriage.plugin.dates` converts all three. `published` must be
   ISO 8601 or None; the registry drops a row it cannot parse, and
   `jobtriage sources test` names the row and the fix.
6. What are the terms? A link back, a credit, a request rate. Write it
   in `TERMS`; `jobtriage sources info` shows it, and it is the reason
   to keep `POLITE_DELAY` honest.

A plugin imports `jobtriage.plugin` and nothing else from this project.
This file loads as shipped — the registry skips files whose name
starts with `_` — but its `recent_jobs()` fetches a placeholder host
that never resolves. `jobtriage sources test NAME` fails with an
HttpError until `API_URL` points at the board. That is expected: the
first real request is the next thing to run.
"""

from jobtriage.plugin import dates, geo, http

# The id the board is addressed by: `-s NAME`, `jobs.source` in the
# database, `[sources.NAME]` in jobtriage.toml. Lowercase, digits and
# hyphens only, and unique across every board the registry finds.
NAME = 'example-board'

# Extra short names `-s` accepts. They must be unique too, and are not
# stored in jobs.source (the canonical NAME is).
ALIASES = ('ex',)

# What listings and `sources info` print. Defaults to the name, title-cased.
TITLE = 'Example Board'

# Where a person can see the board itself.
HOMEPAGE = 'https://example-board.test'

# The plugin contract this file targets. Leave it out (= 1) unless you
# are deliberately writing against a newer `jobtriage.plugin`.
API_VERSION = 1

# What the board's terms ask of a scraper: a link back, a credit, a
# request rate. Shown by `sources info`.
TERMS = 'Link back to the canonical job URL.'

# Seconds between requests, enforced by `jobtriage.plugin.http` for the
# whole `recent_jobs` call — a plugin never sleeps itself. One request
# per run wants 0; a page loop wants a second or two (remotive
# rate-limits hard; wellfound caps its pages).
POLITE_DELAY = 1.0


# --- The location vocabulary ----------------------------------------------
#
# The pipeline turns the user's `[search].locations` into the tags this
# module accepts with `geo.board_tags(...)`, using exactly these
# declarations. Skip all of them when the API returns every region and
# `parse()` filters client-side with the board's own job tags instead:
# then the pipeline passes `locations=None` and the central relevance
# filter narrows. Keep them when the board has a finite vocabulary
# worth matching exactly.

# Tags meaning "open to anywhere", in the board's wording, verbatim.
WORLDWIDE = ('Worldwide', 'Anywhere')

# Canonical region token -> the tags the board uses for that region.
# geo.REGIONS has the tokens ('emea', 'europe', 'eu', 'apac', 'latam',
# ...). The key is the tag's TRUE area, smallest canonical region that
# covers it: "Europe" goes under 'europe', never under 'emea'. The
# containment match asks for a tag only when its area covers the whole
# requested region or country, so a wider key would over-claim.
REGIONS = {'emea': ('EMEA',), 'europe': ('Europe',), 'apac': ('Asia Pacific',)}

# How the board writes a country: 'name' ("Germany"), 'iso2' ("DE") or
# 'none' (it has no per-country tag — leave COUNTRY_ALIASES out then).
COUNTRY_STYLE = 'iso2'

# ISO code -> the board's exact wording, a string or a tuple of strings.
# Countries this board carries but words its own way go here; every
# country it lacks maps to '' and is suppressed, so the filter stays
# exact instead of inventing a tag. Use a tuple when the board spells
# one country two ways: {'CZ': ('Czechia', 'Czech Republic')}.
_WORDING = {'DE': 'DE', 'GB': 'UK', 'NL': 'NL'}
COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}

# The region a single-region board serves ('europe', 'apac', ...); empty
# means it covers everywhere, the usual case. A user whose locations do
# not overlap COVERS skips the board without a request.
COVERS = ()

# The board's category parameters per job family. The pipeline picks the
# entries for `[search].families` and passes them to `recent_jobs`, so
# the requested family is filtered server-side. `{}` means the board has
# no category parameter and serves every family.
FAMILIES = {'software': {'category': 'engineering'}}


# --- The request -----------------------------------------------------------
#
# Placeholder: a `.test` host never resolves, which is what makes
# `sources test` fail until you replace it with the board's real API.
API_URL = 'https://api.example-board.test/v1/jobs'


def fetch(limit: int) -> dict:
	"""One request: the newest page of jobs, as the API returns it.

	Split from `parse` on purpose. This is the only part that needs the
	network, so a test can feed a recorded response to `parse` and cover
	the parsing offline (plan 04 step 5 does that for every built-in).
	Replace the URL, the params and the return shape here; everything
	below stays pure.

	`params` is a dict, and a list value repeats the key — the shape
	WordPress-style filters want. A browser User-Agent is the default,
	because several boards 403 urllib's.
	"""
	return http.get_json(API_URL, params={'limit': limit})


def parse(raw: dict, *, locations=None, limit: int = 50) -> list[dict]:
	"""Turn the API's answer into contract-shaped job dicts, newest first.

	Pure: no network, so an offline fixture can drive it. Keep it honest
	about the contract — the registry drops a bad row and `sources test`
	names the field, but a working board should not need the warning.

	`locations` is the board's own tags (or None for `--all`): match
	them against each job's tags when the API does not filter. `limit`
	is the per-board number from the config.
	"""
	jobs = []
	for entry in raw.get('jobs', []):
		# Research question 4: the job's own location tags. A board that
		# omits them is open anywhere; a board that lists one country per
		# job needs the vocabulary above.
		tags = [entry['location']] if entry.get('location') else [WORLDWIDE[0]]
		if locations and not set(locations) & set(tags):
			continue

		# Research question 5: `dates.iso` takes ISO strings, unix
		# timestamps, most human formats and RFC 822; None is allowed
		# when the board truly has no date. `dates.relative` ("3 days
		# ago") and `dates.yearless` ("10 Nov") cover the other two
		# formats the boards here use.
		published = entry.get('published_at')
		jobs.append(
			{
				'title': entry['title'],
				'company': entry['company'],
				'url': entry['url'],
				'published': dates.iso(published) if published else None,
				'locations': tags,
				'salary': entry.get('salary'),
			}
		)
	# Research question 3: re-sort here when the API's "newest" is really
	# "featured first" — newest first is part of the contract.
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]


def recent_jobs(*, locations=None, limit: int = 50, **options) -> list[dict]:
	"""The only required function: at most `limit` jobs, newest first.

	`locations` is the tags `geo.board_tags` computed, or None (no
	board-side filter — `--all`). `**options` receives anything the user
	put in `[sources.NAME]`, so a knob added here is configurable
	without a release; `sources info` prints this signature.
	"""
	return parse(fetch(limit), locations=locations, limit=limit)


class _Declarations:
	"""This module's location declarations, as `geo.board_tags` reads them.

	A board passes itself there, but `__name__` is only a string; this
	class is the smallest object with the four attributes the helper
	looks for. Copy it verbatim when you implement `server_locations`.
	"""

	WORLDWIDE = WORLDWIDE
	REGIONS = REGIONS
	COUNTRY_STYLE = COUNTRY_STYLE
	COUNTRY_ALIASES = COUNTRY_ALIASES


def server_locations(prefs) -> dict:
	"""The API's location params, when the board filters server-side.

	Optional and declarative: this example's API takes no location
	parameter — it returns every region and `parse()` matches
	client-side — so it returns nothing. A board whose API does filter
	keeps the same mapping here, beside `fetch()`:

	    return {'regions': ','.join(geo.board_tags(_Declarations, prefs))}

	The pipeline passes `geo.board_tags(...)` to `recent_jobs` as
	`locations`; it does not call this hook, so `fetch()` must build its
	request from that tuple itself. The hook is what `sources info`
	reports and what a reviewer reads to know the board filters
	server-side.

	`prefs` is a `jobtriage.plugin.LocationPrefs`: its `.locations` is
	the user's canonical tokens.
	"""
	return {}
