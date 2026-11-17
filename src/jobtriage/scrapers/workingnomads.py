"""Working Nomads job scraper.

The job board at workingnomads.com is an AngularJS app that loads its
listings from an Elasticsearch endpoint (POST /jobsapi/_search). We call
that endpoint directly with the same filters the page uses:

    category=development   ->  terms on "category_name.raw" = "Development"
    positionType=full-time ->  terms on "position_type"      = "ft"
"""

from collections.abc import Sequence

from jobtriage.plugin import http

NAME = 'workingnomads'
ALIASES = ('wn',)
TITLE = 'Working Nomads'
HOMEPAGE = 'https://www.workingnomads.com'
# One POST per run; nothing here is worth a pause.
POLITE_DELAY = 0

SEARCH_URL = 'https://www.workingnomads.com/jobsapi/_search'
FIELDS = ['title', 'company', 'slug', 'pub_date', 'locations', 'salary_range_short']

# The board's own location vocabulary, translated by geo.board_tags.
# ("Global" isn't a Working Nomads tag — the site uses "Anywhere".) There
# are no country tags at all: the site filters with its flat "EMEA" tag,
# so a user who asks for Nigeria is covered by EMEA here, not by a
# "Nigeria" tag the board does not have.
WORLDWIDE = ('Anywhere',)
REGIONS = {'emea': ('EMEA',)}
COUNTRY_STYLE = 'none'

# The board has no per-family taxonomy beyond a category, and the default
# request has always meant the developer category, full-time only. Both
# belong here so the pipeline (plan 03 step 4) can pass them explicitly
# and `recent_jobs` has one place to read its defaults from.
FAMILIES = {'software': {'category': 'Development', 'position_type': 'ft'}}


def recent_jobs(
	category: str = FAMILIES['software']['category'],
	position_type: str = FAMILIES['software']['position_type'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent jobs on the Working Nomads board, newest first.

	locations: keep only jobs open to at least one of these location tags,
	e.g. ("Anywhere", "EMEA"); None applies no location filter. The
	pipeline computes the tags from the user's config with
	`geo.board_tags`; the default here is unfiltered because a caller that
	has no prefs has not asked to narrow.
	"""
	return parse(fetch(category, position_type, locations, limit), limit=limit)


def fetch(
	category: str = FAMILIES['software']['category'],
	position_type: str = FAMILIES['software']['position_type'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> dict:
	"""POST the same Elasticsearch query the site's own page sends.

	The location filter is a server-side `terms` clause: the response
	only ever holds matching hits, which is why `parse` does no
	location work of its own.
	"""
	filters = [
		{'terms': {'category_name.raw': [category]}},
		{'terms': {'position_type': [position_type]}},
	]
	if locations:
		filters.append({'terms': {'locations': list(locations)}})

	query = {
		'from': 0,
		'size': limit,
		'sort': [{'pub_date': {'order': 'desc'}}],
		'query': {'bool': {'filter': filters}},
		'_source': FIELDS,
	}
	return http.post_json(SEARCH_URL, query)


def parse(raw: dict, *, locations: Sequence[str] | None = None, limit: int = 50) -> list[dict]:
	"""Turn an Elasticsearch response into job dicts, newest first.

	Pure: `fetch` already applied `locations` in the query, so the
	parameter is accepted for the uniform parse signature and ignored.
	"""
	jobs = [
		{
			'title': source['title'],
			'company': source['company'],
			'url': f'https://www.workingnomads.com/jobs/{source["slug"]}',
			'published': source['pub_date'],
			'locations': source.get('locations', []),
			'salary': source.get('salary_range_short') or None,
		}
		for source in (hit['_source'] for hit in raw['hits']['hits'])
	]
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]
