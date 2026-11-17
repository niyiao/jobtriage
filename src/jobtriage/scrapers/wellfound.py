"""Wellfound job scraper.

Wellfound's role pages are fully server-rendered: GET /role/r/<role>?page=N
ships the page's whole Apollo cache inside __NEXT_DATA__
(props.pageProps.apolloState.data). The listing is
ROOT_QUERY.talent.seoLandingPageJobSearchResults(...) — 20 startups per
page, each StartupResult carrying highlightedJobListings refs to its
JobListingSearchResult entities. Company is a join, not a field —
normalized and indirect, but complete.

Traps:

- The default urllib user-agent gets a 403; any browser UA sails through.
- `acceptedRemoteLocationNames` is the eligibility vocabulary (where
  candidates may be based), unlike `locationNames` (office hubs).
  Empty does NOT mean worldwide: spot-checked detail pages say
  US-only, so the filter treats empty as US/unknown. No worldwide tag
  exists at all — recruiters pick regions, countries and cities — so a
  worldwide-open job just lists many places, and one open to EMEA
  always carries a matchable region or country tag.
- Promoted listings repeat across pages; dedupe by id.
- Page order is not date order (stale promoted jobs ride page one),
  so sort by `liveStartAt` — unix seconds.
- `remoteConfig.kind` is unreliable and sometimes null; the flat
  `remote` bool is honest (every /role/r/ job is remote anyway).
- `compensation` bundles salary and equity in one string
  ("$170k – $200k • 1.0% – 2.0%") and is "" when unset. Kept verbatim;
  the equity half is the Wellfound signature.
- /role/l/<role>/<place> landing pages filter by office location, not
  eligibility — half their jobs are onsite. A decoy. /graphql and
  /company/* are Cloudflare-challenge-gated, and never needed.

The remote software-engineer feed is ~90% US-anchored: the default EMEA
filter passes roughly one job in ten, and MAX_PAGES keeps us polite, so
a filtered run usually returns fewer than `limit` — the board's ceiling,
not a bug.
"""

from collections.abc import Sequence

from jobtriage import geo
from jobtriage.plugin import dates, http
from jobtriage.plugin import parse as parse_html

NAME = 'wellfound'
ALIASES = ('wf',)
TITLE = 'Wellfound'
HOMEPAGE = 'https://wellfound.com'
# The EMEA density is low, so a filtered run reads all five role pages
# at 20 startups each; a second is the least this board deserves.
POLITE_DELAY = 1.0

BASE_URL = 'https://wellfound.com'

# Politeness cap: a page holds 20 startups (~30-40 jobs), and only ~1 in
# 10 passes the default EMEA filter, so 5 pages buy ~15 matching jobs.
MAX_PAGES = 5

# acceptedRemoteLocationNames values as the board words them, translated
# by geo.board_tags. There is no worldwide tag on this board, so WORLDWIDE
# is empty: a worldwide-only preference yields no tags and the pipeline
# passes locations=None, which is correct — the board cannot express it
# and empty `acceptedRemoteLocationNames` means US/unknown, not open.
#
# `_WORDING` lists the 56 EMEA countries the old filter carried; every
# other EMEA code is suppressed by the empty alias, so no country is
# invented. Names are the board's full-wording style ("United Kingdom",
# not "UK"); TR is spelt "Turkey". CZ is the one code the board spells
# two ways — "Czechia" and "Czech Republic" — so its alias is a tuple
# and `board_tags` emits both wordings; `normalize` folds either back to
# CZ.
#
# City-level entries ("Paris, France") stay out: in live data they always
# ride along with a country or region tag. That is a deliberate old
# quirk, not an oversight.
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
	'CZ': 'Czechia',
	'DE': 'Germany',
	'DK': 'Denmark',
	'EE': 'Estonia',
	'EG': 'Egypt',
	'ES': 'Spain',
	'FI': 'Finland',
	'FR': 'France',
	'GB': 'United Kingdom',
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

WORLDWIDE = ()  # no worldwide tag exists on this board
# Every tag sits under its true area: "Europe" and "Africa" are the
# board's continent-wide buckets, the three sub-region tags cover only
# their own part. A containment match asks for a tag only when the tag's
# area covers the whole requested region or country.
REGIONS = {
	'europe': ('Europe',),
	'africa': ('Africa',),
	'central-europe': ('Central Europe',),
	'eastern-europe': ('Eastern Europe',),
	'western-europe': ('Western Europe',),
}
COUNTRY_STYLE = 'name'
COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}
COUNTRY_ALIASES['CZ'] = ('Czechia', 'Czech Republic')  # both board spellings emitted

# The board's role slugs are functions; software-engineer is the one the
# default scrape has always read.
FAMILIES = {'software': {'role': 'software-engineer'}}


def recent_jobs(
	role: str = FAMILIES['software']['role'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent remote jobs on Wellfound, newest first.

	role: a Wellfound role slug (default: the software family's
	software-engineer; product-manager, ai-engineer,
	machine-learning-engineer, data-engineer, frontend-engineer exist
	too).
	locations: keep only jobs whose acceptedRemoteLocationNames include
	one of these tags, e.g. ('Europe', 'Nigeria'); None applies no
	location filter. A filtered run often returns fewer than `limit`:
	the feed is mostly US-anchored and MAX_PAGES caps paging. The
	pipeline computes the tags from the user's config with
	`geo.board_tags`.
	"""
	return parse(fetch(role), locations=locations, limit=limit)


def fetch(role: str = FAMILIES['software']['role'], pages: int = MAX_PAGES) -> list[str]:
	"""GET the role pages, one HTML document per page."""
	urls = [f'{BASE_URL}/role/r/{role}'] + [
		f'{BASE_URL}/role/r/{role}?page={page}' for page in range(2, pages + 1)
	]
	return [http.get_text(url) for url in urls]


def parse(
	raw: Sequence[str], *, locations: Sequence[str] | None = None, limit: int = 50
) -> list[dict]:
	"""Turn the role pages into job dicts, newest first.

	Pure: `parse_html.next_data` pulls the Apollo cache out of each
	page; ids dedupe promoted listings that repeat across pages.
	"""
	wanted = set(locations) if locations else None
	seen: set[str] = set()
	found = []  # (liveStartAt, job, company, tags), sorted once at the end
	for index, page in enumerate(raw, 1):
		for company, job in _pairs(page, index):
			if job['id'] in seen:
				continue  # promoted listings repeat across pages
			seen.add(job['id'])
			tags = job.get('acceptedRemoteLocationNames') or []
			if wanted is not None and not wanted & set(tags):
				continue  # empty tags mean US/unknown, not worldwide
			found.append((job['liveStartAt'], job, company, tags))
	found.sort(key=lambda item: item[0], reverse=True)
	return [
		{
			'title': job['title'],
			'company': company,
			'url': f'{BASE_URL}/jobs/{job["id"]}-{job["slug"]}',
			'published': dates.iso(when),
			'locations': tags,
			'salary': job.get('compensation') or None,
		}
		for when, job, company, tags in found[:limit]
	]


def _pairs(page: str, number: int) -> list[tuple[str, dict]]:
	"""One page's (company, job) pairs, in board order."""
	cache = parse_html.next_data(page)['props']['pageProps']['apolloState']['data']
	talent = cache.get('ROOT_QUERY', {}).get('talent')
	if not isinstance(talent, dict):
		# TypeError is for Python types; a wrong page payload is a
		# value problem, so ValueError stays.
		raise ValueError(f'no Apollo ROOT_QUERY.talent in __NEXT_DATA__ (page {number})')  # noqa: TRY004
	results = next(
		(value for key, value in talent.items() if key.startswith('seoLandingPageJobSearchResults')),
		None,
	)
	if results is None:
		raise ValueError(f'no seoLandingPageJobSearchResults on page {number}; layout changed?')

	pairs = []
	for startup_ref in results.get('startups') or []:
		startup = cache.get(startup_ref.get('__ref', ''))
		company = startup.get('name') if isinstance(startup, dict) else None
		if not company:
			continue  # a listing with no resolvable company is skipped
		for job_ref in startup.get('highlightedJobListings') or []:
			job = cache.get(job_ref.get('__ref', ''))
			if isinstance(job, dict) and 'id' in job:
				pairs.append((company, job))
	return pairs
