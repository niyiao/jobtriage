"""EuroTechJobs job scraper.

eurotechjobs.com is a plain Laravel site, server-rendered and unguarded
— even the default urllib user agent walks straight in. There is no
API: the listing pages are the data, and filters are URL path segments:

    /job_search/category/developer   every developer job, one page
    /job_search/workplace/remote     every "remote" job, one page

The unfiltered front page (/job_search) is a shop window for the paid
tiers (~23 jobs) and hides the freshest free listings, so a filtered
URL is the only honest way in. We read the developer category — the
board's own Developer jobs (195 today), one page, no pagination — and
keep the jobs whose location text itself offers a remote option. The
board is Europe-only, and its workplace/remote filter is sloppier than
the text it labels: jobs leak onto it with locations like "Krakow,
Poland", so the location text wins over any remote badge.

Dates are relative only ("Posted today", "Posted 3 days ago"), so
published is date-precision: the UTC date counted back from today.
Page order is newest-first with paid cards pinned out of sequence on
top, so we re-sort by date, keeping page order for same-day ties.

Location vocabulary, from the live texts: "Remote, Multiple
Countries" is the open-worldwide marker and "Remote, EMEA, Multiple
Countries" the EMEA one; "Remote, Poland" means remote within one
country; "Lisbon or Remote, Portugal" offers office or remote. Tags
are the remote markers as worded, plus the country field — the last
comma-separated part of the line, which is exactly how the site's own
location filter buckets jobs, "Multiple Countries" included. No job
on the board carries salary data.

The rate-limit header allows 150 requests per window; one run spends
exactly one.
"""

import re
from collections.abc import Sequence

from jobtriage.plugin import dates, http
from jobtriage.plugin import parse as parse_html

NAME = 'eurotechjobs'
ALIASES = ('etj',)
TITLE = 'EuroTechJobs'
HOMEPAGE = 'https://www.eurotechjobs.com'
# One page per run; the rate-limit header allows 150 requests a window.
POLITE_DELAY = 0

BASE_URL = 'https://www.eurotechjobs.com'

# Remote-wide wordings as the board writes them, translated by
# geo.board_tags. "Remote, EMEA" is the short form, kept for the day the
# board writes it that way; today it writes the long one. "Remote,
# <Country>" markers are country-restricted and deliberately stay out —
# pass them explicitly to widen the filter. There is no country style
# here: the board's country markers only ever narrow a job, never make it
# open to a whole region, so a country preference maps onto the EMEA
# wording rather than a bespoke country tag.
WORLDWIDE = ('Remote, Multiple Countries',)
REGIONS = {
	'emea': (
		'Remote, EMEA',  # open across EMEA (short form)
		'Remote, EMEA, Multiple Countries',  # open across EMEA (as worded)
	),
}
COUNTRY_STYLE = 'none'

# The board's own Developer category is the software family; it is the
# category the default scrape has always read.
FAMILIES = {'software': {'category': 'developer'}}

# The board only lists European companies (with some worldwide-open
# roles), so a user asking for anywhere outside Europe would spend a
# request to get nothing. Plan 03 step 4 skips the board on this.
COVERS = ('europe',)

# Every listing, paid card or free row, is anchored by an h3 link; the
# block that follows it, up to the next anchor, holds the fields.
_LISTING_RE = re.compile(
	r'<h3><a href="(?P<href>/job_display/\d+/[^"]+)">(?P<title>.*?)</a></h3>',
	re.DOTALL,
)
_COMPANY_RE = re.compile(r'<div class="companyName">\s*(?P<company>.*?)\s*</div>', re.DOTALL)
_LOCATION_RE = re.compile(r'<div class="location">\s*(?P<location>.*?)\s*</div>', re.DOTALL)
# "Posted today" or "Posted N days ago" — nothing coarser, nothing finer.
_POSTED_RE = re.compile(r'Posted (?P<today>today)|Posted (?P<days>\d+) days? ago')


def _location_tags(text: str) -> list[str]:
	"""Split a location line into its remote markers and the country.

	"Krakow, Lisbon or Remote, Multiple Countries" becomes
	["Remote, Multiple Countries", "Multiple Countries"]; a line with
	no "Remote" alternative ("Krakow, Poland") is just its country.
	"""
	alternatives = [part.strip() for part in text.split(' or ')]
	tags = [part for part in alternatives if part.startswith('Remote')]
	country = text.rsplit(', ', 1)[-1].strip()
	if country not in tags:
		tags.append(country)
	return tags


def recent_jobs(
	category: str = FAMILIES['software']['category'],
	locations: Sequence[str] | None = None,
	limit: int = 50,
) -> list[dict]:
	"""Return the most recent remote developer jobs on EuroTechJobs, newest first.

	category: a EuroTechJobs category slug (default: the software
	family's developer category).
	locations: keep only remote jobs whose remote option is open to at
	least one of these tags, e.g. ("Remote, Multiple Countries",
	"Remote, Poland"); None applies no location filter — remote jobs
	anywhere, country-restricted ones included. The pipeline computes the
	tags from the user's config with `geo.board_tags`.
	"""
	return parse(fetch(category), locations=locations, limit=limit)


def fetch(category: str = FAMILIES['software']['category']) -> str:
	"""GET the category's server-rendered listing page."""
	return http.get_text(f'{BASE_URL}/job_search/category/{category}')


def parse(raw: str, *, locations: Sequence[str] | None = None, limit: int = 50) -> list[dict]:
	"""Turn the listing page into job dicts, newest first.

	Pure: "Posted today"/"Posted N days ago" becomes a date via
	`dates.relative`; the remote scope lives in the location text, not
	in the badge.
	"""
	anchors = list(_LISTING_RE.finditer(raw))
	if not anchors:
		raise ValueError(f'no job listings on {BASE_URL}/job_search; page layout changed?')

	jobs = []
	seen = set()
	for i, anchor in enumerate(anchors):
		if anchor['href'] in seen:  # the same job twice (paid card + row)
			continue
		seen.add(anchor['href'])
		block = raw[anchor.end() : anchors[i + 1].start() if i + 1 < len(anchors) else len(raw)]
		company = _COMPANY_RE.search(block)
		location = _LOCATION_RE.search(block)
		posted = _POSTED_RE.search(block)
		if company is None or location is None or posted is None:
			continue  # the three fields always come as a set; a stray anchor has none
		tags = _location_tags(parse_html.html_text(location['location']))
		# The remote scope lives in the location text: no "Remote"
		# alternative, no remote job — the board's own remote filter and
		# its Remote badges both leak city-only jobs.
		if not any(tag.startswith('Remote') for tag in tags):
			continue
		if locations and not set(locations) & set(tags):
			continue
		jobs.append(
			{
				'title': parse_html.html_text(anchor['title']),
				'company': parse_html.html_text(company['company']),
				'url': f'{BASE_URL}{anchor["href"]}',
				'published': dates.relative(posted.group(0)),
				'locations': tags,
				'salary': None,  # the board never carries salary data
			}
		)
	# Paid cards are pinned out of date order; restore newest first,
	# keeping the page's own order for same-day ties.
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]
