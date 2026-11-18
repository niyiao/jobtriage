"""The built-in boards' side of the plugin contract (plan 04 step 4).

Plan 04 turns the fifteen scrapers into ordinary plugins: `NAME` for
the registry, `ALIASES` for `-s`, `TITLE`/`HOMEPAGE` for listings,
`POLITE_DELAY` for the http helper, and a `fetch()`/`parse()` split so
the network half is one call and the parsing half is testable offline.
Until step 5 saves a real fixture per board, this file pins the shape:
every module loads through the real registry, carries the declarations
the pipeline and the CLI read, and imports its HTTP from
`jobtriage.plugin` rather than its own urllib boilerplate.
"""

from importlib import import_module
from inspect import Parameter, signature
from pathlib import Path
from pkgutil import iter_modules

import pytest

from jobtriage import config, registry, scrapers
from jobtriage.plugin import API_VERSION

# The per-board declarations from plan 04 step 4's table. This is a
# contract, not a detail: the alias is what `-s` accepts and the title
# is what a listing shows.
DECLARED = {
	'bruntwork': ('bw', 'BruntWork', 'https://www.bruntworkcareers.co', 0.5),
	'dynamitejobs': ('dj', 'Dynamite Jobs', 'https://dynamitejobs.com', 0),
	'euremotejobs': ('erj', 'EuroRemoteJobs', 'https://euremotejobs.com', 1.0),
	'eurotechjobs': ('etj', 'EuroTechJobs', 'https://www.eurotechjobs.com', 0),
	'inclusivelyremote': ('ir', 'Inclusively Remote', 'https://inclusivelyremote.com', 0),
	'jobgether': ('jg', 'Jobgether', 'https://jobgether.com', 0.5),
	'justremote': ('jr', 'JustRemote', 'https://justremote.co', 0),
	'remocate': ('rc', 'Remocate', 'https://www.remocate.app', 0.5),
	'remotefrontendjobs': (
		'rfj',
		'Remote Frontend Jobs',
		'https://www.remotefrontendjobs.com',
		0.5,
	),
	'remoteok': ('rok', 'Remote OK', 'https://remoteok.com', 0),
	'remotesource': ('rs', 'Remote Source', 'https://www.remotesource.com', 0),
	'remotive': ('rm', 'Remotive', 'https://remotive.com', 1.0),
	'wellfound': ('wf', 'Wellfound', 'https://wellfound.com', 1.0),
	'weworkremotely': ('wwr', 'We Work Remotely', 'https://weworkremotely.com', 0.5),
	'workingnomads': ('wn', 'Working Nomads', 'https://www.workingnomads.com', 0),
}

MODULES = {name: import_module(f'{scrapers.__name__}.{name}') for name in DECLARED}


@pytest.fixture
def clean_registry(ws, monkeypatch):
	"""The real fifteen and nothing else: no packages, no op plugins."""
	monkeypatch.setattr(registry, 'entry_points', lambda *, group: [])
	return registry.load(ws, config.Config())


def test_all_fifteen_builtins_load_clean(clean_registry):
	"""Before step 4 a real load reported every board broken for a
	missing NAME; after it there is nothing to explain."""
	assert clean_registry.broken == {}
	assert clean_registry.notices == []
	assert {board.name for board in clean_registry.all()} == set(DECLARED)
	assert all(board.origin == 'built-in' for board in clean_registry.all())


def test_every_builtin_declares_its_contract_metadata(clean_registry):
	for board in clean_registry.all():
		alias, title, homepage, delay = DECLARED[board.name]
		assert board.aliases == (alias,), board.name
		assert board.title == title, board.name
		assert board.homepage == homepage, board.name
		assert board.module.POLITE_DELAY == delay, board.name
		assert board.broken == ''
		assert getattr(board.module, 'API_VERSION', API_VERSION) == API_VERSION


@pytest.mark.parametrize('name', sorted(DECLARED))
def test_every_builtin_splits_network_from_parsing(name):
	module = MODULES[name]
	assert callable(module.fetch), f'{name} has no fetch()'
	assert callable(module.parse), f'{name} has no parse()'


@pytest.mark.parametrize('name', sorted(DECLARED))
def test_parse_takes_the_uniform_keywords(name):
	"""`parse(raw, *, locations=None, limit=50)` is the seam step 5's
	offline fixtures call; `recent_jobs` keeps the plugin contract's
	locations/limit keywords too."""
	parse_params = signature(MODULES[name].parse).parameters
	assert 'locations' in parse_params and 'limit' in parse_params, name
	assert parse_params['locations'].kind is Parameter.KEYWORD_ONLY, name
	assert parse_params['limit'].kind is Parameter.KEYWORD_ONLY, name

	recent_params = signature(MODULES[name].recent_jobs).parameters
	assert 'locations' in recent_params and 'limit' in recent_params, name


@pytest.mark.parametrize('name', sorted(DECLARED))
def test_the_http_helper_is_the_only_http_a_builtin_uses(name):
	"""A board still on urllib.request would silently miss the browser
	UA, the timeout, the retry and POLITE_DELAY — the things step 4
	moved into jobtriage.plugin.http."""
	source = import_module(f'{scrapers.__name__}.{name}').__file__
	text = Path(source).read_text(encoding='utf-8')
	assert 'urllib.request' not in text, name
	assert 'from jobtriage.plugin import' in text, name


def test_the_package_no_longer_keeps_a_hand_written_board_list():
	assert not hasattr(scrapers, 'SOURCES')


def test_the_registry_still_discovers_the_same_fifteen_modules():
	"""The package's own module list is the source of truth now: a new
	file here is a new board, and this assertion moves with it."""
	found = sorted(
		info.name for info in iter_modules(scrapers.__path__) if not info.name.startswith('_')
	)
	assert found == sorted(DECLARED)
