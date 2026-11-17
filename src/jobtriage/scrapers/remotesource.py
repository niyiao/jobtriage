"""Remote Source job scraper.

The job board at remotesource.com is a Next.js app that loads listings
from a JSON API (GET /api/jobs). The API is public — no auth required,
the "free account" wall is purely UI-side.

The page's URL parameters map directly to API query params:

    jobCategory=Engineering & Development
    remoteFirstOnly=true
    sortBy=relevance                     (newest first, no sponsored pins)

The API has no server-side location filter, so location eligibility is
filtered client-side from each job's eligibilityScopes ("WORLDWIDE" is
the site's anywhere/global tag).
"""

from collections.abc import Sequence

from jobtriage.plugin import http

NAME = 'remotesource'
ALIASES = ('rs',)
TITLE = 'Remote Source'
HOMEPAGE = 'https://www.remotesource.com'
# One request per run.
POLITE_DELAY = 0

API_URL = 'https://www.remotesource.com/api/jobs'
BASE_URL = 'https://www.remotesource.com/jobs'

# eligibilityScopes values, translated by geo.board_tags. This board
# filters client-side (the API has no location param), so the tags are
# plain scopes and there is no country style: "EMEA" is the board's only
# regional scope, and a country-level preference lands on it.
WORLDWIDE = ('WORLDWIDE',)
REGIONS = {'emea': ('EMEA',)}
COUNTRY_STYLE = 'none'

# The default request's category and the remote-first restriction, in one
# place: `recent_jobs` reads its defaults from here and plan 03 step 4
# passes the same values per family.
FAMILIES = {
	'software': {
		'job_category': 'Engineering & Development',
		'remote_first_only': True,
	},
}


def _salary(job: dict) -> str | None:
	lo, hi = job.get('salaryMinUsd'), job.get('salaryMaxUsd')
	if not lo and not hi:
		return None
	if lo and hi and lo != hi:
		return f'${lo:,} - ${hi:,}'
	return f'${(lo or hi):,}'


def recent_jobs(
	job_category: str = FAMILIES['software']['job_category'],
	remote_first_only: bool = FAMILIES['software']['remote_first_only'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent jobs on Remote Source, newest first.

	locations: keep only jobs whose eligibilityScopes include one of
	these values, e.g. ("WORLDWIDE", "EMEA"); None applies no location
	filter. The pipeline computes the tags from the user's config with
	`geo.board_tags`.
	"""
	return parse(
		fetch(job_category, remote_first_only, locations, limit), locations=locations, limit=limit
	)


def fetch(
	job_category: str = FAMILIES['software']['job_category'],
	remote_first_only: bool = FAMILIES['software']['remote_first_only'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> dict:
	"""GET the listing JSON; ask for the whole category when filtering here.

	The API has no location parameter, so a request that will be
	filtered by `parse` must carry the whole category rather than the
	user's limit: otherwise the newest *matching* jobs are not in hand.
	"""
	params = {
		'jobCategory': job_category,
		'remoteFirstOnly': str(remote_first_only).lower(),
		'sortBy': 'relevance',
		'limit': limit if locations is None else 10_000,
		'offset': 0,
	}
	return http.get_json(API_URL, params=params)


def parse(raw: dict, *, locations: Sequence[str] | None = None, limit: int = 50) -> list[dict]:
	"""Turn an API payload into job dicts, newest first.

	Pure: location eligibility is a client-side scope match because the
	API exposes no filter for it.
	"""
	jobs = []
	for job in raw['jobs']:
		scopes = job.get('eligibilityScopes') or []
		if locations and not set(locations) & set(scopes):
			continue
		jobs.append(
			{
				'title': job['title'],
				'company': job['company']['name'],
				'url': f'{BASE_URL}/{job["uuid"]}-{job["slug"]}',
				'published': job['postedAt'],
				'locations': scopes,
				'salary': _salary(job),
			}
		)
		if len(jobs) == limit:
			break
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]
