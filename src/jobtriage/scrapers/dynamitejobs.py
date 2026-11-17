"""Dynamite Jobs scraper.

dynamitejobs.com is a Nuxt/Vue app backed by a public Firebase
Firestore database (project `djplatform`). The category pages embed only
the first 20 jobs server-side (in `window.__PINIA_STATE__`), but the
whole category is one request away on the Firestore REST API the site
itself uses:

    POST .../v1/projects/djplatform/databases/(default)/documents:runQuery

with the same structuredQuery the client sends (collection `jobs`,
status == "published", categoriesSlugs array-contains "development").
The API is public — no auth, the key below is the public Firebase web
key from the site's JS bundle — so when location-filtering we fetch the
entire category and filter client-side, like the other scrapers.

There is no server-side way to sort by date only: the query's orderBy
must match the site's own (featured-boosted) order or Firestore rejects
it for a missing composite index. We therefore re-sort by publish date
client-side to return the genuinely newest jobs.
"""

import json
from collections.abc import Sequence

from jobtriage import geo
from jobtriage.plugin import http

NAME = 'dynamitejobs'
ALIASES = ('dj',)
TITLE = 'Dynamite Jobs'
HOMEPAGE = 'https://dynamitejobs.com'
# One query per run against a public Firebase endpoint.
POLITE_DELAY = 0

API_URL = (
	'https://firestore.googleapis.com/v1/projects/djplatform'
	'/databases/(default)/documents:runQuery'
	'?key=AIzaSyBovrhxWNthu01l3-xiZ_UW_JCyg4rtFQc'
)
BASE_URL = 'https://dynamitejobs.com'

# locationSlugs values as the board words them, translated by
# geo.board_tags. ("EMEA" isn't a Dynamite Jobs tag — jobs open across
# the region carry the site's region slugs: "europe", "western-asia"
# (the Middle East), "africa", and variants.) Each slug sits under its
# true area, so a containment match asks for a slug only when it covers
# the whole requested region or country. The board has no country
# vocabulary beyond one quirk: the single country slug the old filter
# carried is Nigeria's ISO code, so COUNTRY_STYLE='iso2' and every other
# EMEA code is suppressed by the empty alias. A solo ['NG'] preference
# therefore yields the worldwide slug, "africa" and "NG".
WORLDWIDE = ('worldwide',)
REGIONS = {
	'europe': ('europe',),
	'eu': ('european-union',),
	'eastern-europe': ('eastern-europe',),
	'middle-east': ('western-asia',),
	'africa': ('africa',),
}
COUNTRY_STYLE = 'iso2'
COUNTRY_ALIASES = {**dict.fromkeys(geo.REGIONS['emea'], ''), 'NG': 'NG'}

# The board's categoriesSlugs are functions; development is the one the
# default scrape has always read. Both the query template and the
# per-call override read it from here so the two cannot drift.
FAMILIES = {'software': {'job_category': 'development'}}

# The site's own query, verbatim — see module docstring about orderBy.
_QUERY = {
	'structuredQuery': {
		'from': [{'collectionId': 'jobs'}],
		'where': {
			'compositeFilter': {
				'op': 'AND',
				'filters': [
					{
						'fieldFilter': {
							'field': {'fieldPath': 'status'},
							'op': 'EQUAL',
							'value': {'stringValue': 'published'},
						}
					},
					{
						'fieldFilter': {
							'field': {'fieldPath': 'categoriesSlugs'},
							'op': 'ARRAY_CONTAINS',
							'value': {'stringValue': FAMILIES['software']['job_category']},
						}
					},
				],
			}
		},
		'orderBy': [
			{'field': {'fieldPath': 'sortOrderPriority'}, 'direction': 'DESCENDING'},
			{'field': {'fieldPath': 'sortDateAt'}, 'direction': 'DESCENDING'},
			{'field': {'fieldPath': '__name__'}, 'direction': 'DESCENDING'},
		],
	}
}


def _field(doc: dict, name: str) -> dict:
	return doc.get(name, {}).get('mapValue', {}).get('fields', {})


def _text(doc: dict, name: str) -> str | None:
	return doc.get(name, {}).get('stringValue')


def _number(doc: dict, name: str) -> int | float | None:
	value = doc.get(name, {}).get('integerValue') or doc.get(name, {}).get('doubleValue')
	return float(value) if value else None


def _strings(doc: dict, name: str) -> list[str]:
	values = doc.get(name, {}).get('arrayValue', {}).get('values') or []
	return [v['stringValue'] for v in values if 'stringValue' in v]


def _salary(job: dict) -> str | None:
	salary = _field(job, 'salary')
	if not salary.get('public', {}).get('booleanValue'):
		return None
	lo, hi = _number(salary, 'from'), _number(salary, 'to')
	if not lo and not hi:
		return None
	period = {
		'monthly': '/mo',
		'yearly': '/yr',
		'hourly': '/hr',
	}.get(salary.get('type', {}).get('stringValue'), '')
	if lo and hi and lo != hi:
		return f'${lo:,.0f} - ${hi:,.0f}{period}'
	return f'${(lo or hi):,.0f}{period}'


def recent_jobs(
	job_category: str = FAMILIES['software']['job_category'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent jobs on Dynamite Jobs, newest first.

	job_category: a Dynamite Jobs category slug (default: the software
	family's development category).
	locations: keep only jobs whose locationSlugs include one of these
	values, e.g. ("worldwide", "europe", ...); None applies no location
	filter. The pipeline computes the tags from the user's config with
	`geo.board_tags`.
	"""
	return parse(fetch(job_category, locations, limit), locations=locations, limit=limit)


def fetch(
	job_category: str = FAMILIES['software']['job_category'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Run the site's Firestore query and return its raw document rows.

	The query plan silently drops a `limit` when it is needed to see
	the whole category before the client-side location filter runs;
	that is why a filtered request asks for 500 documents.
	"""
	query = json.loads(json.dumps(_QUERY))
	if job_category != FAMILIES['software']['job_category']:
		query['structuredQuery']['where']['compositeFilter']['filters'][1]['fieldFilter']['value'][
			'stringValue'
		] = job_category
	query['structuredQuery']['limit'] = limit if locations is None else 500
	return http.post_json(API_URL, query)


def parse(
	raw: list[dict], *, locations: Sequence[str] | None = None, limit: int = 50
) -> list[dict]:
	"""Turn Firestore documents into job dicts, newest first.

	Pure: `locations` is matched against each document's locationSlugs,
	because Firestore's query has no location clause.
	"""
	jobs = []
	for entry in raw:
		if 'error' in entry:
			raise RuntimeError(f'Firestore query failed: {entry["error"]["message"]}')
		fields = entry['document']['fields']
		scopes = _strings(fields, 'locationSlugs')
		if locations and not set(locations) & set(scopes):
			continue
		company = _field(fields, 'company')
		jobs.append(
			{
				'title': _text(fields, 'title'),
				'company': company.get('name', {}).get('stringValue'),
				'url': '{}/company/{}/remote-job/{}'.format(
					BASE_URL,
					company.get('username', {}).get('stringValue'),
					_text(fields, 'slug'),
				),
				'published': _text(fields, 'publishedAt')
				or fields.get('sortDateAt', {}).get('timestampValue'),
				'locations': scopes,
				'salary': _salary(fields),
			}
		)
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]
