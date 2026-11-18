"""Offline fixtures for the fifteen built-in boards (plan 04 step 5).

One synthetic response per board — structurally the live one, with the
personal data replaced — saved under `tests/fixtures/<board>/`. Each is
shaped from the board's own `parse()`: the Elasticsearch envelope
Working Nomads reads, the `__NEXT_DATA__` cache Wellfound walks, the
`{"listing", "details"}` pair BruntWork needs. The boards' notes in
docs/scrapers.md say what the live payload looks like; these files are
that shape, so a parsing refactor that drifts from it fails here with
no network and nobody's data.

The contract test runs every fixture through `registry.validate`, the
same boundary `registry.run` uses, so a fixture also catches a board
drifting from the plugin contract. The per-board tests below pin each
fixture's quirk — the thing its parser exists to handle: dedupe,
sorting, a location filter, mojibake repair, a fine-print trap. When a
board changes, record a new fixture; the diff shows what moved.
"""

import json
from datetime import datetime
from importlib import import_module
from pathlib import Path

import pytest

from jobtriage import registry, scrapers
from jobtriage.plugin import dates

FIXTURES = Path(__file__).parent / 'fixtures'


def text(board: str, name: str) -> str:
	"""One fixture file as the board's `fetch` would hand it over."""
	return (FIXTURES / board / name).read_text(encoding='utf-8')


def data(board: str, name: str):
	"""One JSON fixture, parsed the way `http.get_json` parses a body."""
	return json.loads(text(board, name))


def module(board: str):
	"""The built-in scraper module by board name."""
	return import_module(f'{scrapers.__name__}.{board}')


# Each board's fixture, loaded into the positional `raw` its parse()
# takes. Multi-request boards keep one file per request and pass the
# list the production `fetch` would return.
RAW = {
	'workingnomads': lambda: data('workingnomads', 'search.json'),
	'remotesource': lambda: data('remotesource', 'jobs.json'),
	'eurotechjobs': lambda: text('eurotechjobs', 'listing.html'),
	'remotive': lambda: text('remotive', 'listing.html'),
	'justremote': lambda: data('justremote', 'jobs.json'),
	'jobgether': lambda: data('jobgether', 'jobs.json'),
	'remoteok': lambda: data('remoteok', 'feed.json'),
	'inclusivelyremote': lambda: data('inclusivelyremote', 'posts.json'),
	'weworkremotely': lambda: [
		text('weworkremotely', 'full-stack.xml'),
		text('weworkremotely', 'back-end.xml'),
	],
	'wellfound': lambda: [
		text('wellfound', 'page-1.html'),
		text('wellfound', 'page-2.html'),
	],
	'remocate': lambda: [
		text('remocate', 'page-1.html'),
		text('remocate', 'page-2.html'),
	],
	'remotefrontendjobs': lambda: data('remotefrontendjobs', 'page-1.json'),
	'euremotejobs': lambda: data('euremotejobs', 'payload.json'),
	'bruntwork': lambda: data('bruntwork', 'payload.json'),
	'dynamitejobs': lambda: data('dynamitejobs', 'documents.json'),
}


def parse(board: str, **options) -> list[dict]:
	"""Run one board's parse() on its fixture."""
	return module(board).parse(RAW[board](), **options)


# -- the contract ----------------------------------------------------------


@pytest.mark.parametrize('board', sorted(RAW))
def test_every_board_parses_its_fixture_into_contract_jobs(board):
	jobs = parse(board)

	assert jobs, f'{board}: the fixture produced no jobs'
	kept, problems = registry.validate(jobs)
	assert problems == [], f'{board}: the fixture is not contract-clean'
	assert len(kept) == len(jobs)

	for job in jobs:
		assert job['title'].strip(), board
		assert job['company'].strip(), board
		assert job['url'].startswith(('http://', 'https://')), board
		if job['published'] is not None:
			datetime.fromisoformat(job['published'])  # raises if not ISO 8601

	# Every board sorts newest first, undated last.
	order = [job['published'] or '' for job in jobs]
	assert order == sorted(order, reverse=True), board


# -- each board's parsing quirks ------------------------------------------


def test_workingnomads_sorts_elasticsearch_hits_and_keeps_an_empty_salary_none():
	jobs = parse('workingnomads')

	assert [job['title'] for job in jobs] == ['Platform Engineer', 'Junior Web Developer']
	assert jobs[0]['url'] == ('https://www.workingnomads.com/jobs/platform-engineer-cobalt-cloud')
	assert jobs[1]['salary'] is None  # the board's empty salary_range_short


def test_remotesource_filters_scopes_client_side_and_formats_the_salary():
	raw = RAW['remotesource']()
	assert [job['title'] for job in module('remotesource').parse(raw)] == [
		'Staff Engineer',
		'Backend Engineer',
		'Senior Frontend Engineer',
	]

	emea = module('remotesource').parse(raw, locations=('EMEA',))
	assert [job['title'] for job in emea] == ['Backend Engineer']
	assert emea[0]['salary'] == '$100,000'  # min == max, one figure

	worldwide = module('remotesource').parse(raw, locations=('WORLDWIDE',))
	assert [job['title'] for job in worldwide] == ['Senior Frontend Engineer']


def test_eurotechjobs_dedupes_paid_cards_drops_city_only_rows_and_reads_relative_dates():
	page = RAW['eurotechjobs']()
	jobs = module('eurotechjobs').parse(page)

	assert [job['title'] for job in jobs] == ['Frontend Engineer', 'Senior Python Engineer']
	assert jobs[0]['published'] == dates.relative('Posted today')
	assert jobs[1]['published'] == dates.relative('Posted 3 days ago')
	assert 'QA Engineer' not in {job['title'] for job in jobs}  # Krakow, Poland

	# The location text, not the leaky remote badge, decides the filter.
	worldwide = module('eurotechjobs').parse(page, locations=('Remote, Multiple Countries',))
	assert [job['title'] for job in worldwide] == ['Senior Python Engineer']


def test_remotive_skips_paywalled_hits_sorts_by_discovered_on_and_reads_locations():
	page = RAW['remotive']()
	jobs = module('remotive').parse(page)

	assert [job['title'] for job in jobs] == [
		'Frontend Engineer',
		'Product Engineer',
		'Backend Engineer',  # the sponsored pin, sorted back by its date
	]
	assert jobs[2]['salary'] is None  # 'unspecified'

	europe = module('remotive').parse(page, locations=('Europe',))
	assert [job['title'] for job in europe] == ['Frontend Engineer']


def test_justremote_filters_category_infers_the_year_and_maps_empty_restrictions():
	raw = RAW['justremote']()
	jobs = module('justremote').parse(raw)

	assert [job['title'] for job in jobs] == ['Platform Engineer', 'Senior Rails Engineer']
	assert jobs[1]['published'] == dates.yearless('10 Nov')
	assert jobs[1]['locations'] == ['Anywhere']  # no restrictions = worldwide
	assert 'Growth Marketer' not in {job['title'] for job in jobs}

	assert [job['title'] for job in module('justremote').parse(raw, locations=('Germany',))] == [
		'Platform Engineer'
	]
	assert [job['title'] for job in module('justremote').parse(raw, locations=('Anywhere',))] == [
		'Senior Rails Engineer'
	]


def test_jobgether_dedupes_ids_drops_the_mistagged_batch_and_splits_location_tags():
	raw = RAW['jobgether']()
	jobs = module('jobgether').parse(raw)

	assert [job['title'] for job in jobs] == ['Frontend Developer', 'Backend Engineer']
	assert jobs[0]['title'] != 'Asesor Comercial Ventas'
	assert jobs[1]['locations'] == ['Germany', 'European Union']
	assert jobs[1]['salary'] is None  # salaryRange ''

	germany = module('jobgether').parse(raw, locations=('Germany',))
	assert [job['title'] for job in germany] == ['Backend Engineer']


def test_remoteok_skips_the_meta_record_repairs_mojibake_and_trusts_the_title_battery():
	raw = RAW['remoteok']()
	jobs = module('remoteok').parse(raw)

	assert [job['title'] for job in jobs] == [
		'Senior Backend Engineer',
		'Frontend Developer',
	]
	assert jobs[0]['locations'] == ['München']  # double-encoded UTF-8 repaired
	assert jobs[0]['salary'] == '$190k - $220k'
	assert jobs[1]['company'] == 'Fern & Field'  # HTML entity unescaped
	assert jobs[1]['locations'] == ['Worldwide']  # a blank location

	# Account Executive carries the board's decorative dev tag and is
	# still dropped; Sales Engineer is the family the battery excludes.
	assert 'Account Executive' not in {job['title'] for job in jobs}
	assert 'Sales Engineer' not in {job['title'] for job in jobs}

	worldwide = module('remoteok').parse(raw, locations=('worldwide',))
	assert [job['title'] for job in worldwide] == ['Frontend Developer']


def test_inclusivelyremote_decodes_metas_and_falls_back_to_the_class_list_slugs():
	raw = RAW['inclusivelyremote']()
	jobs = module('inclusivelyremote').parse(raw)

	assert [job['title'] for job in jobs] == ['Platform Engineer', 'Senior & Lead Engineer']
	assert jobs[0]['locations'] == ['europe_middle-east_africa']  # empty metas
	assert jobs[1]['company'] == 'Cobalt & Co'
	assert jobs[1]['salary'] == '$140000 - $205000 / year'

	worldwide = module('inclusivelyremote').parse(raw, locations=('worldwide',))
	assert [job['title'] for job in worldwide] == ['Senior & Lead Engineer']


def test_weworkremotely_dedupes_cross_posts_skips_its_test_post_and_reads_countries():
	raw = RAW['weworkremotely']()
	jobs = module('weworkremotely').parse(raw)

	assert [job['title'] for job in jobs] == [
		'Backend Engineer',
		'Platform Engineer',
		'Senior Full-Stack Engineer',
		'Site Reliability Engineer',
	]
	assert jobs[2]['locations'] == ['Anywhere in the World', 'Germany', 'Poland']
	assert 'Do Not Apply' not in {job['title'] for job in jobs}

	germany = module('weworkremotely').parse(raw, locations=('Germany',))
	assert [job['title'] for job in germany] == ['Senior Full-Stack Engineer']

	# The fine print ("authorized to work in the US") beats the worldwide tag.
	worldwide = module('weworkremotely').parse(raw, locations=('Anywhere in the World',))
	assert [job['title'] for job in worldwide] == ['Site Reliability Engineer']


def test_wellfound_walks_the_apollo_cache_dedupes_promoted_ids_and_sorts_by_time():
	raw = RAW['wellfound']()
	jobs = module('wellfound').parse(raw)

	assert [job['title'] for job in jobs] == [
		'Frontend Engineer',
		'Senior Backend Engineer',
		'Platform Engineer',
	]
	assert jobs[1]['salary'] == '$170k – $200k • 1.0% – 2.0%'

	# Empty acceptedRemoteLocationNames means US/unknown, not worldwide.
	europe = module('wellfound').parse(raw, locations=('Europe',))
	assert [job['title'] for job in europe] == ['Senior Backend Engineer', 'Platform Engineer']


def test_remocate_reads_badges_across_interleaved_scripts_and_dedupes_pages():
	raw = RAW['remocate']()
	jobs = module('remocate').parse(raw)

	assert [job['title'] for job in jobs] == [
		'Backend Engineer',
		'Platform Engineer',
		'Product Designer',
	]
	assert jobs[0]['published'] == '2026-11-17T09:15:00'  # +01:00 normalised to UTC
	assert jobs[1]['locations'] == ['Germany']  # the nested flag stays out
	assert jobs[1]['salary'] == '€70k - €90k'
	assert jobs[2]['locations'] == ['World']  # no country badge

	world = module('remocate').parse(raw, locations=('World',))
	assert [job['title'] for job in world] == ['Product Designer']


def test_remotefrontendjobs_dedupes_cross_posts_and_drops_bogus_salaries():
	raw = RAW['remotefrontendjobs']()
	jobs = module('remotefrontendjobs').parse(raw)

	assert [job['title'] for job in jobs] == [
		'Data Engineer',
		'Senior Frontend Engineer',
		'Backend Engineer',
	]
	assert jobs[1]['locations'] == ['worldwide']  # the newest cross-post copy
	assert jobs[0]['salary'] is None  # '1k - 2k per year' is bogus
	assert jobs[2]['salary'] == '$90,000 - $110,000 yearly'
	assert jobs[2]['url'] == 'https://www.remotefrontendjobs.com/4104'

	# A tag set outside the API's vocabulary is filtered client-side.
	assert module('remotefrontendjobs').parse(raw, locations=('north-america',)) == []


def test_euremotejobs_dedupes_cards_and_falls_back_to_card_text_for_unknown_names():
	raw = RAW['euremotejobs']()
	jobs = module('euremotejobs').parse(raw)

	assert [job['title'] for job in jobs] == ['Frontend Engineer', 'Senior Backend Engineer']
	assert jobs[1]['locations'] == ['Worldwide']
	assert jobs[1]['salary'] == '€90.000 - €110.000'

	# 'US' is not one of the board's filter labels: match the card text.
	us = module('euremotejobs').parse(raw, locations=('US',))
	assert [job['title'] for job in us] == ['Frontend Engineer']


def test_bruntwork_reads_badges_and_allows_a_missing_detail_page():
	raw = RAW['bruntwork']()
	jobs = module('bruntwork').parse(raw)

	assert [job['title'] for job in jobs] == [
		'Platform Engineer',
		'Senior Backend Engineer',
		'Junior QA Engineer',
	]
	assert jobs[0]['locations'] == ['Australia, New Zealand and Asia']
	assert jobs[1]['locations'] == ['Worldwide']  # no location badge
	assert jobs[2]['published'] is None  # its detail page is absent

	anz = module('bruntwork').parse(raw, locations=('Australia, New Zealand and Asia',))
	assert [job['title'] for job in anz] == ['Platform Engineer']


def test_dynamitejobs_reads_firestore_documents_and_only_public_salaries():
	raw = RAW['dynamitejobs']()
	jobs = module('dynamitejobs').parse(raw)

	assert [job['title'] for job in jobs] == ['Product Designer', 'Senior Backend Engineer']
	assert jobs[0]['published'] == '2026-11-16T10:00:00Z'  # the sortDateAt fallback
	assert jobs[0]['salary'] is None  # salary.public is false
	assert jobs[1]['salary'] == '$80,000 - $100,000/yr'
	assert jobs[1]['url'] == (
		'https://dynamitejobs.com/company/cobalt-cloud/remote-job/senior-backend-engineer'
	)

	europe = module('dynamitejobs').parse(raw, locations=('europe',))
	assert [job['title'] for job in europe] == ['Product Designer']
