"""Containment: every built-in's declarations produce the expected tuple.

Plan 03 step 3 replaced each scraper's hardcoded `OPEN_LOCATIONS` with the
`WORLDWIDE` / `REGIONS` / `COUNTRY_STYLE` / `COUNTRY_ALIASES`
declarations that `geo.board_tags` translates. That conversion was
originally pinned as *reach-union parity*: the old tuples were
worldwide+EMEA+Nigeria, and every EMEA country wording was in them
because a region meant "everything it covers". That parity was retired
on 18 Nov 2026 when the user chose containment — a job matches when its
stated area *covers* one of the user's locations — so this file now
characterises the containment result for the user's real config,
`['NG']`: each board's worldwide tags, the tags of the regions that
contain Nigeria, and its Nigeria wording. A board whose only area tag is
"Europe" contributes no region tag for Nigeria, because a Europe-only
job does not cover Nigeria; only an EMEA-wide word does.

`EXPECTED` holds the tuples typed out as literals — deliberately *not*
imported from the modules — so a declaration that drifts fails here.
The board modules are found with `pkgutil.iter_modules`, the same way
the registry discovers them, so a module that stops being importable
fails here rather than vanishing from the run.

`PARITY_PREFS` is the user's actual preference list, not a repo
invention: Nigeria alone. It is what the location model is for.
"""

from importlib import import_module
from pkgutil import iter_modules

import pytest

from jobtriage import geo
from jobtriage import scrapers as scraper_package

# The user's real config: one country, the narrow containment case.
PARITY_PREFS = ['NG']

# The containment result for ['NG'], hand-written per board: the
# worldwide tags, the tags of every declared region that contains
# Nigeria (EMEA, Africa, Sub-Saharan Africa, where declared) and the
# board's own Nigeria wording, in the order `geo.board_tags` fixes.
# Deliberately not imported from the modules: this file is the net that
# catches a declaration drifting, so it has to be written independently.
EXPECTED: dict[str, tuple[str, ...]] = {
	'bruntwork': ('Worldwide',),
	'dynamitejobs': ('worldwide', 'africa', 'NG'),
	'euremotejobs': ('remote-jobs-worldwide', 'remote-jobs-emea'),
	'eurotechjobs': (
		'Remote, Multiple Countries',
		'Remote, EMEA',
		'Remote, EMEA, Multiple Countries',
	),
	'inclusivelyremote': ('worldwide', 'europe_middle-east_africa'),
	'jobgether': ('Anywhere', 'EMEA', 'Africa', 'Sub-Saharan Africa', 'Nigeria'),
	'justremote': ('Anywhere', 'Nigeria'),
	'remocate': ('World',),
	'remotefrontendjobs': ('worldwide', 'africa'),
	'remoteok': ('Worldwide', 'Remote', 'Anywhere', 'Anywhere in the World', 'Global', 'EMEA'),
	'remotesource': ('WORLDWIDE', 'EMEA'),
	'remotive': ('Worldwide', 'Nigeria'),
	'wellfound': ('Africa', 'Nigeria'),
	'weworkremotely': ('Anywhere in the World', 'EMEA Only', 'Nigeria'),
	'workingnomads': ('Anywhere', 'EMEA'),
}

# Every built-in module, discovered the way the registry discovers them:
# pkgutil over the scraper package, private modules out.
BOARDS = {
	name: import_module(f'{scraper_package.__name__}.{name}')
	for name in sorted(
		info.name for info in iter_modules(scraper_package.__path__) if not info.name.startswith('_')
	)
}


@pytest.mark.parametrize('board', sorted(EXPECTED))
def test_board_tags_produce_the_containment_tuple(board):
	"""The translated tags are exactly the containment result for NG.

	Both directions matter: a missing tag silently narrows the default
	scrape, and an extra one widens it to jobs the user never asked for
	— a stray Europe tag on remotive would let Europe-only jobs through
	for a Nigeria-based user. The multiset check also catches a
	duplicate that set equality would hide — `locations` is passed to
	the board as-is, duplicates and all.
	"""
	tags = geo.board_tags(BOARDS[board], PARITY_PREFS)
	expected = EXPECTED[board]

	assert set(tags) == set(expected), f'{board}: tags differ from the containment tuple'
	assert sorted(tags) == sorted(expected), f'{board}: tag multiset differs (duplicate?)'


def test_euremotejobs_server_locations_are_its_region_slugs():
	"""The one batch-1 board that filters location on the server: the
	hook's param values are the same slugs the tags carry (worldwide and
	EMEA for NG), so a pipeline never has to guess how a tag becomes a
	query string."""
	module = BOARDS['euremotejobs']

	assert module.server_locations(PARITY_PREFS) == {
		'search_region[]': list(EXPECTED['euremotejobs'])
	}


def test_jobgether_server_locations_is_the_boards_own_emea_view():
	"""jobgether's server filter is one boolean: is everything the user
	asked for inside the board's EMEA/worldwide view? EMEA, a country in
	it and worldwide alone all are; anything outside falls back to the
	client-side tag match. NG must keep riding locations=emea after the
	key re-keying, or the server-side filter would silently fall back."""
	module = BOARDS['jobgether']

	assert module.server_locations(PARITY_PREFS) == {'locations': 'emea'}
	assert module.server_locations(['worldwide']) == {'locations': 'emea'}
	assert module.server_locations(['nordics']) == {'locations': 'emea'}
	assert module.server_locations(['sub-saharan-africa']) == {'locations': 'emea'}
	assert module.server_locations(['US']) == {}


def test_remotefrontendjobs_server_locations_are_the_region_tags():
	"""The API's `regions` param is a set, so the hook sends the tags geo
	computed in its own order. NG contributes the worldwide tag and the
	africa tag (the board has no EMEA tag, and its EMEA-composite jobs
	carry africa among their tags); a country the board's regions do not
	cover still has the worldwide tag: a job open anywhere is open to
	it."""
	module = BOARDS['remotefrontendjobs']

	assert module.server_locations(PARITY_PREFS) == {'regions': 'worldwide,africa'}
	assert module.server_locations(['europe']) == {'regions': 'worldwide,europe'}
	assert module.server_locations(['US']) == {'regions': 'worldwide'}


def test_the_discovered_set_and_the_expectations_agree():
	"""The canary: no module may slip out of the containment net.

	`EXPECTED` is a literal list, so a new built-in must be added here
	with its containment tuple (or a deliberate note); a module that
	stopped importing is caught too. Every module must carry the
	declarations the translation reads, and the deleted `OPEN_LOCATIONS`
	must not come back.
	"""
	assert set(EXPECTED) == set(BOARDS), 'EXPECTED and the built-in boards disagree'

	declarations = ('WORLDWIDE', 'REGIONS', 'COUNTRY_STYLE', 'COUNTRY_ALIASES')
	for board in sorted(EXPECTED):
		module = BOARDS[board]
		assert any(hasattr(module, name) for name in declarations), (
			f'{board} declares no geo vocabulary'
		)
		assert not hasattr(module, 'OPEN_LOCATIONS'), (
			f'{board} still has OPEN_LOCATIONS: it was converted'
		)
