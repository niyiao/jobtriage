"""Tests for the CLI shape: the parser, the dispatch, the workspace
requirement, and the exit codes — with fake scrapers and a tmp
workspace, so nothing here touches a network or the developer's own
op folder.
"""

import csv
import io
import json
import os
import shutil
import types
from datetime import UTC, datetime

import pytest

from jobtriage import __version__, db, registry, store, workspace
from jobtriage.cli.main import main


@pytest.fixture(autouse=True)
def no_pointer(monkeypatch, tmp_path):
	"""Never find a real workspace through the platform pointer file —
	tests resolve through --workspace or the walk-up they set up
	themselves."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'no-pointer')
	monkeypatch.delenv('JOBTRIAGE_HOME', raising=False)


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch):
	"""The scrape command reads jobtriage.toml through config.load now,
	and config.load also reads JOBTRIAGE_* — a developer's shell must not
	change what these tests see."""
	for name in list(os.environ):
		if name.startswith('JOBTRIAGE_') and name != 'JOBTRIAGE_HOME':
			monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_installed_boards(monkeypatch):
	"""The scrape tests install only the fake boards they name; the real
	fifteen must never be reachable from a CLI test."""
	monkeypatch.setattr(registry, 'iter_modules', lambda paths: [])
	monkeypatch.setattr(registry, 'entry_points', lambda *, group: [])


def fake_board(name, jobs=None, error=None, *, aliases=(), seen=None):
	"""A fake built-in plugin for the registry seam, contract-shaped."""
	module = types.ModuleType(f'jobtriage.scrapers.{name}')
	module.NAME = name
	module.ALIASES = aliases
	module.WORLDWIDE = ('Worldwide',)
	module.COUNTRY_STYLE = 'none'
	module.FAMILIES = {'software': {}}

	def recent_jobs(locations=None, limit=50, **options):
		if seen is not None:
			seen.update(locations=locations, limit=limit)
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


def test_version_flag(capsys):
	"""Without a workspace it still answers: the version, and the command
	that makes the folder the other two lines would name."""
	with pytest.raises(SystemExit) as done:
		main(['--version'])
	assert done.value.code == 0
	out = capsys.readouterr().out
	assert f'jobtriage {__version__}' in out
	assert 'workspace: none' in out
	assert 'jobtriage init' in out


def test_version_flag_names_the_workspace_and_the_schema(ws, capsys, monkeypatch):
	monkeypatch.setenv('JOBTRIAGE_HOME', str(ws.root))
	with pytest.raises(SystemExit) as done:
		main(['--version'])
	assert done.value.code == 0
	out = capsys.readouterr().out
	assert f'workspace: {ws.root}' in out
	assert f'database: schema {db.LATEST} (no database file yet)' in out


def test_bare_command_runs_the_tui(ws, monkeypatch):
	"""`jobtriage` with no subcommand opens the TUI (decision 3)."""
	ran = []
	monkeypatch.setattr('jobtriage.cli.tui.run', lambda args, ws: ran.append(ws))
	code = main(['--workspace', str(ws.root)])
	assert code == 0
	assert ran == [ws]


def test_where_prints_the_workspace(ws, capsys):
	assert main(['--workspace', str(ws.root), 'where']) == 0
	assert capsys.readouterr().out.strip() == str(ws.root)


def test_where_from_inside_the_workspace(ws, monkeypatch):
	"""The walk-up makes an agent inside the op folder need no flags."""
	monkeypatch.chdir(ws.root)
	assert main(['where']) == 0


def test_no_workspace_exits_2(ws, capsys):
	"""Nothing resolves: exit 2 with the command to run (stdin is not
	a TTY under pytest, so there's no prompt to answer)."""
	code = main(['where'])
	assert code == 2
	err = capsys.readouterr().err
	assert err.startswith('error:')
	assert 'jobtriage init' in err


def test_explicit_path_without_marker_exits_2(tmp_path, capsys):
	code = main(['--workspace', str(tmp_path / 'nope'), 'where'])
	assert code == 2
	assert 'not a workspace' in capsys.readouterr().err


def test_an_old_version_stamp_prints_one_upgrade_notice(ws, capsys):
	"""Plan 08 step 21: after a package upgrade the first run says so —
	one line on stderr, so `list --json` on stdout stays parseable."""
	ws.state.mkdir()
	ws.version_file.write_text('0.0.1')

	assert main(['--workspace', str(ws.root), 'where']) == 0
	captured = capsys.readouterr()
	assert captured.out.strip() == str(ws.root)
	assert captured.err.count('notice:') == 1
	assert '0.0.1' in captured.err
	assert 'jobtriage init --upgrade' in captured.err


def test_a_current_version_stamp_prints_nothing(ws, capsys):
	ws.state.mkdir()
	ws.version_file.write_text(__version__)

	assert main(['--workspace', str(ws.root), 'where']) == 0
	assert 'notice:' not in capsys.readouterr().err


def test_the_missing_workspace_offer_never_prompts_without_a_tty(tmp_path, monkeypatch, capsys):
	"""Plan 05's rule: an agent's stdin is a pipe, so `input()` must not
	be reached — the command exits 2 with the instruction instead."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'none')

	def never(prompt=''):
		raise AssertionError('a non-TTY must not be asked a question')

	monkeypatch.setattr('builtins.input', never)
	assert main(['where']) == 2
	assert 'jobtriage init' in capsys.readouterr().err


def test_an_unexpected_failure_is_one_line_not_a_traceback(ws, capsys):
	"""A database from a newer build is the realistic last-resort case:
	store.migrate raises inside a command that has no handler for it, and
	the dispatcher's contract turns it into `error: ...` and exit 1."""
	import sqlite3

	conn = sqlite3.connect(ws.db)
	try:
		conn.execute('PRAGMA user_version = 99')
		conn.commit()
	finally:
		conn.close()

	assert main(['--workspace', str(ws.root), 'list']) == 1
	err = capsys.readouterr().err
	assert err.startswith('error:')
	assert 'schema version 99' in err
	assert 'Traceback' not in err


def test_a_broken_config_stops_the_tui_with_one_line(ws, capsys):
	"""`tui` reads `[tui]` through config.load_tui, which was the one
	command without a ConfigError handler; the dispatcher now supplies
	the same one-line contract every other command has."""
	ws.config.write_text('[tui]\ndefault_filter = "no-such-tab"\n')

	assert main(['--workspace', str(ws.root), 'tui']) == 1
	err = capsys.readouterr().err
	assert err.startswith('error:')
	assert 'default_filter' in err
	assert 'Traceback' not in err


def test_list_reads_the_workspace_db(ws, job, capsys):
	from jobtriage import store

	store.ingest(
		[
			job(title='Software Engineer', company='Acme', published='2026-11-09'),
			job(title='Web Developer', company='Beta', published='2026-11-10'),
		],
		ws.db,
	)
	assert main(['--workspace', str(ws.root), 'list']) == 0
	out = capsys.readouterr().out
	assert 'Web Developer — Beta' in out
	assert 'Software Engineer — Acme' in out


def test_scrape_raw_with_a_fake_board(ws, job, monkeypatch, capsys):
	seen = {}
	install(
		monkeypatch,
		fake_board(
			'fake', [job(title='Software Engineer', company='Acme')], aliases=('fk',), seen=seen
		),
	)
	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'fk', '3', '--raw']) == 0
	captured = capsys.readouterr()
	assert 'Software Engineer — Acme' in captured.out
	assert seen == {'locations': ('Worldwide',), 'limit': 3}


def test_scrape_reads_limit_and_flags_override_it(ws, job, monkeypatch, capsys):
	"""The parser's limit default is None, so search.limit in the op
	folder carries when no number was typed; a typed number is an
	override and wins (plan 03 step 4)."""
	seen = {}
	install(monkeypatch, fake_board('fake', [], seen=seen))
	ws.config.write_text('[search]\nlimit = 3\n')

	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'fake']) == 0
	assert seen['limit'] == 3
	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'fake', '7']) == 0
	assert seen['limit'] == 7
	capsys.readouterr()


def test_init_creates_a_workspace(tmp_path, monkeypatch, capsys):
	"""init with a path creates the full tree from the real shipped
	assets — this one intentionally uses them."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')
	op = tmp_path / 'made'
	assert main(['init', str(op)]) == 0
	out = capsys.readouterr().out
	assert f'workspace: {op}' in out
	assert (op / workspace.CONFIG_NAME).is_file()
	assert (op / '.gitignore').is_file()
	assert (op / 'AGENTS.md').is_file()
	assert (op / 'scrapers' / '_example.py').is_file()  # the plugin template (plan 04)
	assert (tmp_path / 'pointer').read_text().strip() == str(op)
	# and it resolves as a workspace now
	assert workspace.find(path=op).root == op


def test_init_is_idempotent_through_the_cli(tmp_path, monkeypatch):
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')
	op = tmp_path / 'made'
	main(['init', str(op)])
	(op / 'AGENTS.md').write_text('my instructions')
	main(['init', str(op)])
	assert (op / 'AGENTS.md').read_text() == 'my instructions'


def test_scrape_ingests_and_prints_the_summary(ws, job, monkeypatch, capsys):
	"""A scrape is one database transaction now: the summary reports
	what relevance kept and what the ingest did, and a second run of the
	same board adds nothing while still saying what it saw."""
	from jobtriage import store

	install(
		monkeypatch,
		fake_board(
			'fake',
			[
				job(
					title='Software Engineer',
					company='Acme',
					published=datetime.now(UTC).isoformat(),
				)
			],
		),
	)

	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'fake']) == 0
	assert '1 relevant · 1 new · 0 already seen · 1 in the database' in capsys.readouterr().err
	assert sum(store.counts(ws.db).values()) == 1

	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'fake']) == 0
	assert '1 relevant · 0 new · 1 already seen · 1 in the database' in capsys.readouterr().err


def test_scrape_explain_prints_the_per_board_tally_and_reasons(ws, job, monkeypatch, capsys):
	"""`--explain` answers "why did this board contribute so little?" on
	stderr, with the same counters the run used; stdout stays clean."""
	install(
		monkeypatch,
		fake_board(
			'fake',
			[
				job(
					title='Software Engineer',
					company='Acme',
					published=datetime.now(UTC).isoformat(),
				),
				job(
					title='Sales Engineer',
					company='Sales',
					published=datetime.now(UTC).isoformat(),
				),
				job(title='Software Engineer', company='Old', published='2020-01-01'),
			],
		),
	)
	ws.config.write_text('[filter]\nexclude_any = ["sales engineer"]\n')

	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'fake', '--explain']) == 0
	captured = capsys.readouterr()

	assert captured.out == ''
	assert 'explain: fake · fetched 3 · age 1 · title 1 · duplicate 0 · kept 1' in captured.err
	assert '  exclude_any matched "sales engineer" ×1' in captured.err
	assert '1 relevant · 1 new · 0 already seen · 1 in the database' in captured.err


def test_scrape_without_explain_stays_quiet(ws, job, monkeypatch, capsys):
	"""The flag is the whole cost: nothing prints without it."""
	install(monkeypatch, fake_board('fake', [job(title='Software Engineer')]))

	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'fake']) == 0
	assert 'explain:' not in capsys.readouterr().err


def test_scrape_raw_explain_counts_every_job_as_kept(ws, job, monkeypatch, capsys):
	"""`--raw` applies no filter, so the tally says fetched == kept and
	puts the report on stderr, away from the job listing on stdout."""
	install(monkeypatch, fake_board('fake', [job(title='Account Executive')]))

	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'fake', '--raw', '--explain']) == 0
	captured = capsys.readouterr()

	assert 'Account Executive — Acme' in captured.out
	assert 'explain: fake · fetched 1 · age 0 · title 0 · duplicate 0 · kept 1' in captured.err


def test_scrape_with_an_unknown_board_is_a_clean_usage_error(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('fake', []))

	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'ghost']) == 2
	err = capsys.readouterr().err
	assert 'error: unknown board' in err
	assert 'ghost' in err
	assert 'Traceback' not in err


def test_scrape_with_a_broken_board_is_one_line(ws, monkeypatch, capsys):
	install(monkeypatch, fake_board('fake', []))
	ws.scrapers.mkdir()
	(ws.scrapers / 'inert.py').write_text("NAME = 'inert'\n")

	assert main(['--workspace', str(ws.root), 'scrape', '-s', 'inert']) == 1
	err = capsys.readouterr().err
	assert 'broken: inert: missing recent_jobs' in err
	assert 'error: inert is broken: missing recent_jobs' in err
	assert 'Traceback' not in err


def test_background_scrape_is_the_same_pipeline_and_reports_progress(ws, job, monkeypatch):
	"""What the TUI's `R` is handed: every board, the same ingest, the
	progress callback naming each board, and the new count returned for
	the footer's flash. A second run sees the job and reports 0 new."""
	from jobtriage import store
	from jobtriage.cli.scrape import background_scrape

	install(monkeypatch, fake_board('fake', [job(title='Software Engineer', company='Acme')]))
	seen = []

	def on_board(name, done, total):
		seen.append((name, done, total))

	assert background_scrape(ws)(on_board) == 1
	assert seen == [('fake', 1, 1)]
	assert sum(store.counts(ws.db).values()) == 1
	assert background_scrape(ws)(on_board) == 0
	assert seen[-1] == ('fake', 1, 1)


# -- list: filters and the JSON shape -----------------------------------


def test_list_json_prints_the_full_job_dicts(ws, job, capsys):
	result = store.ingest(
		[
			job(title='Software Engineer', company='Acme'),
			job(title='Web Developer', company='Beta'),
		],
		ws.db,
	)
	assert main(['--workspace', str(ws.root), 'list', '--json']) == 0
	jobs = json.loads(capsys.readouterr().out)
	assert {entry['title'] for entry in jobs} == {
		'Software Engineer',
		'Web Developer',
	}
	assert {entry['id'] for entry in jobs} == {j['id'] for j in result.new}
	# every column, not just the three the human table prints
	assert {'url', 'status', 'published', 'locations', 'first_seen'} <= set(jobs[0])


def test_list_filters_by_status(ws, job, capsys):
	result = store.ingest(
		[
			job(title='Software Engineer', company='Acme'),
			job(title='Web Developer', company='Beta'),
		],
		ws.db,
	)
	store.set_status(ws.db, result.new[0]['id'], 'to_apply')
	assert main(['--workspace', str(ws.root), 'list', '--status', 'to_apply', '--json']) == 0
	jobs = json.loads(capsys.readouterr().out)
	assert [entry['title'] for entry in jobs] == ['Software Engineer']


def test_list_filters_by_since(ws, job, capsys):
	recent = job(
		title='Recent Engineer',
		company='Acme',
		published=datetime.now(UTC).isoformat(),
	)
	old = job(title='Old Engineer', company='Beta', published='2020-01-01')
	store.ingest([recent, old], ws.db)
	assert main(['--workspace', str(ws.root), 'list', '--since', '7d', '--json']) == 0
	jobs = json.loads(capsys.readouterr().out)
	assert [entry['title'] for entry in jobs] == ['Recent Engineer']


def test_list_search_is_a_literal_substring(ws, job, capsys):
	"""`%` in a search term is text, not a wildcard: searching for it
	must not return every job."""
	store.ingest(
		[
			job(title='100% Remote Engineer', company='Acme'),
			job(title='Remote Engineer', company='Beta'),
		],
		ws.db,
	)
	assert main(['--workspace', str(ws.root), 'list', '--search', '%', '--json']) == 0
	jobs = json.loads(capsys.readouterr().out)
	assert [entry['title'] for entry in jobs] == ['100% Remote Engineer']


def test_list_names_a_typoed_status(ws, capsys):
	assert main(['--workspace', str(ws.root), 'list', '--status', 'to_aply']) == 2
	err = capsys.readouterr().err
	assert 'error:' in err
	assert "unknown status slug 'to_aply'" in err


def test_list_source_order_and_limit(ws, job, capsys):
	store.ingest(
		[
			job(title='Beta Engineer', company='Beta', source='beta'),
			job(title='Alpha Engineer', company='Alpha', source='alpha'),
		],
		ws.db,
	)
	assert main(['--workspace', str(ws.root), 'list', '--source', 'alpha', '--json']) == 0
	assert [entry['title'] for entry in json.loads(capsys.readouterr().out)] == ['Alpha Engineer']
	assert main(['--workspace', str(ws.root), 'list', '--order', 'title', '--json']) == 0
	titles = [entry['title'] for entry in json.loads(capsys.readouterr().out)]
	assert titles == ['Alpha Engineer', 'Beta Engineer']
	assert main(['--workspace', str(ws.root), 'list', '--limit', '1', '--json']) == 0
	assert len(json.loads(capsys.readouterr().out)) == 1


def test_list_json_leaves_stderr_clean(ws, job, capsys):
	store.ingest([job()], ws.db)
	assert main(['--workspace', str(ws.root), 'list', '--json']) == 0
	captured = capsys.readouterr()
	assert json.loads(captured.out)  # the stream is one JSON document
	assert captured.err == ''


# -- show: every field, then notes, history and attachments ------------


def test_show_prints_the_job_its_history_and_notes(ws, job, capsys):
	result = store.ingest([job(title='Software Engineer', company='Acme')], ws.db)
	job_id = result.new[0]['id']
	store.set_status(ws.db, job_id, 'to_apply')
	store.add_note(ws.db, job_id, 'recruiter called')
	assert main(['--workspace', str(ws.root), 'show', str(job_id)]) == 0
	out = capsys.readouterr().out
	assert 'Software Engineer — Acme' in out
	assert 'https://example.com/job' in out  # a field, not just the header
	assert 'title_key' in out  # every field, internal ones included
	assert 'to_apply' in out
	assert 'recruiter called' in out
	assert 'new → to_apply' in out
	assert '(cli)' in out
	assert 'attachments' in out and 'none' in out


def test_show_json_puts_history_and_attachments_beside_the_fields(ws, job, capsys):
	result = store.ingest([job()], ws.db)
	job_id = result.new[0]['id']
	store.set_status(ws.db, job_id, 'applied')
	store.add_note(ws.db, job_id, 'offer on Friday')
	assert main(['--workspace', str(ws.root), 'show', str(job_id), '--json']) == 0
	shown = json.loads(capsys.readouterr().out)
	assert shown['id'] == job_id
	assert shown['status'] == 'applied'
	assert shown['notes'] == 'offer on Friday'
	assert shown['url'] == 'https://example.com/job'
	assert shown['attachments'] == []
	assert [(change['old'], change['new'], change['actor']) for change in shown['history']] == [
		('new', 'applied', 'cli')
	]


def test_show_missing_id_is_an_error_not_a_traceback(ws, capsys):
	assert main(['--workspace', str(ws.root), 'show', '99']) == 1
	assert 'no job with id 99' in capsys.readouterr().err


# -- open: the posting in the workspace browser -------------------------


class FakeOpener:
	"""The TUI opener's interface, recording what the command asked for."""

	focuses_tabs = True

	def __init__(self):
		self.calls = []

	def open_or_focus(self, url, remembered=None):
		self.calls.append((url, remembered))
		return 'tab-1'


def test_open_opens_the_jobs_url_and_prints_it(ws, job, monkeypatch, capsys):
	result = store.ingest([job(url='https://example.com/posting/42')], ws.db)
	opened = FakeOpener()
	monkeypatch.setattr('jobtriage.cli.open.opener_for', lambda _ws: opened)

	assert main(['--workspace', str(ws.root), 'open', str(result.new[0]['id'])]) == 0
	assert capsys.readouterr().out.strip() == 'https://example.com/posting/42'
	assert opened.calls == [('https://example.com/posting/42', None)]


def test_open_unknown_id_exits_1_without_opening_anything(ws, monkeypatch, capsys):
	def unreachable(_ws):
		raise AssertionError('no browser lookup for a missing job')

	monkeypatch.setattr('jobtriage.cli.open.opener_for', unreachable)

	assert main(['--workspace', str(ws.root), 'open', '99']) == 1
	err = capsys.readouterr().err
	assert 'no job with id 99' in err
	assert 'Traceback' not in err


def test_open_reports_a_browser_failure_without_a_traceback(ws, job, monkeypatch, capsys):
	result = store.ingest([job()], ws.db)

	class Broken:
		def open_or_focus(self, url, remembered=None):
			raise RuntimeError('Chrome did not open its DevTools port')

	monkeypatch.setattr('jobtriage.cli.open.opener_for', lambda _ws: Broken())

	assert main(['--workspace', str(ws.root), 'open', str(result.new[0]['id'])]) == 1
	err = capsys.readouterr().err
	assert 'error: could not open https://example.com/job' in err
	assert 'Chrome did not open' in err
	assert 'Traceback' not in err


# -- attach: a file against a job --------------------------------------


def test_attach_records_a_workspace_relative_letter(ws, job, capsys):
	result = store.ingest([job()], ws.db)
	job_id = result.new[0]['id']
	ws.letters.mkdir()
	(ws.letters / 'acme-engineer.md').write_text('Dear Acme')

	assert main(['--workspace', str(ws.root), 'attach', str(job_id), 'letters/acme-engineer.md']) == 0
	out = capsys.readouterr().out
	assert f'{job_id}: attached letters/acme-engineer.md (cover_letter)' in out

	assert main(['--workspace', str(ws.root), 'show', str(job_id), '--json']) == 0
	shown = json.loads(capsys.readouterr().out)
	assert [(row['kind'], row['path']) for row in shown['attachments']] == [
		('cover_letter', 'letters/acme-engineer.md')
	]


def test_attach_twice_is_a_no_op(ws, job, capsys):
	result = store.ingest([job()], ws.db)
	job_id = result.new[0]['id']
	ws.letters.mkdir()
	(ws.letters / 'acme-engineer.md').write_text('Dear Acme')

	main(['--workspace', str(ws.root), 'attach', str(job_id), 'letters/acme-engineer.md'])
	capsys.readouterr()
	assert main(['--workspace', str(ws.root), 'attach', str(job_id), 'letters/acme-engineer.md']) == 0
	assert f'{job_id}: already attached letters/acme-engineer.md' in capsys.readouterr().out
	assert len(store.attachments(ws.db, job_id)) == 1


def test_attach_to_an_outside_file_keeps_it_absolute_and_kind_file(ws, job, tmp_path, capsys):
	result = store.ingest([job()], ws.db)
	job_id = result.new[0]['id']
	outside = tmp_path / 'portfolio.pdf'
	outside.write_text('%PDF')

	assert main(['--workspace', str(ws.root), 'attach', str(job_id), str(outside)]) == 0
	out = capsys.readouterr().out
	assert f'attached {outside} (file)' in out
	assert store.attachments(ws.db, job_id)[0]['path'] == str(outside)


def test_attach_missing_file_and_missing_job_are_clean_errors(ws, job, capsys):
	result = store.ingest([job()], ws.db)
	job_id = result.new[0]['id']

	assert main(['--workspace', str(ws.root), 'attach', str(job_id), 'letters/nope.md']) == 1
	err = capsys.readouterr().err
	assert 'error:' in err and 'not a file' in err
	assert 'Traceback' not in err

	ws.letters.mkdir()
	(ws.letters / 'letter.md').write_text('Dear Acme')
	assert main(['--workspace', str(ws.root), 'attach', '99', 'letters/letter.md']) == 1
	err = capsys.readouterr().err
	assert 'no job with id 99' in err
	assert 'Traceback' not in err


# -- status and note: the two writes ------------------------------------


def test_status_changes_it_and_prints_the_transition(ws, job, capsys):
	result = store.ingest([job()], ws.db)
	job_id = result.new[0]['id']
	assert main(['--workspace', str(ws.root), 'status', str(job_id), 'to_apply']) == 0
	assert f'{job_id}: new → to_apply' in capsys.readouterr().out
	assert store.get(ws.db, job_id)['status'] == 'to_apply'


def test_status_twice_reports_already_and_writes_no_history(ws, job, capsys):
	result = store.ingest([job()], ws.db)
	job_id = result.new[0]['id']
	main(['--workspace', str(ws.root), 'status', str(job_id), 'to_apply'])
	capsys.readouterr()
	assert main(['--workspace', str(ws.root), 'status', str(job_id), 'to_apply']) == 0
	assert f'{job_id}: already to_apply' in capsys.readouterr().out
	assert main(['--workspace', str(ws.root), 'show', str(job_id), '--json']) == 0
	assert len(json.loads(capsys.readouterr().out)['history']) == 1


def test_status_unknown_slug_leaves_the_job_alone(ws, job, capsys):
	result = store.ingest([job()], ws.db)
	job_id = result.new[0]['id']
	assert main(['--workspace', str(ws.root), 'status', str(job_id), 'to_aply']) == 2
	assert "unknown status slug 'to_aply'" in capsys.readouterr().err
	assert store.get(ws.db, job_id)['status'] == 'new'


def test_status_missing_id_exits_1(ws, capsys):
	assert main(['--workspace', str(ws.root), 'status', '99', 'applied']) == 1
	assert 'no job with id 99' in capsys.readouterr().err


def test_note_appends_rather_than_replaces(ws, job, capsys):
	result = store.ingest([job()], ws.db)
	job_id = result.new[0]['id']
	assert main(['--workspace', str(ws.root), 'note', str(job_id), 'called']) == 0
	assert f'{job_id}: note added' in capsys.readouterr().out
	assert main(['--workspace', str(ws.root), 'note', str(job_id), 'called back']) == 0
	notes = store.get(ws.db, job_id)['notes']
	assert notes == 'called\ncalled back'


def test_note_missing_id_exits_1(ws, capsys):
	assert main(['--workspace', str(ws.root), 'note', '99', 'hello']) == 1
	assert 'no job with id 99' in capsys.readouterr().err


# -- export: the same filters, two formats ------------------------------


def test_export_csv_writes_a_header_and_the_filtered_rows(ws, job, capsys):
	result = store.ingest(
		[
			job(
				title='Software Engineer',
				company='Acme',
				url='https://example.com/one',
				salary='$100k',
			),
			job(
				title='Web Developer',
				company='Beta',
				url='https://example.com/two',
			),
		],
		ws.db,
	)
	applied = result.new[0]['id']
	store.set_status(ws.db, applied, 'applied')
	assert main(['--workspace', str(ws.root), 'export', '--status', 'applied']) == 0
	rows = list(csv.reader(io.StringIO(capsys.readouterr().out)))
	header, row = rows[0], rows[1]
	assert len(rows) == 2  # header + the one applied job
	assert {'id', 'title', 'company', 'status', 'notes'} <= set(header)
	assert row[header.index('id')] == str(applied)
	assert row[header.index('title')] == 'Software Engineer'
	assert row[header.index('status')] == 'applied'
	assert row[header.index('locations')] == 'Worldwide'
	assert row[header.index('published')] == '2026-11-10'


def test_export_json_is_the_same_list_as_list_json(ws, job, capsys):
	store.ingest(
		[
			job(title='Software Engineer', company='Acme'),
			job(title='Web Developer', company='Beta'),
		],
		ws.db,
	)
	assert main(['--workspace', str(ws.root), 'export', '--format', 'json']) == 0
	exported = json.loads(capsys.readouterr().out)
	assert {entry['title'] for entry in exported} == {
		'Software Engineer',
		'Web Developer',
	}
	assert all(entry['status'] == 'new' for entry in exported)


def test_export_names_a_typoed_status(ws, capsys):
	assert main(['--workspace', str(ws.root), 'export', '--status', 'nope']) == 2
	assert "unknown status slug 'nope'" in capsys.readouterr().err


# -- db: path, schema, the shell ----------------------------------------


def test_db_path_prints_the_workspace_database(ws, capsys):
	assert main(['--workspace', str(ws.root), 'db', 'path']) == 0
	assert capsys.readouterr().out.strip() == str(ws.db)


def test_db_schema_is_readable_and_commented(ws, job, capsys):
	store.ingest([job()], ws.db)
	assert main(['--workspace', str(ws.root), 'db', 'schema']) == 0
	out = capsys.readouterr().out
	assert 'schema version 3' in out
	for table in ('jobs', 'status_history', 'attachments', 'source_runs'):
		assert table in out
	assert 'stable id' in out  # a column comment, not just the column name
	assert 'status_history' in out
	assert 'unique: title_key, company_key, role, year, month' in out
	assert '→ jobs.id' in out


@pytest.mark.skipif(shutil.which('sqlite3') is None, reason='no sqlite3 binary')
def test_db_shell_runs_sqlite_and_passes_the_exit_code(ws, job, capfd):
	store.ingest([job(title='Software Engineer')], ws.db)
	code = main(
		[
			'--workspace',
			str(ws.root),
			'db',
			'shell',
			'select title from jobs;',
		]
	)
	assert code == 0
	out, _ = capfd.readouterr()
	assert 'Software Engineer' in out
	# sqlite3's own exit code comes back, not a generic failure
	assert main(['--workspace', str(ws.root), 'db', 'shell', 'nonsense;']) == 1


# -- db import-sheet: the one-time move off the sheet -------------------
#
# The header is the real export layout the command has to read, so it is
# mirrored here rather than imported: old spreadsheets do not change
# when this code does.

SHEET_HEADER = (
	'title',
	'company',
	'published',
	'locations',
	'salary',
	'role',
	'source',
	'url',
	'status',
	'apply status',
)


def sheet_row(**fields):
	"""One CSV row in the sheet's column order; unset cells are empty."""
	return [fields.get(name, '') for name in SHEET_HEADER]


def write_sheet(path, rows, header=SHEET_HEADER):
	"""A sheet export: the given header, then the rows, BOM and all.

	utf-8-sig is what Google's download actually is, so every import
	test exercises the BOM handling too.
	"""
	with path.open('w', newline='', encoding='utf-8-sig') as handle:
		writer = csv.writer(handle)
		writer.writerow(header)
		writer.writerows(rows)
	return path


def import_sheet(ws, path, *extra):
	"""Run the command the way a person does; returns its exit code."""
	return main(['--workspace', str(ws.root), 'db', 'import-sheet', str(path), *extra])


def test_import_sheet_matches_on_url_then_key_and_inserts_the_rest(ws, job, tmp_path, capsys):
	seeded = store.ingest(
		[
			job(
				title='Software Engineer',
				company='Acme',
				url='https://example.com/one',
			),
			job(title='Web Developer', company='Beta', url='https://example.com/two'),
		],
		ws.db,
	)
	by_url, by_key = seeded.new
	sheet = write_sheet(
		tmp_path / 'export.csv',
		[
			sheet_row(
				title='Software Engineer',
				company='Acme',
				published='2026-11-09',
				locations='Worldwide',
				role='software',
				source='test',
				url='https://example.com/one',
				status='applied',
			),
			# same job, moved url: only the folded title/company can find it
			sheet_row(
				title='Web Developer',
				company='Beta',
				published='2026-11-10',
				role='web',
				source='other',
				url='https://other.example/two',
				status='to apply',
			),
			# nothing in the database: inserted from the CSV columns
			sheet_row(
				title='Platform Engineer',
				company='Gamma',
				published='2026-11-01',
				locations='London, Remote',
				salary='£100k',
				role='software',
				source='somewhere',
				url='https://gamma.example/job',
				status='skip',
			),
			# the first row again: a no-op, not a second write
			sheet_row(
				title='Software Engineer',
				company='Acme',
				role='software',
				url='https://example.com/one',
				status='applied',
			),
		],
	)
	assert import_sheet(ws, sheet) == 0
	out = capsys.readouterr().out
	assert 'matched 2 · inserted 1 · skipped 1' in out
	assert 'already imported in this file (same url)' in out

	assert store.get(ws.db, by_url['id'])['status'] == 'applied'
	assert store.get(ws.db, by_key['id'])['status'] == 'to_apply'
	assert sum(store.counts(ws.db).values()) == 3

	inserted = store.query(ws.db, search='Platform Engineer')[0]
	assert inserted['source'] == 'sheet-import'  # not scraped: not a board
	assert inserted['status'] == 'skipped'
	assert inserted['role'] == 'software'
	assert inserted['salary'] == '£100k'
	assert inserted['locations'] == ['London', 'Remote']
	assert inserted['published'] == '2026-11-01T00:00:00+00:00'
	assert (inserted['year'], inserted['month']) == (2026, 11)

	# the import says who moved the status, and the duplicate added nothing
	assert main(['--workspace', str(ws.root), 'show', str(by_url['id']), '--json']) == 0
	shown = json.loads(capsys.readouterr().out)
	assert [(change['old'], change['new'], change['actor']) for change in shown['history']] == [
		('new', 'applied', 'import')
	]


def test_import_sheet_reads_headers_by_name_and_keeps_a_notes_column(ws, job, tmp_path, capsys):
	result = store.ingest([job(url='https://example.com/one')], ws.db)
	job_id = result.new[0]['id']
	sheet = write_sheet(
		tmp_path / 'export.csv',
		[
			[
				'Software Engineer',
				'Acme',
				'software',
				'https://example.com/one',
				'applied',
				'recruiter called',
				'phone screen',
			]
		],
		# re-cased, re-ordered, extra columns — still the same export
		header=(' Title ', 'COMPANY', 'Role', 'URL', 'Status', 'Notes', 'apply status'),
	)
	assert import_sheet(ws, sheet) == 0
	out = capsys.readouterr().out
	assert 'matched 1 · inserted 0 · skipped 0' in out
	assert "ignored the 'apply status' column on 1 row(s)" in out
	assert store.get(ws.db, job_id)['notes'] == 'recruiter called'

	# a second run is a no-op: the same note is not appended twice
	assert import_sheet(ws, sheet) == 0
	capsys.readouterr()
	assert store.get(ws.db, job_id)['notes'] == 'recruiter called'


def test_import_sheet_lists_an_unknown_status_and_leaves_it_alone(ws, job, tmp_path, capsys):
	result = store.ingest([job(url='https://example.com/one')], ws.db)
	job_id = result.new[0]['id']
	sheet = write_sheet(
		tmp_path / 'export.csv',
		[
			sheet_row(
				title='Software Engineer',
				company='Acme',
				role='software',
				url='https://example.com/one',
				status='maybe',
			),
			sheet_row(
				title='Boundary Engineer',
				company='Delta',
				role='software',
				url='https://delta.example/job',
				status='maybe',
			),
		],
	)
	assert import_sheet(ws, sheet) == 0
	out = capsys.readouterr().out
	assert 'matched 1 · inserted 1 · skipped 0' in out
	assert out.count("'maybe'") == 2  # one line per offending row
	assert store.get(ws.db, job_id)['status'] == 'new'
	inserted = store.query(ws.db, search='Boundary Engineer')[0]
	assert inserted['status'] == 'new'  # an insert still defaults to new


def test_import_sheet_does_not_guess_between_two_jobs_with_the_same_key(ws, job, tmp_path, capsys):
	first = job(
		title='Software Engineer',
		company='Acme',
		role='software',
		url='https://example.com/one',
	)
	second = job(
		title='Software Engineer',
		company='Acme',
		role='python',
		url='https://example.com/two',
	)
	seeded = store.ingest([first, second], ws.db)
	sheet = write_sheet(
		tmp_path / 'export.csv',
		[
			# the key pair is shared, so the url is the only safe match
			sheet_row(
				title='Software Engineer',
				company='Acme',
				role='software',
				url='https://moved.example/job',
				status='applied',
			),
			# this one names a url, and a url beats the ambiguity
			sheet_row(
				title='Software Engineer',
				company='Acme',
				role='python',
				url='https://example.com/two',
				status='applied',
			),
		],
	)
	assert import_sheet(ws, sheet) == 0
	out = capsys.readouterr().out
	assert 'matched 1 · inserted 0 · skipped 1' in out
	assert 'ambiguous' in out and '2 jobs' in out
	statuses = [store.get(ws.db, entry['id'])['status'] for entry in seeded.new]
	assert statuses == ['new', 'applied']  # only the url match was written


def test_import_sheet_dry_run_reports_and_writes_nothing(ws, job, tmp_path, capsys):
	result = store.ingest([job(url='https://example.com/one')], ws.db)
	job_id = result.new[0]['id']
	sheet = write_sheet(
		tmp_path / 'export.csv',
		[
			sheet_row(
				title='Software Engineer',
				company='Acme',
				role='software',
				url='https://example.com/one',
				status='applied',
			),
			sheet_row(
				title='Boundary Engineer',
				company='Delta',
				role='software',
				url='https://delta.example/job',
				status='skip',
			),
		],
	)
	assert import_sheet(ws, sheet, '--dry-run') == 0
	out = capsys.readouterr().out
	assert 'dry run — nothing written' in out
	assert 'matched 1 · inserted 1 · skipped 0' in out
	assert store.get(ws.db, job_id)['status'] == 'new'
	assert sum(store.counts(ws.db).values()) == 1


def test_import_sheet_dry_run_does_not_even_create_the_database(ws, tmp_path, capsys):
	sheet = write_sheet(
		tmp_path / 'export.csv',
		[
			sheet_row(
				title='Software Engineer',
				company='Acme',
				role='software',
				url='https://example.com/one',
				status='none',
			)
		],
	)
	assert not ws.db.exists()
	assert import_sheet(ws, sheet, '--dry-run') == 0
	assert 'inserted 1' in capsys.readouterr().out
	assert not ws.db.exists()


def test_import_sheet_missing_file_is_a_clean_error(ws, tmp_path, capsys):
	code = import_sheet(ws, tmp_path / 'nope.csv')
	assert code == 2
	err = capsys.readouterr().err
	assert 'error:' in err
	assert 'not a file' in err


def test_import_sheet_missing_a_column_names_it(ws, tmp_path, capsys):
	sheet = write_sheet(
		tmp_path / 'export.csv',
		[['Software Engineer', 'Acme', 'https://example.com/one', 'new']],
		header=('title', 'company', 'url', 'status'),
	)
	code = import_sheet(ws, sheet)
	assert code == 2
	err = capsys.readouterr().err
	assert "missing the 'role' column" in err
	assert 'title, company, published' in err  # names the expected header


def test_import_sheet_skips_a_row_without_a_title_or_url(ws, tmp_path, capsys):
	"""Separator rows and hand-added totals were never jobs; a short
	row is read as empty cells rather than crashing the whole file."""
	sheet = write_sheet(
		tmp_path / 'export.csv',
		[
			['Software Engineer', 'Acme'],  # no url, no rest
			sheet_row(
				title='Boundary Engineer',
				company='Delta',
				role='software',
				url='https://delta.example/job',
				status='none',
			),
		],
	)
	assert import_sheet(ws, sheet) == 0
	out = capsys.readouterr().out
	assert 'inserted 1 · skipped 1' in out
	assert 'no title or url' in out
