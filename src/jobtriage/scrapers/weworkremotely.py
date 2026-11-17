"""We Work Remotely job scraper.

We Work Remotely advertises its data through per-category RSS feeds, and
each feed is a complete census of its category, not a recent slice: the
full-stack feed returns every active listing (104 items on the day of
writing, the same count as the site's own "View all 104 jobs"), so five
feed requests cover the whole developer board with no pagination. Two
URL traps: the new-style slugs (`/categories/full-stack.rss`) reply with
a 301 that carries no Location header — a redirect to nowhere — while
the old long slugs (`remote-full-stack-programming-jobs`) still serve
the goods; and the site-wide `/remote-jobs.rss` is capped at 100 items
(ten per category), useless as a census. Cloudflare's managed challenge
guards the HTML pages but not the feeds, and the default urllib user
agent is 403'd even there, so we send a browser one.

Item shape: `<title>` is "Company: Role" (split on the first ": "),
`<region>` is the headline restriction ("Anywhere in the World" is the
worldwide marker; "USA Only", "North America Only", "California" and
friends are not), `<pubDate>` is RFC-822 UTC, and there is no salary
element — pay lives in the prose, so salary is None.

The region tag lies in two ways. Jobs tagged "Anywhere in the World"
often carry a `<country>` list of flag-emoji countries that is the real
restriction (US-only jobs routinely wear the worldwide tag), so whenever
a job names countries, that list replaces the region as the job's
location. And jobs with no country list sometimes let the fine print
contradict the tag — "Remote - United States", "candidates must be
located in the US", the title "(U.S. Based, Remote Contractor)". A
deliberately narrow regex battery catches those; global companies whose
descriptions merely mention US pay bands or US-based benefits
(DuckDuckGo, Reddit's boilerplate) stay in. WWR's own feed test posts
("Do Not Apply") are skipped in every mode.

Location vocabulary: the region, plus the country names a job lists,
worded as the board words them (UN-style names, normalised to the short
forms people actually type: "Moldova (Republic of)" becomes "Moldova",
the UK's full ceremonial name becomes "United Kingdom"). Links stay on
weworkremotely.com — the API terms route consumers through WWR, and the
feed link already does.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence

from jobtriage import geo
from jobtriage.plugin import dates, http

NAME = 'weworkremotely'
ALIASES = ('wwr',)
TITLE = 'We Work Remotely'
HOMEPAGE = 'https://weworkremotely.com'
# The /api-terms route consumers through WWR, which the feed URLs in
# `url` already do.
TERMS = 'Link to the weworkremotely.com job URL; the API terms keep consumers on WWR.'
# Five category feeds per run is the whole board; space them.
POLITE_DELAY = 0.5

BASE_URL = 'https://weworkremotely.com'

# The five developer categories, under the old long slugs: the new-style
# slugs (e.g. "full-stack") redirect to nowhere (301, no Location). The
# structure lives inside FAMILIES because those slugs are what "software"
# means on this board, not a separate list that could drift from it.
FAMILIES = {
	'software': {
		'categories': (
			'remote-full-stack-programming-jobs',
			'remote-back-end-programming-jobs',
			'remote-front-end-programming-jobs',
			'remote-devops-sysadmin-jobs',
			'remote-product-jobs',
		),
	}
}

# Region and country tags meaning "open worldwide or across Europe, the
# Middle East and Africa", worded exactly as the feeds word them,
# translated by geo.board_tags. The country names carry the default
# filter: a job that lists countries is restricted to them no matter what
# its region tag says, so a Europe list passes through its country names
# ("Austria", "Poland", ...) while an "Anywhere in the World" job listing
# only the USA does not.
#
# The board carries 112 of the 129 EMEA countries. `_WORDING` lists those
# and every other EMEA code is suppressed by the empty alias — no country
# is invented. The UN-style feeds are normalised to the board's own
# short spellings ("Congo" for CG, "Cape Verde" for CV, the full name
# for CD, "Turkey" for TR).
_WORDING = {
	'AD': 'Andorra',
	'AE': 'United Arab Emirates',
	'AL': 'Albania',
	'AM': 'Armenia',
	'AO': 'Angola',
	'AT': 'Austria',
	'BA': 'Bosnia and Herzegovina',
	'BE': 'Belgium',
	'BF': 'Burkina Faso',
	'BG': 'Bulgaria',
	'BH': 'Bahrain',
	'BI': 'Burundi',
	'BJ': 'Benin',
	'BW': 'Botswana',
	'BY': 'Belarus',
	'CD': 'Democratic Republic of the Congo',
	'CG': 'Congo',
	'CH': 'Switzerland',
	'CI': "Côte d'Ivoire",
	'CM': 'Cameroon',
	'CV': 'Cape Verde',
	'CY': 'Cyprus',
	'CZ': 'Czechia',
	'DE': 'Germany',
	'DJ': 'Djibouti',
	'DK': 'Denmark',
	'DZ': 'Algeria',
	'EE': 'Estonia',
	'EG': 'Egypt',
	'ER': 'Eritrea',
	'ES': 'Spain',
	'ET': 'Ethiopia',
	'FI': 'Finland',
	'FR': 'France',
	'GA': 'Gabon',
	'GB': 'United Kingdom',
	'GE': 'Georgia',
	'GH': 'Ghana',
	'GM': 'Gambia',
	'GN': 'Guinea',
	'GQ': 'Equatorial Guinea',
	'GR': 'Greece',
	'GW': 'Guinea-Bissau',
	'HR': 'Croatia',
	'HU': 'Hungary',
	'IE': 'Ireland',
	'IL': 'Israel',
	'IQ': 'Iraq',
	'IR': 'Iran',
	'IS': 'Iceland',
	'IT': 'Italy',
	'JO': 'Jordan',
	'KE': 'Kenya',
	'KM': 'Comoros',
	'KW': 'Kuwait',
	'LB': 'Lebanon',
	'LI': 'Liechtenstein',
	'LR': 'Liberia',
	'LS': 'Lesotho',
	'LT': 'Lithuania',
	'LU': 'Luxembourg',
	'LV': 'Latvia',
	'LY': 'Libya',
	'MA': 'Morocco',
	'MC': 'Monaco',
	'MD': 'Moldova',
	'ME': 'Montenegro',
	'MG': 'Madagascar',
	'MK': 'North Macedonia',
	'ML': 'Mali',
	'MR': 'Mauritania',
	'MT': 'Malta',
	'MU': 'Mauritius',
	'MW': 'Malawi',
	'MZ': 'Mozambique',
	'NA': 'Namibia',
	'NE': 'Niger',
	'NG': 'Nigeria',
	'NL': 'Netherlands',
	'NO': 'Norway',
	'OM': 'Oman',
	'PL': 'Poland',
	'PS': 'Palestine',
	'PT': 'Portugal',
	'QA': 'Qatar',
	'RO': 'Romania',
	'RS': 'Serbia',
	'RW': 'Rwanda',
	'SA': 'Saudi Arabia',
	'SC': 'Seychelles',
	'SD': 'Sudan',
	'SE': 'Sweden',
	'SI': 'Slovenia',
	'SK': 'Slovakia',
	'SL': 'Sierra Leone',
	'SM': 'San Marino',
	'SN': 'Senegal',
	'SO': 'Somalia',
	'SS': 'South Sudan',
	'SY': 'Syria',
	'SZ': 'Eswatini',
	'TD': 'Chad',
	'TG': 'Togo',
	'TN': 'Tunisia',
	'TR': 'Turkey',
	'TZ': 'Tanzania',
	'UA': 'Ukraine',
	'UG': 'Uganda',
	'YE': 'Yemen',
	'ZA': 'South Africa',
	'ZM': 'Zambia',
	'ZW': 'Zimbabwe',
}

WORLDWIDE = ('Anywhere in the World',)
# Under containment "EMEA Only" claims EMEA and "Europe Only" claims
# Europe; each sits under its own, smallest covering region.
REGIONS = {
	'emea': ('EMEA Only',),
	'europe': ('Europe Only',),
}
COUNTRY_STYLE = 'name'
COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}

# Region tags that claim the whole world while the fine print says
# US or Canada. Case-sensitive on purpose: the title pattern must match
# "US" and "U.S." without matching the pronoun in "Join us".
_US_TITLE_RE = re.compile(r'\b(?:U\.S\.?|USA?)\b')
_US_TEXT_RE = re.compile(
	# "authorized to work in the US", "work authorization in the US" —
	# but not "FedRAMP-authorized US Government environments"
	r'\bauthoriz\w*\s+(?:to\s+)?(?:work\s+)?(?:in|within)\s+(?:the\s+)?(?:u\.?s\.?a?|united states)\b'
	# "eligible to work in the US"
	r'|\beligible\s+to\s+work\s+(?:in|within)\s+(?:the\s+)?(?:u\.?s\.?a?|united states)\b'
	# "candidates located in the United States", "must be based in Canada"
	r'|\b(?:must|need|needs|requiring|required|candidates?|only)\b[^.]{0,60}'
	r'\b(?:located|based|living|residing)\b[^.]{0,40}\b(?:in|within)\b[^.]{0,40}'
	r'\b(?:u\.?s\.?a?|united states|canada)\b'
	# location lines: "Remote - United States", "Remote (USA", "Remote in
	# the U.S.", "Remote within Canada or United States"
	r'|\bremote\s*(?:[-\u2013(]|\bin\s+|\bwithin\s+)\s*(?:the\s+)?'
	r'(?:(?:canada|north america)\s+(?:or|and)\s+)?(?:u\.?s\.?a?|united states)\b'
	# "anywhere in the U.S."
	r'|\banywhere\s+in\s+the\s+u\.?s\.?a?\b'
	# "this position is US-based"
	r'|\b(?:position|role|job)\s+is\s+(?:u\.?s\.?|usa)\s*-?\s*based\b'
	# "location within the US"
	r'|\bwithin\s+the\s+(?:u\.?s\.?|united states)\b'
	# "United States | Remote"
	r'|\bunited states\s*\|\s*remote\b',
	re.IGNORECASE,
)

# The <country> element is flag emoji followed by UN-style names.
_FLAG_RE = re.compile('[\U0001f1e6-\U0001f1ff]+')
_PAREN_RE = re.compile(r'\s*\([^)]*\)')
_AND_RE = re.compile(r'^and\s+', re.IGNORECASE)
# UN-style names are normalised to the short forms people type.
_COUNTRY_NAMES = {
	'United Kingdom of Great Britain and Northern Ireland': 'United Kingdom',
	'Türkiye': 'Turkey',
	'United States of America': 'United States',
}


def fetch(categories: Sequence[str] = FAMILIES['software']['categories']) -> list[str]:
	"""Fetch every category's RSS feed; the default urllib UA 403s."""
	return [
		http.get_text(
			f'{BASE_URL}/categories/{slug}.rss',
			headers={'Accept': 'application/rss+xml, application/xml, text/xml'},
		)
		for slug in categories
	]


def _parse_countries(raw: str | None) -> list[str]:
	"""Turn a <country> flag list into plain, short country names."""
	if not raw or not raw.strip():
		return []
	names = []
	for part in _FLAG_RE.sub('', raw).split(','):
		part = _PAREN_RE.sub('', part).strip()
		part = _AND_RE.sub('', part)
		if part:
			names.append(_COUNTRY_NAMES.get(part, part))
	return names


def _fine_print_restricted(title: str, description: str) -> bool:
	"""True when the text contradicts an "Anywhere in the World" tag."""
	if _US_TITLE_RE.search(title):
		return True
	text = re.sub(r'<[^>]+>', ' ', description or '')
	text = text.replace('&nbsp;', ' ')
	return bool(_US_TEXT_RE.search(text))


def _open_to(
	region: str,
	countries: list[str],
	title: str,
	description: str,
	locations: Sequence[str],
) -> bool:
	"""A job is open to `locations` if its effective tags intersect them.

	The effective tags are the country list when the job names countries
	(the region headline is decoration by then), else the region. Jobs
	whose only evidence is an "Anywhere in the World" tag get one extra
	look at the fine print.
	"""
	effective = countries or [region]
	if not set(effective) & set(locations):
		return False
	return not (
		region == 'Anywhere in the World'
		and not countries
		and _fine_print_restricted(title, description)
	)


def _parse_item(item: ET.Element) -> dict | None:
	"""Turn one RSS <item> into a record, or None for WWR's test posts."""
	raw_title = item.findtext('title') or ''
	if 'do not apply' in raw_title.lower():
		return None
	company, sep, title = raw_title.partition(': ')
	if not sep:  # not the usual "Company: Role" shape
		title, company = raw_title, ''
	region = (item.findtext('region') or '').strip() or 'Anywhere in the World'
	countries = _parse_countries(item.findtext('country'))
	# Display tags: the region headline plus the countries, or a count
	# when the list is too long to print (one job lists 73 of them).
	tags = [region]
	if countries:
		tags += countries if len(countries) <= 8 else [f'{len(countries)} countries']
	return {
		'title': title.strip(),
		'company': company.strip(),
		'url': (item.findtext('link') or '').strip(),
		'published': dates.iso(item.findtext('pubDate')),
		'locations': tags,
		'salary': None,  # no salary element; WWR keeps pay in the prose
		# internal bookkeeping, stripped before returning
		'region': region,
		'countries': countries,
		'description': item.findtext('description') or '',
	}


def recent_jobs(
	locations: Sequence[str] | None = None,
	limit: int = 50,
	categories: Sequence[str] = FAMILIES['software']['categories'],
) -> list[dict]:
	"""Return the most recent jobs on We Work Remotely, newest first.

	locations: keep only jobs open to at least one of these tags — a
	region ("Anywhere in the World", "Europe Only", "USA Only") or a
	country name as the board words it ("Germany", "United Kingdom");
	None applies no location filter. The pipeline computes the tags from
	the user's config with `geo.board_tags`.
	categories: the category feed slugs to sweep (default: the software
	family's five developer categories).
	"""
	return parse(fetch(categories), locations=locations, limit=limit)


def parse(
	raw: Sequence[str], *, locations: Sequence[str] | None = None, limit: int = 50
) -> list[dict]:
	"""Turn the category feeds into job dicts, newest first.

	Pure: cross-posts are deduped by URL, and the region/country/fine
	print check decides whether a job is open to the requested tags.
	"""
	records: dict[str, dict] = {}
	for feed in raw:
		# Re-encode: an `<?xml ... encoding="UTF-8"?>` declaration is
		# legal in bytes and a ValueError in a str, and the feeds carry
		# one. The helper already decoded it as UTF-8.
		for item in ET.fromstring(feed.encode('utf-8')).findall('./channel/item'):
			record = _parse_item(item)
			if record is not None:
				records.setdefault(record['url'], record)  # cross-posts
	ordered = sorted(records.values(), key=lambda record: record['published'], reverse=True)
	if locations is not None:
		ordered = [
			record
			for record in ordered
			if _open_to(
				record['region'],
				record['countries'],
				record['title'],
				record['description'],
				locations,
			)
		]
	return [
		{
			'title': record['title'],
			'company': record['company'],
			'url': record['url'],
			'published': record['published'],
			'locations': record['locations'],
			'salary': record['salary'],
		}
		for record in ordered[:limit]
	]
