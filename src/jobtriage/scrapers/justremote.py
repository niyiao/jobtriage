"""JustRemote job scraper.

justremote.co is a React SPA whose listing pages embed the job list in
`window.__PRELOADED_STATE__`, but the same data is one plain GET away on
the API the client itself uses (base URL hardcoded in the JS bundle):

    https://justremote-api.herokuapp.com/api/v1/jobs

The API is public — no auth, no cookies — and has no pagination or
filters: it returns every active job (about a month's worth, newest
first) and the client filters by category/location itself. We do the
same in one request.

There is no salary data in the listing API, and dates are only
"10 Nov" — no year — so we infer the year (wrapping back if that would
be in the future, since the board only keeps ~30 days of jobs) and
return ISO dates for the shared printer.

Location vocabulary: each job carries `location_restrictions`, a list
of country names. An empty list means no restrictions — open anywhere.
"""

from collections.abc import Sequence

from jobtriage import geo
from jobtriage.plugin import dates, http

NAME = 'justremote'
ALIASES = ('jr',)
TITLE = 'JustRemote'
HOMEPAGE = 'https://justremote.co'
# One request returns every active job.
POLITE_DELAY = 0

API_URL = 'https://justremote-api.herokuapp.com/api/v1/jobs'
BASE_URL = 'https://justremote.co'

# Pseudo-tag for jobs with an empty location_restrictions list; per job
# the module replaces an empty list with it, so normalize reads "open
# anywhere" as the worldwide marker.
ANYWHERE = 'Anywhere'

# location_restrictions country names as the board words them,
# translated by geo.board_tags. The board carries 26 of the 129 EMEA
# countries and no region tags at all, so REGIONS stays empty and every
# country outside `_WORDING` is suppressed — no region tag is invented
# either. "United Kingdom" is the display name already; the other two
# wordings differ.
_WORDING = {
	'AE': 'United Arab Emirates',
	'AT': 'Austria',
	'BE': 'Belgium',
	'CH': 'Switzerland',
	'CZ': 'Czech Republic',
	'DE': 'Germany',
	'DK': 'Denmark',
	'EG': 'Egypt',
	'ES': 'Spain',
	'FI': 'Finland',
	'FR': 'France',
	'GB': 'United Kingdom',
	'GH': 'Ghana',
	'IE': 'Ireland',
	'IL': 'Israel',
	'IT': 'Italy',
	'KE': 'Kenya',
	'NG': 'Nigeria',
	'NL': 'Netherlands',
	'NO': 'Norway',
	'PL': 'Poland',
	'PT': 'Portugal',
	'SA': 'Saudi Arabia',
	'SE': 'Sweden',
	'TR': 'Turkey',
	'ZA': 'South Africa',
}

WORLDWIDE = (ANYWHERE,)
REGIONS = {}  # the board has no region-level tag
COUNTRY_STYLE = 'name'
COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}

# JustRemote's categories are functions; developer is the one the default
# scrape has always read.
FAMILIES = {'software': {'job_category': 'developer'}}


def recent_jobs(
	job_category: str = FAMILIES['software']['job_category'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent jobs on JustRemote, newest first.

	job_category: a JustRemote category slug (default: the software
	family's developer category).
	locations: keep only jobs whose location_restrictions include one
	of these country names — or that have no restrictions at all
	(ANYWHERE); None applies no location filter. The pipeline computes
	the tags from the user's config with `geo.board_tags`.
	"""
	return parse(
		fetch(),
		job_category=job_category,
		locations=locations,
		limit=limit,
	)


def fetch() -> list[dict]:
	"""GET the whole active-job list; the API has no filters to send."""
	return http.get_json(API_URL)


def parse(
	raw: list[dict],
	*,
	job_category: str = FAMILIES['software']['job_category'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Turn the API's job list into job dicts, newest first.

	Pure: category and location are client-side filters here because
	the API exposes neither. Dates are year-less ("10 Nov"), so
	`dates.yearless` infers the posting year.
	"""
	matches = []
	for job in raw:
		if job_category and job.get('category') != job_category:
			continue
		scopes = job.get('location_restrictions') or [ANYWHERE]
		if locations and not set(locations) & set(scopes):
			continue
		matches.append(
			{
				'title': job['title'],
				'company': job['company_name'],
				'url': f'{BASE_URL}/{job["href"]}',
				'published': dates.yearless(job['date']),
				'locations': scopes,
				'salary': None,
			}
		)
	matches.sort(key=lambda job: job['published'] or '', reverse=True)
	return matches[:limit]
