"""Tests for `jobtriage sources` (plan 04 steps 7-8): the inventory, one
board's info, the template scaffold, a connector test and the two config
edits.

Nothing here touches a network. Built-ins are faked through the two
registry seams (`iter_modules` and `import_module`); workspace plugins
return literals or raise, so `sources test` goes through the real
`registry.run` — validation, limit and timing included — without a
request. `sources new` uses the real shipped template: that is the file
this suite is meant to keep honest.
"""

import json
import os
import re
import types

import pytest

from jobtriage import config, registry, store, workspace
from jobtriage.cli.main import main

# -- fixtures and fakes ----------------------------------------------------


@pytest.fixture(autouse=True)
def no_pointer(monkeypatch, tmp_path):
	"""Never find a real workspace through the platform pointer file."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'no-pointer')
	monkeypatch.delenv('JOBTRIAGE_HOME', raising=False)


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch):
	"""A developer's JOBTRIAGE_* shell must not change what these tests see."""
	for name in list(os.environ):
		if name.startswith('JOBTRIAGE_') and name != 'JOBTRIAGE_HOME':
			monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_installed_boards(monkeypatch):
	"""Only the boards a test installs or writes exist."""
	monkeypatch.setattr(registry, 'iter_modules', lambda paths: [])
	monkeypatch.setattr(registry, 'entry_points', lambda *, group: [])


def fake_board(name, jobs=None, *, aliases=(), doc='A fake board.', **declarations):
	"""A fake built-in plugin, contract-shaped, for the registry seam."""
	module = types.ModuleType(f'jobtriage.scrapers.{name}')
	module.__name__ = f'jobtriage.scrapers.{name}'
	module.__doc__ = doc
	module.NAME = name
	module.ALIASES = aliases
	module.WORLDWIDE = ('Worldwide',)
	module.COUNTRY_STYLE = 'none'
	module.FAMILIES = {'software': {}}
	for attribute, value in declarations.items():
		setattr(module, attribute, value)

	def recent_jobs(locations=None, limit=50, **options):
		return [dict(job) for job in (jobs or [])][:limit]

	module.recent_jobs = recent_jobs
	return module


def install(monkeypatch, *modules):
	"""Make the registry's built-in scan find exactly these fakes."""
	by_name = {module.NAME: module for module in modules}
	monkeypatch.setattr(
		registry,
		'iter_modules',
		lambda paths: [types.SimpleNamespace(name=name) for name in by_name],
	)
	monkeypatch.setattr(
		registry, 'import_module', lambda fullname: by_name[fullname.rsplit('.', 1)[-1]]
	)


def plugin(ws, source, stem='alpha'):
	"""Write one workspace plugin file.

	UTF-8 explicitly: Python reads source as UTF-8 on every platform,
	while `write_text` alone would use the locale codec — cp1252 on
	Windows, whose em-dash byte breaks the import and turns the board
	into an 'unknown board' usage error.
	"""
	ws.scrapers.mkdir(parents=True, exist_ok=True)
	path = ws.scrapers / f'{stem}.py'
	path.write_text(source, encoding='utf-8')
	return path


def plugin_source(name='alpha', *, aliases=(), title=None, body=None):
	"""A plugin file with the declarations a test names; `[]` by default."""
	head = [f'NAME = {name!r}']
	if aliases:
		head.append(f'ALIASES = {aliases!r}')
	if title is not None:
		head.append(f'TITLE = {title!r}')
	body = '\treturn []' if body is None else body
	head.append(f'\ndef recent_jobs(*, locations=None, limit=50, **options):\n{body}')
	return '\n'.join(head) + '\n'


GOOD_JOB = """
	return [{
		'title': 'Software Engineer',
		'company': 'Acme',
		'url': 'https://alpha.test/jobs/1',
		'published': '2026-11-10',
		'locations': ['Worldwide'],
		'salary': None,
	}]"""


def record(ws, source='alpha', **fields):
	"""One `source_runs` row in the shape the pipeline writes."""
	values = {
		'started_at': '2026-11-01T08:00:00+00:00',
		'duration_ms': 120,
		'ok': True,
		'fetched': 1,
		'dropped_invalid': 0,
		'error': None,
	}
	values.update(fields)
	store.record_run(ws.db, source, **values)


def call(ws, *args):
	"""Run the CLI against this workspace."""
	return main(['--workspace', str(ws.root), *args])


# -- the inventory ---------------------------------------------------------


def test_the_listing_has_a_row_per_board_with_origin_and_config_state(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha', aliases=('al',)))
	plugin(ws, plugin_source(name='beta', title='Beta Jobs'))
	ws.config.write_text('[sources]\ndisabled = ["al"]\n')

	assert call(ws, 'sources') == 0
	out = capsys.readouterr().out

	assert re.search(r'^alpha\s+al\s+built-in\s+no\s', out, re.MULTILINE)
	assert re.search(r'^beta\s+-\s+workspace\s+yes\s', out, re.MULTILINE)
	assert out.startswith('NAME')
	# the alias in the config disabled the canonical board, without an edit
	assert 'alpha' in out


def test_the_listing_carries_health_and_the_reason_a_board_is_broken(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha'))
	record(ws, started_at='2026-11-01T08:00:00+00:00')
	record(ws, started_at='2026-11-02T08:00:00+00:00')
	record(
		ws,
		started_at='2026-11-03T08:00:00+00:00',
		ok=False,
		fetched=0,
		error='HttpError: HTTP 403 for https://alpha.test/jobs',
	)
	plugin(ws, "NAME = 'broken'\n", stem='broken')
	plugin(ws, 'def recent_jobs(:\n', stem='bad')

	assert call(ws, 'sources') == 0
	out = capsys.readouterr().out

	assert re.search(r'^alpha\s+-\s+built-in\s+yes\s+2026-11-03 08:00\s+2/3$', out, re.MULTILINE)
	assert 'last error: alpha: HttpError: HTTP 403 for https://alpha.test/jobs' in out
	assert 'broken: broken: missing recent_jobs' in out
	assert 'broken: bad: import failed' in out
	assert re.search(r'^bad\s+-\s+-\s+no\s+-\s+-$', out, re.MULTILINE)


def test_the_json_listing_is_boards_plus_the_broken_map(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha', aliases=('al',), HOMEPAGE='https://alpha.test'))
	record(ws, ok=False, error='RuntimeError: layout changed')
	plugin(ws, 'def recent_jobs(:\n', stem='bad')
	plugin(ws, "NAME = 'inert'\n", stem='inert')
	ws.config.write_text('[sources]\ndisabled = ["alpha"]\n')

	assert call(ws, 'sources', '--json') == 0
	document = json.loads(capsys.readouterr().out)

	assert set(document) == {'boards', 'broken'}
	alpha = document['boards'][0]
	assert alpha == {
		'name': 'alpha',
		'title': 'Alpha',
		'aliases': ['al'],
		'origin': 'built-in',
		'enabled': False,
		'broken': '',
		'homepage': 'https://alpha.test',
		'terms': '',
		'health': {
			'runs': 1,
			'failures': 1,
			'streak': 1,
			'success_rate': 0.0,
			'last_run': '2026-11-01T08:00:00+00:00',
			'last_ok': False,
			'last_error': 'RuntimeError: layout changed',
			'last_duration_ms': 120,
			'last_fetched': 1,
			'fetched': 1,
			'dropped_invalid': 0,
		},
	}
	assert 'bad' in document['broken']
	assert 'import failed' in document['broken']['bad']
	inert = document['boards'][1]
	assert inert['name'] == 'inert'
	assert inert['enabled'] is False
	assert inert['broken'].startswith('missing recent_jobs')
	assert document['broken']['inert'] == inert['broken']


def test_an_unknown_disabled_name_is_a_warning_not_a_failure(ws, capsys):
	ws.config.write_text('[sources]\ndisabled = ["ghost"]\n')

	assert call(ws, 'sources') == 0
	captured = capsys.readouterr()

	assert "sources.disabled lists 'ghost'" in captured.err
	assert 'sources enable ghost' in captured.err


def test_listing_boards_does_not_create_a_database(ws, capsys):
	assert call(ws, 'sources') == 0
	capsys.readouterr()
	assert not ws.db.exists()


def test_a_broken_config_stops_the_listing_with_one_line(ws, capsys):
	ws.config.write_text('[sources\n')

	assert call(ws, 'sources') == 1
	err = capsys.readouterr().err
	assert err.startswith('error:')
	assert 'Traceback' not in err


# -- info ------------------------------------------------------------------


INFO_PLUGIN = '''
"""Alpha Jobs — a board for testing `sources info`.

It paginates with limit/offset and filters location client-side.
"""

NAME = 'alpha'
ALIASES = ('al',)
TITLE = 'Alpha Jobs'
HOMEPAGE = 'https://alpha.test'
TERMS = 'Link back to the canonical URL.'
POLITE_DELAY = 2.0
WORLDWIDE = ('Worldwide',)
REGIONS = {'emea': ('Europe', 'EMEA')}
COUNTRY_STYLE = 'iso2'
COUNTRY_ALIASES = {'DE': 'DE'}
COVERS = ('europe',)
FAMILIES = {'software': {'category': 'dev'}}


def server_locations(prefs):
	return {}


def recent_jobs(*, max_pages=2, locations=None, limit=50, **options):
	return []
'''


def test_info_prints_the_docstring_terms_vocabulary_and_signature(ws, capsys):
	plugin(ws, INFO_PLUGIN)

	assert call(ws, 'sources', 'info', 'al') == 0  # an alias resolves to the board
	out = capsys.readouterr().out

	assert out.startswith('alpha — Alpha Jobs (workspace)')
	assert 'It paginates with limit/offset' in out
	assert 'aliases: al' in out
	assert 'homepage: https://alpha.test' in out
	assert 'terms: Link back to the canonical URL.' in out
	assert 'API version: 1' in out
	assert 'polite delay: 2s' in out
	assert 'locations worldwide: Worldwide' in out
	assert 'locations regions: emea (2 tags)' in out
	assert 'locations countries: ISO-2 codes; 1 worded by the board' in out
	assert 'locations covers: europe' in out
	assert "families: software → category='dev'" in out
	assert 'server_locations: yes' in out
	assert '[sources.alpha] is where you set them' in out
	assert 'max_pages' in out and 'default 2' in out
	assert 'locations' in out and 'the run supplies it' in out
	assert '**options' in out


def test_info_json_is_the_prose_views_data(ws, capsys):
	plugin(ws, INFO_PLUGIN)

	assert call(ws, 'sources', 'info', 'al', '--json') == 0
	document = json.loads(capsys.readouterr().out)

	assert document['name'] == 'alpha'
	assert document['title'] == 'Alpha Jobs'
	assert document['aliases'] == ['al']
	assert document['origin'] == 'workspace'
	assert document['homepage'] == 'https://alpha.test'
	assert document['terms'] == 'Link back to the canonical URL.'
	assert document['api_version'] == 1
	assert document['polite_delay'] == 2.0
	assert document['broken'] == ''
	assert 'It paginates with limit/offset' in document['docstring']
	assert document['locations'] == {
		'worldwide': ['Worldwide'],
		'regions': {'emea': 2},
		'country_style': 'iso2',
		'country_aliases': {'DE': 'DE'},
		'covers': ['europe'],
	}
	assert document['families'] == {'software': {'category': 'dev'}}
	assert document['server_locations'] is True

	options = {entry['name']: entry for entry in document['options']}
	assert options['max_pages']['default'] == 2
	assert options['max_pages']['required'] is False
	assert options['locations']['supplied_by_run'] is True
	assert options['options']['kind'] == 'var_keyword'
	assert '[sources.alpha] keys arrive here' in options['options']['note']


def test_info_names_a_broken_board_and_exits_1(ws, capsys):
	plugin(ws, "NAME = 'broken'\n")

	assert call(ws, 'sources', 'info', 'broken') == 1
	out = capsys.readouterr().out
	assert 'broken: missing recent_jobs' in out


def test_info_unknown_is_a_usage_error(ws, capsys):
	assert call(ws, 'sources', 'info', 'ghost') == 2
	err = capsys.readouterr().err
	assert "unknown board 'ghost'" in err
	assert 'Traceback' not in err


# -- new -------------------------------------------------------------------


HOOKS = (
	'NAME =',
	'ALIASES =',
	'TITLE =',
	'HOMEPAGE =',
	'API_VERSION =',
	'WORLDWIDE =',
	'REGIONS =',
	'COUNTRY_STYLE =',
	'COUNTRY_ALIASES =',
	'COVERS =',
	'FAMILIES =',
	'TERMS =',
	'POLITE_DELAY =',
	'def server_locations(',
	'def recent_jobs(',
)


def test_new_copies_the_template_with_the_name_filled_in(ws, monkeypatch, capsys):
	# No installed boards: the registry is only asked whether the name
	# already means something.

	assert call(ws, 'sources', 'new', 'demo') == 0
	out = capsys.readouterr().out
	path = ws.scrapers / 'demo.py'

	assert path.is_file()
	assert 'created scrapers/demo.py' in out
	assert 'jobtriage sources test demo' in out
	text = path.read_text()
	assert text.count("NAME = 'demo'") == 1
	assert "NAME = 'example-board'" not in text  # the NAME line was filled in
	for hook in HOOKS:
		assert hook in text

	# and the result is a loadable board: the template imports, it does
	# not run, so the placeholder API is never called here
	reg = registry.load(ws, config.Config())
	board = reg.by_alias('demo')
	assert board is not None
	assert board.origin == 'workspace'
	assert board.broken == ''
	assert board.title == 'Example Board'


def test_new_refuses_bad_names_without_writing(ws, capsys):
	for name in ('all', 'All', 'two words', '_private', 'a/b'):
		assert call(ws, 'sources', 'new', name) == 2
		assert 'error:' in capsys.readouterr().err
	assert not ws.scrapers.exists() or not list(ws.scrapers.glob('*.py'))


def test_new_never_overwrites_a_file(ws, capsys):
	existing = plugin(ws, '# mine\n', stem='demo')

	assert call(ws, 'sources', 'new', 'demo') == 1
	assert 'already exists' in capsys.readouterr().err
	assert existing.read_text() == '# mine\n'


def test_new_refuses_another_boards_alias(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha', aliases=('al',)))

	assert call(ws, 'sources', 'new', 'al') == 2
	assert 'is an alias of' in capsys.readouterr().err


def test_new_refuses_a_name_another_workspace_file_already_uses(ws, capsys):
	plugin(ws, plugin_source(name='alpha'), stem='other')

	assert call(ws, 'sources', 'new', 'alpha') == 1
	err = capsys.readouterr().err
	assert "the workspace already defines 'alpha' in other.py" in err
	assert not (ws.scrapers / 'alpha.py').exists()


def test_new_of_a_builtin_says_it_will_override(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha'))

	assert call(ws, 'sources', 'new', 'alpha') == 0
	out = capsys.readouterr().out
	assert 'already a built-in board; this file overrides it' in out
	assert (ws.scrapers / 'alpha.py').is_file()


# -- test ------------------------------------------------------------------


def test_test_runs_with_limit_five_and_prints_the_numbers_and_jobs(ws, capsys):
	jobs = (
		'return ['
		+ ', '.join(
			f"{{'title': 'Engineer {index}', 'company': 'Acme', "
			f"'url': 'https://alpha.test/{index}', 'published': '2026-11-10', "
			f"'locations': ['Worldwide'], 'salary': None}}"
			for index in range(6)
		)
		+ ']'
	)
	plugin(ws, plugin_source(body='\n\t' + jobs))

	assert call(ws, 'sources', 'test', 'alpha') == 0
	out = capsys.readouterr().out

	# six fetched, five survived: registry.run's limit is the command's
	assert re.search(
		r'^alpha \(workspace\) · 6 fetched · 5 valid · 0 dropped · \d+\.\d{2}s$',
		out,
		re.MULTILINE,
	)
	assert out.count('Acme') == 5
	assert 'Engineer 0 — Acme · 2026-11-10 · Worldwide · https://alpha.test/0' in out


def test_test_names_each_contract_problem(ws, capsys):
	body = """
	return [
		{
			'title': 'Good Engineer',
			'company': 'Acme',
			'url': 'https://alpha.test/1',
			'published': '2026-11-10',
			'locations': ['Worldwide'],
			'salary': None,
		},
		{
			'title': 'Bad Date',
			'company': 'Acme',
			'url': 'https://alpha.test/2',
			'published': '2 days ago',
			'locations': ['Worldwide'],
			'salary': None,
		},
	]"""
	plugin(ws, plugin_source(body=body))

	assert call(ws, 'sources', 'test', 'alpha') == 1
	captured = capsys.readouterr()

	assert '2 fetched · 1 valid · 1 dropped' in captured.out
	assert "job 2: published '2 days ago' is not ISO 8601" in captured.out
	assert 'dates.relative()' in captured.out
	assert 'the contract rejected 1 row(s)' in captured.err


def test_test_reports_a_board_that_raises_without_a_traceback(ws, capsys):
	plugin(ws, plugin_source(body="\traise RuntimeError('rate limited')"))

	assert call(ws, 'sources', 'test', 'alpha') == 1
	err = capsys.readouterr().err
	assert 'error: alpha failed after' in err
	assert 'RuntimeError: rate limited' in err
	assert 'Traceback' not in err


def test_test_unknown_and_broken_names(ws, capsys):
	plugin(ws, "NAME = 'broken'\n")

	assert call(ws, 'sources', 'test', 'ghost') == 2
	assert "unknown board 'ghost'" in capsys.readouterr().err

	assert call(ws, 'sources', 'test', 'broken') == 1
	assert 'broken: missing recent_jobs' in capsys.readouterr().err


def test_test_records_nothing_for_health(ws, capsys):
	"""A connector test is not a scrape: `source_runs` must not move."""
	plugin(ws, plugin_source(body=GOOD_JOB))

	assert call(ws, 'sources', 'test', 'alpha') == 0
	assert not ws.db.exists()
	capsys.readouterr()


def test_test_an_explicitly_disabled_board_still_runs_with_a_note(ws, capsys):
	"""Naming the board is the choice, as with `scrape -s`; `--all` is
	the enabled set, and the note keeps the two from looking alike."""
	plugin(ws, plugin_source())
	ws.config.write_text('[sources]\ndisabled = ["alpha"]\n')

	assert call(ws, 'sources', 'test', 'alpha') == 0
	captured = capsys.readouterr()
	assert 'alpha (workspace)' in captured.out
	assert 'is disabled in [sources].disabled; testing it anyway' in captured.err


def test_test_needs_a_name_or_all(ws, capsys):
	assert call(ws, 'sources', 'test') == 2
	assert 'needs a NAME' in capsys.readouterr().err
	assert call(ws, 'sources', 'test', 'alpha', '--all') == 2
	assert 'not both' in capsys.readouterr().err


def test_test_all_runs_every_enabled_board(ws, monkeypatch, capsys):
	install(
		monkeypatch,
		fake_board(
			'alpha',
			[
				{
					'title': 'A',
					'company': 'A',
					'url': 'https://a.test/1',
					'published': None,
					'locations': [],
					'salary': None,
				}
			],
		),
	)
	plugin(ws, plugin_source(name='beta', title='Beta'), stem='beta')
	ws.config.write_text('[sources]\ndisabled = ["beta"]\n')

	assert call(ws, 'sources', 'test', '--all') == 0
	out = capsys.readouterr().out
	assert 'alpha (built-in)' in out
	assert 'beta' not in out  # disabled: --all is the enabled set


def test_test_all_names_broken_boards_and_fails(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha'))
	plugin(ws, "NAME = 'broken'\n", stem='broken')

	assert call(ws, 'sources', 'test', '--all') == 1
	captured = capsys.readouterr()
	assert 'alpha (built-in)' in captured.out
	assert 'broken: broken: missing recent_jobs' in captured.err


def test_test_all_json_is_one_document_for_the_canary(ws, monkeypatch, capsys):
	"""The nightly workflow reads this: per-board ok, error and counts,
	the broken map, and an `ok` that matches the exit code."""
	import json

	install(
		monkeypatch,
		fake_board(
			'alpha',
			[
				{
					'title': 'A',
					'company': 'A',
					'url': 'https://a.test/1',
					'published': None,
					'locations': [],
					'salary': None,
				}
			],
		),
	)
	plugin(ws, "NAME = 'broken'\n", stem='broken')

	assert call(ws, 'sources', 'test', '--all', '--json') == 1
	document = json.loads(capsys.readouterr().out)

	assert document['ok'] is False
	assert document['broken'] == {'broken': 'missing recent_jobs(*, locations, limit, **options)'}
	assert document['boards'][0]['name'] == 'alpha'
	assert document['boards'][0]['ok'] is True
	assert document['boards'][0]['fetched'] == 1
	assert document['boards'][0]['error'] is None
	assert document['boards'][0]['jobs'] == ['https://a.test/1']


def test_test_json_for_one_board_uses_the_same_shape(ws, monkeypatch, capsys):
	import json

	install(monkeypatch, fake_board('alpha', []))

	assert call(ws, 'sources', 'test', 'alpha', '--json') == 0
	document = json.loads(capsys.readouterr().out)
	assert document['ok'] is True
	assert document['boards'][0]['name'] == 'alpha'


# -- enable / disable ------------------------------------------------------


CANONICAL_DISABLE = '# my config\n[sources]\ndisabled = []  # muted boards\n'


def test_disable_canonicalises_an_alias_and_keeps_the_comments(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha', aliases=('al',)))
	ws.config.write_text(CANONICAL_DISABLE)

	assert call(ws, 'sources', 'disable', 'al') == 0
	captured = capsys.readouterr()
	text = ws.config.read_text()

	assert 'note: al is an alias of alpha' in captured.out
	assert 'sources.disabled = ["alpha"]' in captured.out
	assert text.startswith('# my config\n')
	assert 'disabled = ["alpha"]  # muted boards' in text


def test_disable_twice_and_enable_are_idempotent(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha'))
	ws.config.write_text('[sources]\ndisabled = ["alpha"]\n')

	assert call(ws, 'sources', 'disable', 'alpha') == 0
	assert 'alpha: already disabled' in capsys.readouterr().out

	assert call(ws, 'sources', 'enable', 'alpha') == 0
	assert 'sources.disabled = []' in capsys.readouterr().out
	assert 'alpha' not in ws.config.read_text()

	assert call(ws, 'sources', 'enable', 'alpha') == 0
	assert 'alpha: already enabled' in capsys.readouterr().out


def test_disable_canonicalises_an_alias_spelling_already_in_the_list(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha', aliases=('al',)))
	ws.config.write_text('[sources]\ndisabled = ["al", "other"]\n')

	assert call(ws, 'sources', 'disable', 'alpha') == 0
	captured = capsys.readouterr()
	assert 'sources.disabled = ["other", "alpha"]' in captured.out
	assert 'disabled = ["other", "alpha"]' in ws.config.read_text()


def test_enable_removes_every_alias_spelling(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha', aliases=('al',)))
	ws.config.write_text('[sources]\ndisabled = ["al", "other"]\n')

	assert call(ws, 'sources', 'enable', 'alpha') == 0
	assert 'sources.disabled = ["other"]' in capsys.readouterr().out


def test_enabling_a_board_shows_its_terms(ws, monkeypatch, capsys):
	"""euremotejobs ships off because its terms matter; flipping it on is
	the acceptance, so the line arrives with the edit."""
	install(monkeypatch, fake_board('alpha', TERMS='Link back to the canonical URL.'))
	ws.config.write_text('[sources]\ndisabled = ["alpha"]\n')

	assert call(ws, 'sources', 'enable', 'alpha') == 0
	assert 'terms: Link back to the canonical URL.' in capsys.readouterr().out


def test_an_already_enabled_board_does_not_repeat_its_terms(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha', TERMS='Link back.'))
	ws.config.write_text('[sources]\ndisabled = []\n')

	assert call(ws, 'sources', 'enable', 'alpha') == 0
	captured = capsys.readouterr()
	assert 'already enabled' in captured.out
	assert 'terms:' not in captured.out


def test_enable_cleans_a_stale_entry_for_a_name_no_board_has(ws, capsys):
	ws.config.write_text('[sources]\ndisabled = ["ghost"]\n')

	assert call(ws, 'sources', 'enable', 'ghost') == 0
	captured = capsys.readouterr()
	assert "ghost' names no board; removing the stale entry" in captured.out
	assert 'sources.disabled = []' in captured.out


def test_disable_an_unknown_name_is_a_usage_error(ws, capsys):
	before = ws.config.read_text()

	assert call(ws, 'sources', 'disable', 'ghost') == 2
	assert "unknown board 'ghost'" in capsys.readouterr().err
	assert ws.config.read_text() == before


def test_enable_does_not_need_the_database_or_a_network(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('alpha'))
	ws.config.write_text('[sources]\ndisabled = ["alpha"]\n')

	assert call(ws, 'sources', 'enable', 'alpha') == 0
	capsys.readouterr()
	assert not ws.db.exists()  # an edit is not a scrape
