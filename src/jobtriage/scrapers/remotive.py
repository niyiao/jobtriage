"""Remotive job scraper.

Remotive's free data is thin everywhere, and misleading at first glance.
The documented public API (https://remotive.com/api/remote-jobs) ignores
its own category/search/limit params and always returns the same ~13
mixed-category jobs; the per-category RSS feeds (/remote-jobs/feed/<slug>)
serve the same handful, delayed by 24 hours. The richest free source is
the listing page itself. It is server-rendered, and ships the Algolia
response for page one of the category inside `window.__INITIAL_SEARCH_RESULTS__`:
the day's fresh jobs plus the featured ones, with sponsored pins on top
and a few paywalled jobs at the end flagged `is_blurred`. Free users get
one page only — pagination is the paid tier — so one request is the
whole board.

The pinned order is not date order, so we re-sort by `discovered_on`
(the board's own publication time) client-side.

Remotive's terms ask listings to link back to their URLs; `url` below is
the canonical remotive.com one.

Location vocabulary: each hit carries `locations`, a list of tags from
the site's location filter — countries plus regions ("Europe",
"Eastern Europe") and "Worldwide" for open-anywhere jobs. Note the board
writes "UK", not "United Kingdom".
"""

from collections.abc import Sequence

from jobtriage import geo
from jobtriage.plugin import dates, http
from jobtriage.plugin import parse as parse_html

NAME = 'remotive'
ALIASES = ('rm',)
TITLE = 'Remotive'
HOMEPAGE = 'https://remotive.com'
# Their terms ask a listings site to link back to the canonical job URL.
TERMS = 'Link back to the canonical Remotive job URL.'
# The site rate-limits hard (connection resets after a burst of
# requests), so two requests in a run are already one too many.
POLITE_DELAY = 1.0

BASE_URL = 'https://remotive.com'

# location_filters tags as the board words them, translated by
# geo.board_tags. Remotive carries 56 of the 129 EMEA countries and no
# tag at all for the rest, so `_WORDING` lists what it has and every
# other EMEA code is suppressed by the empty alias below — no country is
# invented. Note the board writes "UK", not "United Kingdom", and
# "Czech Republic" and "Turkey", not their ISO display names.
_WORDING = {
	'AE': 'United Arab Emirates',
	'AL': 'Albania',
	'AM': 'Armenia',
	'AT': 'Austria',
	'BA': 'Bosnia and Herzegovina',
	'BE': 'Belgium',
	'BG': 'Bulgaria',
	'BH': 'Bahrain',
	'BY': 'Belarus',
	'CH': 'Switzerland',
	'CY': 'Cyprus',
	'CZ': 'Czech Republic',
	'DE': 'Germany',
	'DK': 'Denmark',
	'EE': 'Estonia',
	'EG': 'Egypt',
	'ES': 'Spain',
	'FI': 'Finland',
	'FR': 'France',
	'GB': 'UK',
	'GE': 'Georgia',
	'GH': 'Ghana',
	'GR': 'Greece',
	'HR': 'Croatia',
	'HU': 'Hungary',
	'IE': 'Ireland',
	'IL': 'Israel',
	'IS': 'Iceland',
	'IT': 'Italy',
	'JO': 'Jordan',
	'KE': 'Kenya',
	'KW': 'Kuwait',
	'LB': 'Lebanon',
	'LT': 'Lithuania',
	'LU': 'Luxembourg',
	'LV': 'Latvia',
	'MC': 'Monaco',
	'MD': 'Moldova',
	'ME': 'Montenegro',
	'MK': 'North Macedonia',
	'MT': 'Malta',
	'NG': 'Nigeria',
	'NL': 'Netherlands',
	'NO': 'Norway',
	'PL': 'Poland',
	'PT': 'Portugal',
	'QA': 'Qatar',
	'RO': 'Romania',
	'RS': 'Serbia',
	'SA': 'Saudi Arabia',
	'SE': 'Sweden',
	'SI': 'Slovenia',
	'SK': 'Slovakia',
	'TR': 'Turkey',
	'UA': 'Ukraine',
	'ZA': 'South Africa',
}

WORLDWIDE = ('Worldwide',)
# Each tag sits under its true area, so containment can read it:
# "Europe" covers a Europe preference; an EMEA preference does not ask
# for it, because a Europe-only job does not cover EMEA. The sub-region
# tags serve an explicit sub-region preference and the countries they
# contain. There is no EMEA-wide tag on this board, so an EMEA
# preference asks for the worldwide tag only.
REGIONS = {
	'europe': ('Europe',),
	'western-europe': ('Western Europe',),
	'eastern-europe': ('Eastern Europe',),
	'southern-europe': ('Southern Europe',),
	'northern-europe': ('Northern Europe',),
}
COUNTRY_STYLE = 'name'
COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}

# The board's categories are broad functions; software-development is the
# one the default scrape has always read.
FAMILIES = {'software': {'job_category': 'software-development'}}


def recent_jobs(
	job_category: str = FAMILIES['software']['job_category'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent jobs on Remotive, newest first.

	job_category: a Remotive category slug (default: the software
	family's software-development; devops and qa exist too).
	locations: keep only jobs whose locations include one of these tags,
	e.g. ("Worldwide", "Europe", "Germany"); None applies no location
	filter. The pipeline computes the tags from the user's config with
	`geo.board_tags`.
	"""
	return parse(fetch(job_category), locations=locations, limit=limit)


def fetch(job_category: str = FAMILIES['software']['job_category']) -> str:
	"""GET the category's server-rendered listing page."""
	return http.get_text(f'{BASE_URL}/remote-jobs/{job_category}')


def parse(raw: str, *, locations: Sequence[str] | None = None, limit: int = 50) -> list[dict]:
	"""Turn the listing page into job dicts, newest first.

	Pure: the page ships the Algolia response for page one inside
	`window.__INITIAL_SEARCH_RESULTS__`; `parse_html.json_script` finds
	it without guessing at the surrounding script syntax.
	"""
	state = parse_html.json_script(raw, '__INITIAL_SEARCH_RESULTS__')
	hits = state['results'][0]['hits']

	jobs = []
	for hit in hits:
		if hit.get('is_blurred'):  # paywalled, subscribers only
			continue
		tags = hit.get('locations') or []
		if locations and not set(locations) & set(tags):
			continue
		salary = hit.get('salary')
		jobs.append(
			{
				'title': hit['title'],
				'company': hit['company_name'],
				# hit['url'] is false for some jobs and the external ATS
				# link for most others; remotive_com_url is the one that
				# always points back to the listing on remotive.com.
				'url': hit['remotive_com_url'],
				'published': dates.iso(hit['discovered_on']),
				'locations': tags,
				'salary': salary if salary and salary != 'unspecified' else None,
			}
		)
	# Sponsored pins break the date order; restore newest first.
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]
