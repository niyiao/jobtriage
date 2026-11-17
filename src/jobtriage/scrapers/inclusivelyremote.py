"""Inclusively Remote job scraper.

inclusivelyremote.com is WordPress with WP Job Manager and its REST API
left wide open — the cleanest WordPress case of the batch: one endpoint,
filters that work server-side, no nonce dance. We call it directly:

    GET /wp-json/wp/v2/job_listing
        ?per_page=100&orderby=date&order=desc
        &job_listing_category=<ids>
        &_fields=id,date_gmt,link,title,metas,class_list

Traps: Cloudflare 403s the default urllib User-Agent (any browser UA
passes); a full record drags its ~100KB description along, so _fields is
not optional; and job_listing_category takes term IDs — the taxonomy is
hierarchical but the REST filter does not include child terms, so
"Engineering" alone quietly misses posts tagged only "Backend
Engineering". FAMILIES['software']['categories'] therefore spells out
the whole developer family: Engineering, Software Development, AI/ML
and their dev sub-disciplines. Left out on purpose: the support and tool tags
inside the dev trees (Technical Support, IT Support, Jira), Data
Analytics, and Blockchain/Cryptocurrency/Web3 — inspected live, those
are an accountant and four marketers, no developers. Re-derive IDs from
/wp-json/wp/v2/job_listing_category when the board reshuffles terms; the
list runs past per_page=100, and "Software Development" hides on page 2.

Metas carry the payload: _job_employer_name, _job_salary and
_job_location arrive HTML-escaped ("&#36;140000 - &#36;205000 / year"),
so `parse_html.html_text` unescapes them (and strips any markup);
_job_apply_url points at the employer's ATS and we keep the post's own
link instead. date_gmt is UTC and already ISO.

Location vocabulary — the job_listing_location taxonomy, in the board's
own wording. The region-level terms are "Remote Jobs Worldwide" (slug
worldwide) and "Remote Jobs in EMEA" (slug europe_middle-east_africa);
those two are the board's declared region tags. Narrower terms are
country-level and quirky: "Remote Jobs in Nigeria", "Remote Jobs in the United Kingdom",
"South-Africa", "Kenya" (slug kenya-specific). Per job, _job_location
holds {term_id: display name}; the odd post leaves it empty, so the
job_listing_location-<slug> classes in class_list are the fallback.
Filtering checks both spellings — slugs and display names both work as
values, matched exactly as the board words them.

Listings expire after ~30 days, so the developer slice is ~90 posts on
a single page; the page loop is future-proofing, capped at three
requests. date_gmt is fixed-format UTC, so a plain string sort is true
chronological order.
"""

from collections.abc import Sequence

from jobtriage.plugin import http
from jobtriage.plugin import parse as parse_html

NAME = 'inclusivelyremote'
ALIASES = ('ir',)
TITLE = 'Inclusively Remote'
HOMEPAGE = 'https://inclusivelyremote.com'
# One page covers the developer slice; the loop is capped at three.
POLITE_DELAY = 0

BASE_URL = 'https://inclusivelyremote.com'
API_URL = f'{BASE_URL}/wp-json/wp/v2/job_listing'

PER_PAGE = 100
MAX_PAGES = 3

# The job_listing_category term IDs for the developer family; see the
# module docstring for why the sub-disciplines are enumerated instead of
# trusting the "Engineering" and "Software Development" parents. The
# structure lives inside FAMILIES because the IDs are what "software"
# means on this board, not a separate list that could drift from it.
FAMILIES = {
	'software': {
		'categories': (
			182,  # Engineering
			59,  # Software Development
			1136,  # Artificial Intelligence
			320,  # Machine Learning
			3107,  # Machine Learning Engineering
			# children of Engineering
			3078,  # Backend Engineering
			3075,  # Frontend Engineering
			3301,  # Full Stack Engineering
			3609,  # Android Engineering
			3074,  # Quality Assurance Engineering
			3527,  # Cyber-Security Engineering
			3077,  # DevOps
			3073,  # Cloud Engineering
			4502,  # Infra
			3069,  # AWS
			3063,  # C/C++
			3062,  # Golang
			3060,  # Kubernetes
			3056,  # Linux
			3068,  # MongoDB
			3093,  # MySQL
			3061,  # Python
			3070,  # Terraform
			3064,  # Flutter
			# children of Software Development
			3057,  # Android
			3058,  # iOS
			3065,  # JavaScript
			3302,  # Mobile Development
			3066,  # React Native
			3106,  # Rust
			3067,  # TypeScript
			3097,  # WordPress
			3096,  # Game Development (filed under Design; still dev work)
		),
	}
}

# The board's two region-level location terms, translated by
# geo.board_tags: "Remote Jobs Worldwide" (slug worldwide) and "Remote
# Jobs in EMEA" (slug europe_middle-east_africa). The narrower terms are
# country-level and quirky ("Remote Jobs in Nigeria", "South-Africa",
# "Kenya"), so they are not spelt out as a country style: the EMEA term
# is how this board says "open across the region", and it is the only
# region a country preference maps onto here.
WORLDWIDE = ('worldwide',)
REGIONS = {'emea': ('europe_middle-east_africa',)}
COUNTRY_STYLE = 'none'


def recent_jobs(
	locations: Sequence[str] | None = None,
	limit: int = 50,
	categories: Sequence[int] = FAMILIES['software']['categories'],
) -> list[dict]:
	"""Return the most recent developer jobs on Inclusively Remote, newest first.

	locations: keep only jobs open to at least one of these location tags;
	either taxonomy slugs ("worldwide", "nigeria") or the board's display
	names ("Remote Jobs Worldwide", "Remote Jobs in Nigeria") work, matched
	exactly. None applies no location filter. The pipeline computes the
	tags from the user's config with `geo.board_tags`.
	categories: job_listing_category term IDs to list (default: the
	software family's categories). Empty means the whole board.
	"""
	return parse(fetch(categories), locations=locations, limit=limit)


def fetch(categories: Sequence[int] = FAMILIES['software']['categories']) -> list[dict]:
	"""Page through the REST endpoint and return every post, flattened.

	Asking past the last page earns a 400, not an empty list; the helper
	does not expose response headers, so the short-page check and that
	400 are the stop signals (the old `X-WP-TotalPages` shortcut is
	gone).
	"""
	posts = []
	for page in range(1, MAX_PAGES + 1):
		query = {
			'per_page': str(PER_PAGE),
			'page': str(page),
			'orderby': 'date',
			'order': 'desc',
			'_fields': 'id,date_gmt,link,title,metas,class_list',
		}
		if categories:
			query['job_listing_category'] = ','.join(str(c) for c in categories)
		try:
			batch = http.get_json(API_URL, params=query)
		except http.HttpError as error:
			if error.status == 400 and page > 1:
				break
			raise
		posts.extend(batch)
		if len(batch) < PER_PAGE:
			break
	return posts


def parse(
	raw: Sequence[dict], *, locations: Sequence[str] | None = None, limit: int = 50
) -> list[dict]:
	"""Turn REST posts into job dicts, newest first.

	Pure: the location check reads both the display names in `metas`
	and the `class_list` slugs, because a couple of posts leave the
	metas empty.
	"""
	jobs = []
	for post in raw:
		metas = post['metas']
		names = [parse_html.html_text(name) for name in (metas.get('_job_location') or {}).values()]
		slugs = [
			cls.removeprefix('job_listing_location-')
			for cls in post['class_list']
			if cls.startswith('job_listing_location-')
		]
		# A couple of posts leave _job_location empty; the class_list slugs
		# are the same information, just less prettily worded.
		if not names:
			names = slugs
		if locations and not set(locations) & (set(names) | set(slugs)):
			continue
		salary = parse_html.html_text(metas.get('_job_salary') or '')
		jobs.append(
			{
				'title': parse_html.html_text(post['title']['rendered']),
				'company': parse_html.html_text(metas.get('_job_employer_name') or ''),
				'url': post['link'],
				'published': post['date_gmt'],
				'locations': names,
				'salary': salary or None,
			}
		)
	# date_gmt is fixed-format UTC, so string order is chronological order.
	jobs.sort(key=lambda job: job['published'] or '', reverse=True)
	return jobs[:limit]
