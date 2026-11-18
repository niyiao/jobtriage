"""Board health (plan 04 step 6): the source_runs history, its summaries
and the two surfaces that read them.

The pipeline writes one row per attempted board through an injectable
recorder; `store.board_health` turns the rows into what `jobtriage
sources` (step 8) prints and what the end of `scrape` checks. Every test
here builds its boards through the registry seams, so no network and no
real board is reachable.
"""

import types
from datetime import datetime

import pytest

from jobtriage import config, pipeline, registry, store
from jobtriage.cli.main import main
from jobtriage.cli.scrape import warn_failing_boards
from jobtriage.pipeline import scrape_all, scrape_board

START = '2026-11-01T08:00:00+00:00'


def board(name, jobs=None, error=None):
	"""A fake plugin honouring the contract, like test_pipeline's."""
	module = types.ModuleType(f'jobtriage.scrapers.{name}')
	module.__name__ = f'jobtriage.scrapers.{name}'
	module.NAME = name
	module.WORLDWIDE = ('Worldwide',)
	module.COUNTRY_STYLE = 'none'
	module.FAMILIES = {'software': {}}

	def recent_jobs(locations=None, limit=50, **options):
		if error is not None:
			raise error
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


def settings(**search):
	return config.Config(search=config.SearchSettings(**search))


def record(db_path, source='alpha', **fields):
	"""One run row in the shape the pipeline writes it."""
	values = {
		'started_at': START,
		'duration_ms': 120,
		'ok': True,
		'fetched': 1,
		'dropped_invalid': 0,
		'error': None,
	}
	values.update(fields)
	store.record_run(db_path, source, **values)


@pytest.fixture(autouse=True)
def only_the_boards_this_test_installs(monkeypatch):
	"""No real built-ins, no installed packs, no developer op folder."""
	monkeypatch.setattr(registry, 'iter_modules', lambda paths: [])
	monkeypatch.setattr(registry, 'entry_points', lambda *, group: [])


# -- recording -------------------------------------------------------------


def test_record_run_writes_the_columns_health_reads(db_path):
	record(
		db_path,
		source='alpha',
		started_at='2026-11-01T08:00:00+00:00',
		duration_ms=1234,
		ok=False,
		fetched=7,
		dropped_invalid=2,
		error='RuntimeError: layout changed',
	)

	health = store.board_health(db_path)['alpha']
	assert health.source == 'alpha'
	assert health.runs == 1
	assert health.last_started_at == '2026-11-01T08:00:00+00:00'
	assert health.last_ok is False
	assert health.last_error == 'RuntimeError: layout changed'
	assert health.last_duration_ms == 1234
	assert health.last_fetched == 7
	assert health.dropped_invalid == 2


def test_a_successful_run_keeps_the_error_column_null(db_path):
	record(db_path, fetched=3)
	health = store.board_health(db_path)['alpha']

	assert health.last_ok is True
	assert health.last_error is None
	assert health.last_fetched == 3


def test_health_is_empty_before_the_first_run(db_path):
	assert store.board_health(db_path) == {}


# -- the summary -----------------------------------------------------------


def test_board_health_summarises_the_last_ten_runs(db_path):
	"""The success rate and the fetched/dropped totals cover the same
	window, so the numbers on one line always agree."""
	for number in range(1, 13):  # 12 runs; the first two fall out
		record(
			db_path,
			started_at=f'2026-11-{number:02d}T08:00:00+00:00',
			duration_ms=number * 10,
			ok=number not in (3, 12),
			fetched=number,
			dropped_invalid=1 if number == 12 else 0,
			error='RuntimeError: boom' if number == 12 else None,
		)

	health = store.board_health(db_path)['alpha']

	assert health.runs == 10
	assert health.failures == 2  # runs 3 and 12
	assert health.success_rate == 0.8
	assert health.fetched == sum(range(3, 13))  # the window, not all 12
	assert health.dropped_invalid == 1
	# The last fields describe only the newest run.
	assert health.last_started_at == '2026-11-12T08:00:00+00:00'
	assert health.last_ok is False
	assert health.last_error == 'RuntimeError: boom'
	assert health.last_duration_ms == 120
	assert health.last_fetched == 12


def test_a_streak_counts_failures_back_to_the_last_success(db_path):
	for _ in range(3):
		record(db_path, ok=False, error='TimeoutError: timed out')
	assert store.board_health(db_path)['alpha'].streak == 3
	assert store.board_health(db_path)['alpha'].failing() is True

	record(db_path)  # recovered today
	health = store.board_health(db_path)['alpha']
	assert health.streak == 0
	assert health.failing() is False
	assert health.failures == 3  # still visible in the window's rate


def test_two_failures_are_not_yet_a_failing_board(db_path):
	record(db_path, ok=False, error='one')
	record(db_path, ok=False, error='two')

	health = store.board_health(db_path)['alpha']
	assert health.streak == 2
	assert health.failing() is False
	assert health.failing(2) is True  # the threshold is the caller's


def test_boards_are_returned_by_name_and_keep_their_own_history(db_path):
	record(db_path, source='zulu', fetched=4)
	record(db_path, source='alpha', ok=False, error='down')

	health = store.board_health(db_path)

	assert list(health) == ['alpha', 'zulu']
	assert health['zulu'].last_fetched == 4
	assert health['alpha'].last_ok is False


# -- the pipeline's recorder ----------------------------------------------


def test_scrape_all_records_every_attempted_board(monkeypatch, ws, job):
	calls = []
	install(
		monkeypatch,
		board('good', [job(title='Software Engineer', company='Acme')]),
		board('flaky', error=RuntimeError('rate limited')),
	)

	scrape_all(ws, settings(), recorder=lambda **fields: calls.append(fields))

	assert {call['source'] for call in calls} == {'good', 'flaky'}
	good = next(call for call in calls if call['source'] == 'good')
	assert good['ok'] is True
	assert good['fetched'] == 1
	assert good['dropped_invalid'] == 0
	assert good['error'] is None
	assert good['duration_ms'] >= 0
	datetime.fromisoformat(good['started_at'])

	flaky = next(call for call in calls if call['source'] == 'flaky')
	assert flaky['ok'] is False
	assert flaky['error'] == 'RuntimeError: rate limited'
	assert flaky['fetched'] == 0


def test_skipped_and_broken_boards_are_not_recorded(monkeypatch, ws, capsys):
	calls = []
	install(monkeypatch, board('on', []), board('off', []))
	ws.scrapers.mkdir(exist_ok=True)
	(ws.scrapers / 'bad.py').write_text("NAME = 'bad'\n")  # no recent_jobs
	cfg = config.Config(sources=config.SourcesSettings(disabled=('off',)))

	scrape_all(ws, cfg, recorder=lambda **fields: calls.append(fields))
	capsys.readouterr()

	assert [call['source'] for call in calls] == ['on']


def test_scrape_board_records_a_failure_before_raising(monkeypatch, ws):
	calls = []
	install(monkeypatch, board('flaky', error=RuntimeError('layout changed')))

	with pytest.raises(pipeline.BoardError):
		scrape_board(ws, settings(), 'flaky', recorder=lambda **fields: calls.append(fields))

	assert len(calls) == 1
	assert calls[0]['source'] == 'flaky'
	assert calls[0]['ok'] is False
	assert 'layout changed' in calls[0]['error']


def test_a_recorder_that_raises_warns_and_the_scrape_goes_on(monkeypatch, ws, job, capsys):
	install(monkeypatch, board('good', [job(title='Software Engineer', company='Acme')]))

	def broken(**fields):
		raise RuntimeError('disk full')

	jobs = scrape_all(ws, settings(), recorder=broken)

	assert [job['title'] for job in jobs] == ['Software Engineer']
	assert 'could not record the run of good' in capsys.readouterr().err


def test_a_default_scrape_writes_the_workspace_database(monkeypatch, ws, job, capsys):
	install(
		monkeypatch,
		board('good', [job(title='Software Engineer', company='Acme')]),
		board('flaky', error=RuntimeError('down')),
	)
	scrape_all(ws, settings())

	health = store.board_health(ws.db)
	assert health['good'].last_ok is True
	assert health['flaky'].last_ok is False
	assert health['flaky'].last_error == 'RuntimeError: down'
	capsys.readouterr()  # the failing board's usual warning


# -- the end-of-scrape notice ---------------------------------------------


def test_scrape_points_at_sources_test_after_three_failures(monkeypatch, ws, job, capsys):
	install(
		monkeypatch,
		board('good', [job(title='Software Engineer', company='Acme')]),
		board('flaky', error=RuntimeError('layout changed')),
	)
	for _ in range(2):  # the run's own failure makes three
		record(ws.db, source='flaky', ok=False, error='RuntimeError: layout changed')

	assert main(['--workspace', str(ws.root), 'scrape']) == 0

	err = capsys.readouterr().err
	assert 'warning: flaky has failed its last 3 runs; try `jobtriage sources test flaky`' in err


def test_scrape_stays_quiet_below_three_failures(monkeypatch, ws, job, capsys):
	install(
		monkeypatch,
		board('good', [job(title='Software Engineer', company='Acme')]),
		board('flaky', error=RuntimeError('layout changed')),
	)
	record(ws.db, source='flaky', ok=False, error='RuntimeError: layout changed')

	assert main(['--workspace', str(ws.root), 'scrape']) == 0

	err = capsys.readouterr().err
	assert 'sources test' not in err
	assert 'warning: flaky: RuntimeError: layout changed' in err  # the run's own warning


def test_a_health_read_that_fails_warns_instead_of_ending_the_scrape(monkeypatch, ws, capsys):
	"""Health is an accessory, like the write side: a newer-schema
	database or a locked read must not turn a finished scrape into a
	traceback."""

	def broken(db_path):
		raise RuntimeError('database is locked')

	monkeypatch.setattr(store, 'board_health', broken)

	warn_failing_boards(ws)

	assert 'could not read board health: database is locked' in capsys.readouterr().err
