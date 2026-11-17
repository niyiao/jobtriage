"""Jobgether job scraper.

The friendliest board in the set: Jobgether ships a documented, keyless
public search API meant for AI agents (jobgether.com/developers,
/openapi.json, /llms.txt), and a bare urllib user agent passes. One
endpoint does everything:

    GET https://jobgether.com/api/v1/jobs
        jobReferences=software-engineer,frontend-developer,...
        locations=emea   sort=date   limit=25   page=N

Knobs and traps: `sort=date` must be explicit (the API default is
relevance). `limit` is capped at 25 — a larger value is silently
truncated — so the module paginates, four pages at most. Multi-value
params AND together server-side: locations=emea,anywhere returns jobs
tagged with both, which is why a custom locations tuple is matched
client-side instead of sent. jobReferences is the exception — its
values OR. There is no published enum for those slugs (unknown ones
400, naming the offending value), so FAMILIES['software'] holds a
live-probed list, and a 400 naming one of its slugs is dropped and
retried once per culprit rather than breaking every future run.

Locations filter in two ways. The board's own EMEA view is one server
param, locations=emea, with "Anywhere" (worldwide) jobs included; the
module sends it whenever every requested tag is one the board's
EMEA/worldwide vocabulary can produce (`server_locations` exposes the
same decision to the pipeline). A country preference contributes the
worldwide tag, the tags of every declared region that contains it and
its own wording, so Nigeria (inside EMEA) rides the same param while a
country outside EMEA keeps its name tag and is matched client-side.
Anything else — a US preference, a typo'd city — falls back to matching
the displayed `location` tags, which are sometimes an abbreviation of
the job's true eligibility: a Germany-open role may display only
"European Union", while the server's slug filters match the full
internal eligibility data. The old code compared the tuple to
`OPEN_LOCATIONS` by order; that silently broke once geo reordered the
same set, so the test is now order-free.

Two data caveats. `postedAt` is batch-ingestion time — jobs share
timestamps to the millisecond — not the employer's posting date, but it
is the only date on offer. And the aggregator mis-tags batches of
Spanish-language telemarketing gigs as iOS/Swift developer roles, so a
small title filter drops those; they match no legitimate developer
title observed on the board.

Location vocabulary: "Anywhere" is the worldwide marker; the area tags
are "EMEA", "Europe", "European Union", "Middle East", "Africa",
"Sub-Saharan Africa", plus country names as the board words them
("United Kingdom", not "UK"). Each tag is declared under its true area,
so a containment match asks for "EMEA" when the whole of EMEA (or a
part of it) is the user's ask, and for "Sub-Saharan Africa" only when
that smaller area covers the ask.
"""

import json
import re
from collections.abc import Sequence
from datetime import datetime

from jobtriage import geo
from jobtriage.plugin import http

NAME = 'jobgether'
ALIASES = ('jg',)
TITLE = 'Jobgether'
HOMEPAGE = 'https://jobgether.com'
# Four pages is the cap; the API is meant for agents, but no reason to
# hammer it.
POLITE_DELAY = 0.5

SEARCH_URL = 'https://jobgether.com/api/v1/jobs'

# Job-function slugs covering the developer families: software,
# front/back/full-stack, mobile, QA, the ops cluster (no DevOps slug
# exists; it lives under SRE/infrastructure/platform/cloud), data,
# AI/ML, security, embedded. All probed live — broad slugs are avoided
# on purpose: "engineering" pulls in electrical and other physical
# engineers, "systems-engineer" systems analysts, and "game-developer"
# is a polluted bucket. `recent_jobs` reads the list from here, so the
# pipeline can pass a family's references explicitly.
FAMILIES = {
	'software': {
		'job_references': (
			'software-engineer',
			'software-developer',
			'frontend-developer',
			'frontend-engineer',
			'backend-developer',
			'backend-engineer',
			'full-stack-developer',
			'full-stack-engineer',
			'android-developer',
			'ios-developer',
			'qa-engineer',
			'site-reliability-engineer',
			'infrastructure-engineer',
			'platform-engineer',
			'cloud-engineer',
			'data-engineer',
			'data-scientist',
			'ai-engineer',
			'machine-learning-engineer',
			'security-engineer',
			'embedded-software-engineer',
			'firmware-engineer',
		)
	}
}

# Location tags as the board words them, translated by geo.board_tags.
# "Anywhere" is the worldwide marker; the area tags are "EMEA", "Europe",
# "European Union", "Middle East", "Africa" and "Sub-Saharan Africa".
# Each sits under its true area, smallest covering region, so a
# containment match reads "Sub-Saharan Africa" as covering a sub-Saharan
# preference but not a whole-Africa one, and "EMEA" as covering all of
# them. `_WORDING` lists the 56 EMEA countries the old filter carried
# and every other EMEA code is suppressed by the empty alias — no
# country is invented. Names are the board's full-wording style
# ("United Kingdom", not "UK"); CZ and TR keep their common spellings.
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

WORLDWIDE = ('Anywhere',)
REGIONS = {
	'emea': ('EMEA',),
	'europe': ('Europe',),
	'eu': ('European Union',),
	'middle-east': ('Middle East',),
	'africa': ('Africa',),
	'sub-saharan-africa': ('Sub-Saharan Africa',),
}
COUNTRY_STYLE = 'name'
COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}

# Every tag a worldwide/EMEA preference can produce on this board: the
# worldwide wording, the area wordings and the countries it carries.
# A requested tuple inside this set asks for nothing outside the board's
# EMEA view, so the API's own locations=emea filter is safe to send.
_EMEA_TAGS = frozenset(
	tag.casefold()
	for tag in (
		*WORLDWIDE,
		*(tag for tags in REGIONS.values() for tag in tags),
		*(w for w in COUNTRY_ALIASES.values() if w),
	)
)

# Region tokens whose members all lie inside EMEA: a preference built
# only from these is covered by locations=emea server-side. Every
# European sub-region and sub-Saharan Africa qualify by construction.
_EMEA_TOKENS = (
	frozenset(
		{
			'emea',
			'europe',
			'eu',
			'uk-ireland',
			'nordics',
			'benelux',
			'western-europe',
			'eastern-europe',
			'southern-europe',
			'northern-europe',
			'central-europe',
			'middle-east',
			'africa',
			'sub-saharan-africa',
		}
	)
	| frozenset(geo.REGIONS['emea'])
	| {geo.WORLDWIDE}
)


def server_locations(prefs) -> dict:
	"""The API query params for the user's prefs, server-side.

	`locations=emea` is the board's own EMEA view ("Anywhere" jobs
	included). It is the right param exactly when the user asks for
	nothing outside it; a preference that includes, say, the US falls
	back to the client-side match on displayed location tags. Returning
	a param dict gives the pipeline one documented seam instead of
	making it infer that an EMEA preference means `locations=emea`.
	"""
	wanted = geo.expand(prefs)
	if wanted and wanted <= _EMEA_TOKENS:
		return {'locations': 'emea'}
	return {}


PAGE_SIZE = 25  # the API caps `limit` at 25, larger values silently truncated
MAX_PAGES = 4  # pagination ceiling: ~100 jobs scanned per run

# Markers of the mis-tagged Spanish telemarketing/call-center batch
# (see module docstring); every observed gig hits at least one.
_NON_DEV_TITLE = re.compile(
	r'ventas|comision|asesor|comercial|telemarketing|televentas|'
	r'call ?center|trabaja desde casa|home ?office|vacantes|'
	r'horarios fijos|sin exp|planilla|turno|gana ',
	re.IGNORECASE,
)


def _fetch(params: dict) -> dict:
	"""GET /api/v1/jobs once and return the parsed payload.

	Retired job-function slugs are dropped from `params` in place and the
	request retried (bounded at three attempts); anything else the API
	rejects is raised as a ValueError carrying its own problem detail.
	"""
	for _ in range(3):
		try:
			return http.get_json(SEARCH_URL, params=params)
		except http.HttpError as error:
			if error.status != 400:
				raise
			try:
				problem = json.loads(error.body)
			except ValueError:
				problem = {}
			# The problem document names the offending value in double quotes.
			match = re.search(r'"([^"]+)"', problem.get('detail', ''))
			slugs = str(params.get('jobReferences', '')).split(',')
			if problem.get('field') != 'jobReferences' or not match or match.group(1) not in slugs:
				raise ValueError(
					f'jobgether API rejected the search: {problem.get("detail", error)}'
				) from error
			slugs = [slug for slug in slugs if slug != match.group(1)]
			if slugs:
				params['jobReferences'] = ','.join(slugs)
			else:
				params.pop('jobReferences')
	raise ValueError('jobgether API kept rejecting jobReferences after dropping the named slugs')


def recent_jobs(
	locations: Sequence[str] | None = None,
	limit: int = 50,
	job_references: Sequence[str] = FAMILIES['software']['job_references'],
) -> list[dict]:
	"""Return the most recent developer jobs on Jobgether, newest first.

	locations: keep only jobs open to at least one of these location
	tags, e.g. ("Anywhere", "EMEA", "Germany"); None applies no location
	filter. When every tag is within the board's EMEA/worldwide
	vocabulary — as `geo.board_tags` produces for EMEA or worldwide
	prefs — the filter rides the API's own locations=emea param (the
	board's EMEA region, "Anywhere" jobs included) as well as the
	client-side tags; any other tuple is matched client-side only.
	job_references: the job-function slugs to scope the search
	(default: the software family's 22 references).
	"""
	return parse(fetch(job_references, locations, limit), locations=locations, limit=limit)


def fetch(
	job_references: Sequence[str] = FAMILIES['software']['job_references'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Page through the search API and return every job row, flattened.

	The API caps a page at 25, so the page count follows from `limit`
	with one extra page of slack for the title and tag filters that
	run later in `parse`; `MAX_PAGES` stays the ceiling.
	"""
	params = {
		'jobReferences': ','.join(job_references),
		'sort': 'date',  # explicit: the API default is relevance
		'limit': PAGE_SIZE,
	}
	if locations and {str(tag).casefold() for tag in locations} <= _EMEA_TAGS:
		# One server-side param implements the whole EMEA filter. The
		# test is set-based: geo may order the same tags differently.
		params['locations'] = 'emea'

	pages_needed = min(MAX_PAGES, max(1, -(-limit // PAGE_SIZE) + 1))
	rows = []
	for page in range(1, pages_needed + 1):
		params['page'] = page
		payload = _fetch(params)
		rows.extend(payload.get('jobs', []))
		if not payload.get('pagination', {}).get('hasMore'):
			break
	return rows


def parse(
	raw: Sequence[dict], *, locations: Sequence[str] | None = None, limit: int = 50
) -> list[dict]:
	"""Turn API job rows into job dicts, newest first.

	Pure: dedupes ids across pages, drops the mis-tagged non-dev batch,
	and applies the client-side location-tag match.
	"""
	jobs = []
	seen = set()
	for item in raw:
		if item['id'] in seen:
			continue  # live ingestion can shift a job between pages
		seen.add(item['id'])
		if _NON_DEV_TITLE.search(item['title']):
			continue
		tags = [tag.strip() for tag in (item.get('location') or '').split(',') if tag.strip()]
		if locations and not set(locations) & set(tags):
			continue
		jobs.append(
			{
				'title': item['title'],
				'company': item['company'],
				'url': item['url'],
				'published': item['postedAt'],
				'locations': tags,
				'salary': item.get('salaryRange') or None,
			}
		)

	# postedAt is uniform ISO-8601 UTC, but mixed precision ("...54.652Z"
	# next to "...54Z") would mis-sort as plain strings.
	jobs.sort(key=lambda job: datetime.fromisoformat(job['published']), reverse=True)
	return jobs[:limit]
