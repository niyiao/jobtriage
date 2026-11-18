"""Logging: the workspace file, the `-v` flag, and what moved into it.

The file is the durable record; stderr is the same one-line warnings the
CLI has always printed. The tests use a tmp workspace, so nothing here
touches a real op folder, and the logger's handlers are restored around
each test because logging is process-global by design.
"""

import logging
import logging.handlers

import pytest

from jobtriage import logs, pipeline, registry, workspace
from jobtriage.cli.main import main


@pytest.fixture(autouse=True)
def fresh_handlers():
	"""Undo a test's handler changes: logging outlives the tmp folder."""
	yield
	logs._close_file()
	logs._stderr.setLevel(logging.WARNING)


@pytest.fixture
def ws(tmp_path, monkeypatch):
	"""A minimal workspace with its pointer file kept out of the real
	platform config dir."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')
	root = tmp_path / 'op'
	root.mkdir()
	(root / workspace.CONFIG_NAME).write_text('# test workspace\n')
	return workspace.Workspace(root)


def read_log(ws) -> str:
	return ws.log_file.read_text(encoding='utf-8')


def test_the_workspace_owns_the_log_paths(ws):
	assert ws.logs == ws.state / 'logs'
	assert ws.log_file == ws.logs / 'jobtriage.log'


def test_setup_creates_the_log_and_records_the_run(ws):
	logs.setup(ws)

	assert ws.log_file.is_file()
	text = read_log(ws)
	assert 'workspace' in text and str(ws.root) in text


def test_the_file_handler_rotates_around_a_megabyte_and_keeps_three(ws):
	logs.setup(ws)

	handler = next(
		item for item in logs.logger.handlers if isinstance(item, logging.handlers.RotatingFileHandler)
	)
	assert handler.maxBytes == logs.FILE_BYTES
	assert handler.backupCount == logs.FILE_COPIES
	assert str(ws.log_file) == handler.baseFilename


def test_warnings_reach_stderr_with_the_cli_prefix(ws, capsys):
	logs.setup(ws)
	logging.getLogger('jobtriage.test').warning('a board broke')

	assert 'warning: a board broke' in capsys.readouterr().err


def test_debug_is_file_only_until_verbose(ws, capsys):
	logs.setup(ws)
	logging.getLogger('jobtriage.test').debug('a quiet detail')

	assert 'a quiet detail' not in capsys.readouterr().err
	assert 'a quiet detail' in read_log(ws)

	logs.setup(ws, verbose=True)
	logging.getLogger('jobtriage.test').debug('a loud detail')

	assert 'debug: a loud detail' in capsys.readouterr().err


def test_switching_workspaces_closes_the_old_file(tmp_path, ws):
	logs.setup(ws)
	other = workspace.Workspace(tmp_path / 'other')
	other.root.mkdir()
	logs.setup(other)

	assert logs._file is not None
	assert logs._file.baseFilename == str(other.log_file)
	assert len([h for h in logs.logger.handlers if h is not logs._stderr]) == 1


def test_a_read_only_workspace_still_runs(tmp_path):
	"""No log file is an accessory, not a failed command."""
	root = tmp_path / 'read-only'
	root.mkdir()
	(root / workspace.CONFIG_NAME).write_text('# test\n')
	(root / '.jobtriage').mkdir()
	(root / '.jobtriage' / 'logs').write_text('not a folder')

	logs.setup(workspace.Workspace(root))  # no raise
	assert logs._file is None


def test_the_verbose_flag_is_global_and_writes_a_file(ws, capsys):
	assert main(['--workspace', str(ws.root), '-v', 'where']) == 0

	captured = capsys.readouterr()
	assert captured.out.strip() == str(ws.root)
	assert 'debug:' in captured.err  # setup's own line
	assert ws.log_file.is_file()
	assert 'workspace' in read_log(ws)


def test_a_pipeline_warning_is_also_in_the_workspace_log(ws, monkeypatch, capsys):
	"""The one-line stderr warning stays; the file gets the timestamped
	copy, so a board that failed last week is still diagnosable."""
	import types

	module = types.ModuleType('jobtriage.scrapers.flaky')
	module.NAME = 'flaky'
	module.WORLDWIDE = ('Worldwide',)
	module.COUNTRY_STYLE = 'none'
	module.FAMILIES = {'software': {}}

	def recent_jobs(locations=None, limit=50, **options):
		raise RuntimeError('layout changed')

	module.recent_jobs = recent_jobs

	good = types.ModuleType('jobtriage.scrapers.good')
	good.NAME = 'good'
	good.WORLDWIDE = ('Worldwide',)
	good.COUNTRY_STYLE = 'none'
	good.FAMILIES = {'software': {}}
	good.recent_jobs = lambda **options: [
		{
			'title': 'Software Engineer',
			'company': 'Acme',
			'url': 'https://acme.test/1',
			'published': '2026-11-10',
			'locations': ['Worldwide'],
			'salary': None,
		}
	]

	by_name = {'flaky': module, 'good': good}
	monkeypatch.setattr(
		registry,
		'iter_modules',
		lambda paths: [types.SimpleNamespace(name=name) for name in by_name],
	)
	monkeypatch.setattr(
		registry, 'import_module', lambda fullname: by_name[fullname.rsplit('.', 1)[-1]]
	)

	logs.setup(ws)
	from jobtriage import config

	pipeline.scrape_all(ws, config.Config(), recorder=lambda **fields: None)

	assert 'warning: flaky: RuntimeError: layout changed' in capsys.readouterr().err
	assert 'flaky: RuntimeError: layout changed' in read_log(ws)
