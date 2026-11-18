"""Tests for the scraper registry: discovery from the three sources,
override and broken rules, aliases, and the validated `run` wrapper.

Every test builds its boards in a tmp op folder. The autouse fixture
empties the installed environment first — no real built-ins, no
installed board packs, no developer op folder — so a test's board set is
exactly what it wrote. Built-ins and entry points are faked with the
same seam `registry` uses in production: `iter_modules`,
`import_module` and `entry_points`.
"""

import types
from pkgutil import iter_modules as real_iter_modules

import pytest

from jobtriage import config, registry

GOOD_JOB = """[{
	'title': 'Software Engineer',
	'company': 'Acme',
	'url': 'https://alpha.test/jobs/1',
	'published': '2026-11-10',
	'locations': ('Worldwide',),
	'salary': None,
}]"""

SLOW = """
NAME = 'slow'
POLITE_DELAY = 0.25

from jobtriage.plugin import http


def recent_jobs(*, locations=None, limit=50, **options):
	http.get_text('https://slow.test/1')
	http.get_text('https://slow.test/2')
	return []
"""

SPY = """
NAME = 'spy'
seen = []


def recent_jobs(*, locations=None, limit=50, **options):
	seen.append({'locations': locations, 'limit': limit, 'options': options})
	return []
"""


@pytest.fixture(autouse=True)
def no_installed_boards(monkeypatch):
	"""Start from nothing: a test installs only the fake boards it wants."""
	monkeypatch.setattr(registry, 'iter_modules', lambda paths: [])
	monkeypatch.setattr(registry, 'entry_points', lambda *, group: [])


def plugin_source(
	name='alpha',
	*,
	aliases=None,
	title=None,
	homepage=None,
	terms=None,
	api_version=None,
	polite_delay=None,
	jobs=GOOD_JOB,
	body=None,
):
	"""A workspace plugin file, with every optional declaration spelled."""
	head = []
	if name is not None:
		head.append(f'NAME = {name!r}')
	if aliases is not None:
		head.append(f'ALIASES = {aliases!r}')
	if title is not None:
		head.append(f'TITLE = {title!r}')
	if homepage is not None:
		head.append(f'HOMEPAGE = {homepage!r}')
	if terms is not None:
		head.append(f'TERMS = {terms!r}')
	if api_version is not None:
		head.append(f'API_VERSION = {api_version!r}')
	if polite_delay is not None:
		head.append(f'POLITE_DELAY = {polite_delay!r}')
	body = f'\treturn {jobs.strip()}' if body is None else body
	head.append(f'\ndef recent_jobs(*, locations=None, limit=50, **options):\n{body}\n')
	return '\n'.join(head)


def write(ws, source, stem='alpha'):
	"""Drop one plugin file in the op folder's scrapers/."""
	folder = ws.scrapers
	folder.mkdir(parents=True, exist_ok=True)
	path = folder / f'{stem}.py'
	path.write_text(source)
	return path


def load(ws, cfg=None):
	return registry.load(ws, cfg or config.Config())


def fake_module(name, jobs=None):
	"""A module honouring the contract, for the built-in/package fakes."""
	module = types.ModuleType(f'fake.{name}')
	module.NAME = name

	def recent_jobs(**options):
		return list(jobs or [])

	module.recent_jobs = recent_jobs
	return module


def builtin(monkeypatch, module):
	"""Make the built-in scan find exactly `module`."""
	monkeypatch.setattr(
		registry, 'iter_modules', lambda paths: [types.SimpleNamespace(name=module.NAME)]
	)
	monkeypatch.setattr(registry, 'import_module', lambda fullname: module)


class EntryPoint:
	"""The slice of importlib.metadata.EntryPoint the registry reads."""

	def __init__(self, name, module=None, error=None):
		self.name = name
		self._module = module
		self._error = error

	def load(self):
		if self._error is not None:
			raise self._error
		return self._module


class FakeResponse:
	def __init__(self, body):
		self.body = body.encode('utf-8')

	def read(self):
		return self.body

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


# -- discovery -------------------------------------------------------------


def test_a_workspace_plugin_is_discovered_with_all_its_declarations(ws):
	write(
		ws,
		plugin_source(
			aliases=('al',),
			title='Alpha Jobs',
			homepage='https://alpha.test',
			terms='Link back to the canonical URL.',
		),
	)
	reg = load(ws)
	board = reg.by_alias('alpha')

	assert board is not None
	assert board.name == 'alpha'
	assert board.origin == 'workspace'
	assert board.aliases == ('al',)
	assert board.title == 'Alpha Jobs'
	assert board.homepage == 'https://alpha.test'
	assert board.terms == 'Link back to the canonical URL.'
	assert board.broken == ''
	assert reg.by_alias('al') is board
	assert reg.all() == [board]
	assert reg.enabled(config.Config()) == [board]
	assert board.module.__name__ == 'jobtriage_workspace_plugins.alpha'


def test_optional_declarations_have_defaults(ws):
	write(ws, plugin_source(name='remote-frontend-jobs'))
	board = load(ws).by_alias('remote-frontend-jobs')

	assert board.title == 'Remote Frontend Jobs'
	assert board.aliases == ()
	assert board.homepage == ''
	assert board.terms == ''


def test_builtins_are_discovered_by_pkgutil_not_a_hand_written_list(ws, monkeypatch):
	"""The real package scan finds the fifteen modules. The import is
	faked so this stays a discovery test, independent of what each
	board's module does at import time."""
	seen = []
	monkeypatch.setattr(registry, 'iter_modules', real_iter_modules)

	def fake_import(fullname):
		seen.append(fullname)
		return fake_module(fullname.rsplit('.', 1)[-1])

	monkeypatch.setattr(registry, 'import_module', fake_import)
	reg = load(ws)

	assert len(seen) == 15
	assert all(name.startswith('jobtriage.scrapers.') for name in seen)
	assert {'remotive', 'wellfound', 'bruntwork'} <= {board.name for board in reg.all()}


def test_installed_packages_are_discovered_through_the_entry_point_group(ws, monkeypatch):
	module = fake_module('packboard')
	groups = []

	def fake_entry_points(*, group):
		groups.append(group)
		return [EntryPoint('packboard', module)]

	monkeypatch.setattr(registry, 'entry_points', fake_entry_points)
	reg = load(ws)
	board = reg.by_alias('packboard')

	assert groups == ['jobtriage.scrapers']
	assert board is not None
	assert board.origin == 'package'
	assert board.module is module


def test_an_entry_point_that_cannot_load_is_broken(ws, monkeypatch):
	monkeypatch.setattr(
		registry,
		'entry_points',
		lambda *, group: [EntryPoint('ghost', error=ImportError('no such package'))],
	)
	reason = load(ws).broken['ghost']

	assert 'import failed: ImportError' in reason
	assert 'no such package' in reason


def test_plugins_are_never_loaded_from_the_current_directory(ws, tmp_path, monkeypatch):
	elsewhere = tmp_path / 'elsewhere'
	(elsewhere / 'scrapers').mkdir(parents=True)
	(elsewhere / 'scrapers' / 'evil.py').write_text(plugin_source(name='evil'))
	monkeypatch.chdir(elsewhere)

	reg = load(ws)

	assert reg.by_alias('evil') is None
	assert reg.broken == {}


# -- broken plugins --------------------------------------------------------


def test_a_syntax_error_marks_the_file_broken_and_the_run_goes_on(ws):
	write(ws, "NAME = 'bad'\ndef recent_jobs(:\n", stem='bad')
	reg = load(ws)

	assert 'bad' in reg.broken
	assert 'import failed' in reg.broken['bad']
	assert 'bad' not in reg.boards
	assert reg.enabled(config.Config()) == []


def test_a_broken_file_does_not_hide_the_good_ones(ws):
	write(ws, 'NAME = "bad"\ndef recent_jobs(:\n', stem='aa')
	write(ws, plugin_source(), stem='zz')
	reg = load(ws)

	assert 'aa' in reg.broken
	assert reg.by_alias('alpha') is not None


def test_a_plugin_without_a_name_is_broken_under_its_file_stem(ws):
	write(ws, 'def recent_jobs(**options):\n\treturn []\n', stem='nameless')
	reg = load(ws)

	assert 'missing NAME' in reg.broken['nameless']
	assert reg.by_alias('nameless') is None


def test_a_name_must_be_a_slug(ws):
	write(ws, plugin_source(name='Alpha Jobs'))
	reg = load(ws)

	assert 'must match [a-z0-9-]+' in reg.broken['alpha']


def test_a_plugin_without_recent_jobs_is_broken_but_still_listed(ws):
	write(ws, "NAME = 'inert'\n")
	reg = load(ws)

	board = reg.by_alias('inert')
	assert board is not None
	assert 'missing recent_jobs' in board.broken
	assert reg.broken['inert'] == board.broken
	assert reg.enabled(config.Config()) == []
	with pytest.raises(registry.BrokenBoard, match='missing recent_jobs'):
		reg.run('inert')


def test_a_non_callable_recent_jobs_is_broken(ws):
	write(ws, "NAME = 'inert'\nrecent_jobs = 3\n")
	assert 'not callable' in load(ws).broken['inert']


def test_a_newer_api_version_is_refused(ws):
	write(ws, plugin_source(api_version=2))
	assert 'targets plugin API 2' in load(ws).broken['alpha']


def test_files_starting_with_an_underscore_are_not_boards(ws):
	write(ws, plugin_source(name='example'), stem='_example')
	reg = load(ws)

	assert reg.all() == []
	assert reg.broken == {}


# -- overrides and aliases -------------------------------------------------


def test_a_workspace_plugin_overrides_a_builtin_with_a_notice(ws, monkeypatch):
	builtin(monkeypatch, fake_module('alpha'))
	write(ws, plugin_source())

	reg = load(ws)

	assert reg.boards['alpha'].origin == 'workspace'
	assert reg.notices == ['using workspace scraper "alpha" instead of the built-in']


def test_a_broken_workspace_override_replaces_the_builtin(ws, monkeypatch):
	builtin(monkeypatch, fake_module('alpha'))
	write(ws, "NAME = 'alpha'\n")

	reg = load(ws)

	assert reg.boards['alpha'].origin == 'workspace'
	assert 'missing recent_jobs' in reg.boards['alpha'].broken
	assert reg.enabled(config.Config()) == []
	assert reg.notices == ['using workspace scraper "alpha" instead of the built-in']


def test_a_later_workspace_file_with_the_same_name_wins_with_a_notice(ws):
	write(ws, plugin_source(name='alpha', title='First'), stem='a')
	write(ws, plugin_source(name='alpha', title='Second'), stem='b')

	reg = load(ws)

	assert reg.boards['alpha'].title == 'Second'
	assert reg.notices == ['using workspace scraper "alpha" instead of an earlier workspace scraper']


def test_an_alias_clash_marks_the_later_board_broken(ws):
	write(ws, plugin_source(name='aaa', aliases=('x',)), stem='a')
	write(ws, plugin_source(name='bbb', aliases=('x',)), stem='b')

	reg = load(ws)

	assert reg.boards['bbb'].broken == 'alias "x" is already used by "aaa"'
	assert reg.by_alias('x').name == 'aaa'
	assert [board.name for board in reg.enabled(config.Config())] == ['aaa']


def test_an_alias_clash_across_sources_marks_the_later_board_broken(ws, monkeypatch):
	module = fake_module('alpha')
	module.ALIASES = ('x',)
	builtin(monkeypatch, module)
	write(ws, plugin_source(name='bbb', aliases=('x',)), stem='b')

	reg = load(ws)

	assert reg.boards['bbb'].broken == 'alias "x" is already used by "alpha"'
	assert reg.by_alias('x').name == 'alpha'


def test_a_name_that_is_another_boards_alias_is_broken(ws):
	write(ws, plugin_source(name='aaa', aliases=('bbb',)), stem='a')
	write(ws, plugin_source(name='bbb'), stem='b')

	reg = load(ws)

	assert reg.boards['bbb'].broken == 'NAME "bbb" is already an alias of "aaa"'


def test_an_alias_equal_to_the_boards_own_name_is_dropped(ws):
	write(ws, plugin_source(name='aaa', aliases=('aaa', 'aa')))
	assert load(ws).boards['aaa'].aliases == ('aa',)


# -- enablement and lookup -------------------------------------------------


def test_disabled_boards_are_dropped_from_enabled(ws):
	write(ws, plugin_source())
	reg = load(ws)
	cfg = config.Config(sources=config.SourcesSettings(disabled=('alpha',)))

	assert reg.enabled(cfg) == []
	assert reg.enabled(config.Config()) != []


def test_a_disabled_alias_drops_the_canonical_board(ws):
	"""`sources disable` stores the long name, but a hand-written
	`disabled = ["al"]` must mean the same board (step 8 canonicalises)."""
	write(ws, plugin_source(aliases=('al',)))
	reg = load(ws)
	cfg = config.Config(sources=config.SourcesSettings(disabled=('al',)))

	assert reg.disabled(cfg) == {'alpha'}
	assert reg.enabled(cfg) == []


def test_an_unknown_board_raises(ws):
	with pytest.raises(registry.UnknownBoard, match='ghost'):
		load(ws).run('ghost')


# -- run: validation, tagging, stats --------------------------------------


def test_run_tags_each_job_and_reports_the_numbers(ws):
	write(ws, plugin_source())
	jobs, stats = load(ws).run('alpha', locations=('Worldwide',), limit=5)

	assert len(jobs) == 1
	assert jobs[0]['source'] == 'alpha'
	assert jobs[0]['title'] == 'Software Engineer'
	assert jobs[0]['locations'] == ['Worldwide']  # a tuple is normalised
	assert stats.fetched == 1
	assert stats.valid == 1
	assert stats.dropped_invalid == 0
	assert stats.duration >= 0
	assert stats.error == ''


def test_run_passes_locations_limit_and_the_board_options(ws):
	write(ws, SPY, stem='spy')
	reg = load(ws)

	reg.run('spy', locations=('Worldwide',), limit=3, category='dev')

	assert reg.by_alias('spy').module.seen == [
		{'locations': ('Worldwide',), 'limit': 3, 'options': {'category': 'dev'}}
	]


def test_every_bad_row_is_dropped_counted_and_explained(ws):
	jobs = """
[
	{
		'title': 'Software Engineer',
		'company': 'Acme',
		'url': 'https://alpha.test/jobs/1',
		'published': '2026-11-10',
		'locations': ['Worldwide'],
		'salary': None,
	},
	{
		'title': 'Missing salary',
		'company': 'Acme',
		'url': 'https://alpha.test/jobs/2',
		'published': '2026-11-10',
		'locations': [],
	},
	{
		'title': '   ',
		'company': 'Acme',
		'url': 'https://alpha.test/jobs/3',
		'published': None,
		'locations': [],
		'salary': None,
	},
	{
		'title': 'Relative URL',
		'company': 'Acme',
		'url': '/jobs/4',
		'published': None,
		'locations': [],
		'salary': None,
	},
	{
		'title': 'Relative date',
		'company': 'Acme',
		'url': 'https://alpha.test/jobs/5',
		'published': '2 days ago',
		'locations': [],
		'salary': None,
	},
	{
		'title': 'String tags',
		'company': 'Acme',
		'url': 'https://alpha.test/jobs/6',
		'published': None,
		'locations': 'Worldwide',
		'salary': None,
	},
	{
		'title': 'Salary number',
		'company': 'Acme',
		'url': 'https://alpha.test/jobs/7',
		'published': None,
		'locations': [],
		'salary': 10,
	},
	'oops',
]
"""
	write(ws, plugin_source(jobs=jobs))

	jobs_out, stats = load(ws).run('alpha')
	problems = '\n'.join(stats.problems)

	assert [job['title'] for job in jobs_out] == ['Software Engineer']
	assert stats.fetched == 8
	assert stats.valid == 1
	assert stats.dropped_invalid == 7
	assert 'job 2: missing "salary"' in problems
	assert 'job 3: title is empty' in problems
	assert "job 4: url '/jobs/4' is not an absolute http(s) URL" in problems
	assert "job 5: published '2 days ago' is not ISO 8601" in problems
	assert 'job 6: locations is str, expected a list of strings' in problems
	assert 'job 7: salary is int, expected a string or None' in problems
	assert 'job 8: not a job table (str)' in problems


def test_the_limit_truncates_valid_rows_after_validation(ws):
	many = (
		'['
		+ ', '.join(
			f"{{'title': 'Job {index}', 'company': 'Acme', "
			f"'url': 'https://alpha.test/{index}', 'published': None, "
			f"'locations': [], 'salary': None}}"
			for index in range(4)
		)
		+ ']'
	)
	write(ws, plugin_source(jobs=many))

	jobs, stats = load(ws).run('alpha', limit=2)

	assert [job['title'] for job in jobs] == ['Job 0', 'Job 1']
	assert (stats.fetched, stats.valid, stats.dropped_invalid) == (4, 2, 0)


def test_a_board_exception_is_isolated_in_the_stats(ws):
	write(
		ws,
		"NAME = 'boom'\n\n\ndef recent_jobs(**options):\n\traise RuntimeError('rate limited')\n",
		stem='boom',
	)

	jobs, stats = load(ws).run('boom')

	assert jobs == []
	assert 'rate limited' in stats.error
	assert stats.fetched == 0
	assert stats.duration >= 0


def test_a_board_returning_a_non_list_is_an_error(ws):
	write(ws, plugin_source(body='\treturn {"not": "a list"}'))

	jobs, stats = load(ws).run('alpha')

	assert jobs == []
	assert 'expected a list' in stats.error


def test_polite_delay_from_the_module_reaches_the_http_helper(ws, monkeypatch):
	"""The registry owns the ambient delay: a plugin that just calls
	http.get_text gets its POLITE_DELAY between requests, without
	threading the number through its own code."""
	write(ws, SLOW, stem='slow')
	state = {'now': 0.0, 'sleeps': []}
	monkeypatch.setattr(registry.http, 'urlopen', lambda request, timeout=None: FakeResponse('ok'))
	monkeypatch.setattr(registry.http, 'monotonic', lambda: state['now'])

	def sleep(seconds):
		state['sleeps'].append(seconds)
		state['now'] += seconds

	monkeypatch.setattr(registry.http, 'sleep', sleep)

	load(ws).run('slow')

	assert state['sleeps'] == [0.25]


def test_the_validation_keys_are_the_plugin_contract():
	from jobtriage.plugin import Job

	assert set(registry.JOB_KEYS) == set(Job.__required_keys__)


def test_load_applies_the_configured_user_agent(ws):
	"""`[network] user_agent` reaches the http layer on every path that
	runs a board, because registry.load is that path's one door."""
	try:
		load(ws, config.Config(network=config.NetworkSettings(user_agent='jobtriage/1.0 (+url)')))
		assert registry.http.user_agent() == 'jobtriage/1.0 (+url)'
	finally:
		registry.http.set_user_agent('')
