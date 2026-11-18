"""`jobtriage doctor`: every check, the remedies, and the exit code.

The tests are offline by construction: the browser finder, the CDP port
probe and the registry discovery are replaced, so the only things
touched are the tmp workspace and the files the checks read. The one
network-shaped check (`--offline` off) is driven through the module's
`urlopen` seam with a fake response, which also asserts the method is
HEAD.
"""

import os
import types
import urllib.error
from types import SimpleNamespace

import pytest

from jobtriage import db, registry, workspace
from jobtriage.cli import doctor
from jobtriage.cli.main import main


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch):
	"""The config loader reads JOBTRIAGE_*; the developer's shell must not
	change what these tests see."""
	for name in list(os.environ):
		if name.startswith('JOBTRIAGE_') and name != 'JOBTRIAGE_HOME':
			monkeypatch.delenv(name, raising=False)


@pytest.fixture
def op(tmp_path, monkeypatch):
	"""A complete workspace, with everything outside the filesystem faked:
	a found browser, a free port, and no boards at all."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')
	monkeypatch.setattr(registry, 'iter_modules', lambda paths: [])
	monkeypatch.setattr(registry, 'entry_points', lambda *, group: [])
	ws, _notes = workspace.create(tmp_path / 'op')
	monkeypatch.setattr(doctor, 'find_browser', lambda executable='': tmp_path / 'chrome')
	monkeypatch.setattr(doctor, 'port_state', lambda port: 'free')
	return ws


def report(ws, *, offline=True) -> int:
	return doctor.run(SimpleNamespace(offline=offline), ws)


def test_a_fresh_workspace_passes_offline(op, capsys):
	"""The CI acceptance: init --yes, then doctor --offline is green. No
	database and no resume are normal, not failures."""
	assert report(op) == 0
	out = capsys.readouterr().out
	assert 'problem' not in out
	assert 'not checked (--offline)' in out
	assert 'profile/RESUME.md' in out
	assert not op.db.exists()  # doctor looks, it never creates


def test_the_workspace_and_config_paths_are_reported(op, capsys):
	report(op)
	out = capsys.readouterr().out
	assert 'workspace' in out and str(op.root) in out
	assert f'{workspace.CONFIG_NAME} valid' in out


def test_a_broken_config_is_one_problem_and_skips_what_needs_it(op, capsys):
	op.config.write_text('[search]\nlimit = "many"\n')
	assert report(op) == 1
	out = capsys.readouterr().out
	assert 'problem' in out
	assert 'search.limit' in out
	assert 'config edit' in out
	assert 'not checked (the config could not be read)' in out


def test_a_stale_schema_names_the_upgrade_command(op, capsys):
	db.migrate(op.db, op.backups)
	connection = db.connect(op.db)
	connection.execute(f'PRAGMA user_version = {db.LATEST - 1}')
	connection.commit()
	connection.close()

	assert report(op) == 1
	out = capsys.readouterr().out
	assert f'schema {db.LATEST - 1}, this build expects {db.LATEST}' in out
	assert 'init --upgrade' in out


def test_a_current_schema_is_ok_and_backups_are_reported(op, capsys):
	db.migrate(op.db, op.backups)
	report(op)
	out = capsys.readouterr().out
	assert f'schema {db.LATEST}' in out
	assert 'backup' in out  # migrations 2 and 3 each took one


def test_a_database_from_a_newer_build_says_upgrade_jobtriage(op, capsys):
	connection = db.connect(op.db)
	connection.execute(f'PRAGMA user_version = {db.LATEST + 1}')
	connection.commit()
	connection.close()

	assert report(op) == 1
	out = capsys.readouterr().out
	assert f'schema {db.LATEST + 1}' in out
	assert 'upgrade jobtriage' in out


def test_an_edited_skill_file_is_a_problem_with_the_upgrade_remedy(op, capsys):
	skill = op.skills / 'jobtriage' / 'SKILL.md'
	skill.write_text(skill.read_text(encoding='utf-8') + '\nedited\n', encoding='utf-8')

	assert report(op) == 1
	out = capsys.readouterr().out
	assert '1 edited' in out
	assert 'init --upgrade' in out


def test_a_missing_skill_file_is_a_problem(op, capsys):
	(op.skills / 'triage' / 'SKILL.md').unlink()
	assert report(op) == 1
	assert '1 missing' in capsys.readouterr().out


def test_a_broken_plugin_is_named_with_its_reason(op, capsys):
	(op.scrapers / 'broken.py').write_text('raise RuntimeError("no import")\n')
	assert report(op) == 1
	out = capsys.readouterr().out
	assert 'import failed: RuntimeError: no import' in out


def test_no_browser_found_is_a_problem_with_two_fixes(op, monkeypatch, capsys):
	monkeypatch.setattr(doctor, 'find_browser', lambda executable='': None)
	assert report(op, offline=False) == 1
	out = capsys.readouterr().out
	assert 'no Chrome, Chromium, Brave or Edge found' in out
	assert 'browser.executable' in out


def test_a_configured_but_missing_browser_names_the_config_value(op, monkeypatch, capsys):
	op.config.write_text('[browser]\nexecutable = "/nope/chrome"\n', encoding='utf-8')
	monkeypatch.setattr(doctor, 'find_browser', lambda executable='': None)

	assert report(op, offline=False) == 1
	out = capsys.readouterr().out
	assert "browser.executable = '/nope/chrome' does not exist" in out
	assert 'fix the path' in out


def test_a_port_used_by_something_else_is_a_problem(op, monkeypatch, capsys):
	monkeypatch.setattr(doctor, 'port_state', lambda port: 'busy')
	assert report(op, offline=False) == 1
	out = capsys.readouterr().out
	assert 'not a DevTools endpoint' in out
	assert 'browser.port' in out


def test_a_devtools_port_is_ours_and_ok(op, monkeypatch, capsys):
	monkeypatch.setattr(doctor, 'port_state', lambda port: 'devtools')
	assert report(op, offline=False) == 0
	assert 'already attached' in capsys.readouterr().out


def test_offline_skips_the_browser_and_port_checks_entirely(op, monkeypatch, capsys):
	"""The CI contract: no Chrome and no display must not fail a sound
	installation, so --offline never looks for a browser or probes the
	port — not even one that would be reported as a problem."""

	def fail(*args, **kwargs):
		raise AssertionError('an environment check ran under --offline')

	monkeypatch.setattr(doctor, 'find_browser', fail)
	monkeypatch.setattr(doctor, 'port_state', fail)

	assert report(op) == 0
	out = capsys.readouterr().out
	assert out.count('not checked (--offline)') == 3  # browser, port, boards


def fake_board(name='fake', homepage='https://fake.example'):
	"""A contract-shaped plugin module for the registry seam."""
	module = types.ModuleType(f'jobtriage.scrapers.{name}')
	module.NAME = name
	module.HOMEPAGE = homepage
	module.recent_jobs = lambda *, locations=None, limit=50, **options: []
	return module


def fake_registry(monkeypatch, *modules):
	reg = registry.Registry()
	for module in modules:
		reg._add(module, registry.ORIGIN_BUILTIN, module.NAME)
	monkeypatch.setattr(doctor.registry, 'load', lambda ws, settings: reg)
	return reg


class Response:
	"""The slice of an HTTP response `_reachable` reads."""

	status = 204

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False


def test_the_board_check_is_one_head_request_per_enabled_board(op, monkeypatch, capsys):
	fake_registry(monkeypatch, fake_board())
	seen = []

	def fake_urlopen(request, timeout=None):
		seen.append((request.get_method(), request.full_url, timeout))
		return Response()

	monkeypatch.setattr(doctor, 'urlopen', fake_urlopen)

	assert report(op, offline=False) == 0
	out = capsys.readouterr().out
	assert 'board fake' in out and 'HTTP 204' in out
	assert seen == [('HEAD', 'https://fake.example', doctor.BOARD_TIMEOUT)]


def test_the_board_check_sends_the_configured_user_agent(op, monkeypatch, capsys):
	"""`[network] user_agent` is the one setting doctor's HEAD request
	shares with the scrapers, so a board that 403s the default UA gets
	the same answer a scrape would."""
	fake_registry(monkeypatch, fake_board())
	op.config.write_text('[network]\nuser_agent = "jobtriage/1.0 (+url)"\n')
	requests = []

	def fake_urlopen(request, timeout=None):
		requests.append(request)
		return Response()

	monkeypatch.setattr(doctor, 'urlopen', fake_urlopen)

	assert report(op, offline=False) == 0
	assert requests[0].get_header('User-agent') == 'jobtriage/1.0 (+url)'


def test_a_board_that_answers_an_error_is_a_problem(op, monkeypatch, capsys):
	fake_registry(monkeypatch, fake_board())

	def forbidden(request, timeout=None):
		raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', None, None)

	monkeypatch.setattr(doctor, 'urlopen', forbidden)

	assert report(op, offline=False) == 1
	out = capsys.readouterr().out
	assert 'HTTP 403' in out
	assert 'sources test fake' in out


def test_a_board_without_a_homepage_is_a_note_not_a_failure(op, monkeypatch, capsys):
	fake_registry(monkeypatch, fake_board(homepage=''))

	assert report(op, offline=False) == 0
	assert 'no HOMEPAGE declared' in capsys.readouterr().out


def test_offline_makes_no_request_and_says_so(op, monkeypatch, capsys):
	called = []
	monkeypatch.setattr(doctor, 'urlopen', lambda *args, **kwargs: called.append(args))

	assert report(op) == 0
	assert called == []
	assert 'not checked (--offline)' in capsys.readouterr().out


def test_main_returns_1_when_any_check_fails(op, monkeypatch):
	skill = op.skills / 'jobtriage' / 'SKILL.md'
	skill.write_text(skill.read_text(encoding='utf-8') + '\nedited\n', encoding='utf-8')
	assert main(['--workspace', str(op.root), 'doctor', '--offline']) == 1
