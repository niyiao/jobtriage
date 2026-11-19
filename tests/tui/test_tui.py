"""Headless tests for the triage TUI: the wiring, driven without a
terminal.

Textual's Pilot needs a running event loop and pytest-asyncio is not a
dev dependency, so the tests below construct TriageApp and call the same
handlers the keys call (`_loaded`, `action_*`, the widget renderers,
`frame`). `_repaint` is a no-op before compose(), so none of this needs
a screen. What that covers is exactly what the database and the module
split touched: the tracker is store.Tracker, a job is addressed by `id`
instead of a sheet `row`, statuses are slugs with labels painted from
the status table, a status change is written the moment it is made, and
a failed write is a footer line rather than a queued retry. Terminal
rendering (widths, colours) is deliberately not pinned beyond the label
strings a person would notice; tests/tui/test_pilot.py drives the keys.
"""

import asyncio
import importlib.resources
import time

from jobtriage import config, status, store, tui
from jobtriage.tui import TriageApp, keys, widgets


class Opener:
	"""The browser stand-in: these tests never press enter, but the app
	takes an opener and must be constructible with one."""

	def open_or_focus(self, url, remembered):
		return remembered or 'tab-1'


class FlakyTracker:
	"""A tracker whose first write fails, the way a locked database
	would: the app must keep going and say so in the footer."""

	def __init__(self, jobs):
		self.jobs = jobs
		self.calls = 0

	def load(self):
		return self.jobs

	def set_status(self, job_id, slug, actor='tui'):
		self.calls += 1
		if self.calls == 1:
			raise RuntimeError('database is locked')


def _entry(job_id=1, status='new', title='Job', published='2026-11-01'):
	"""One job as Tracker.load() hands it to the TUI: an id, a slug
	status and the 'undated' published fallback."""
	return {
		'id': job_id,
		'title': title,
		'company': 'Acme',
		'url': f'https://example.com/{job_id}',
		'published': published or 'undated',
		'status': status,
		'source': 'test',
	}


def _app(tracker):
	app = TriageApp(tracker, opener=Opener())
	app._loaded(tracker.load())
	return app


# -- the vocabulary is the status table's -------------------------------


def test_the_tui_vocabulary_comes_from_the_status_table():
	assert tui.STATUSES == tuple(entry.slug for entry in status.STATUSES)
	# The tab order is the workflow, not the status table's cycle.
	assert tui.FILTERS == ('new', 'to_apply', 'applied', 'skipped', 'all')
	assert set(tui.FILTERS) == {*tui.STATUSES, 'all'}
	assert tui.DEFAULT_FILTER == 'to_apply'
	assert tui.STATUS_STYLES == {entry.slug: entry.colour for entry in status.STATUSES}
	assert tui.LABELS == {entry.slug: entry.label for entry in status.STATUSES}


def test_jobs_are_addressed_by_id_and_start_on_to_apply(job, db_path):
	store.ingest(
		[
			job(title='Already applied', company='A', published='2026-11-01'),
			job(title='Worth a look', company='B', published='2026-11-09'),
		],
		db_path,
	)
	applied = store.query(db_path, search='Already applied')[0]
	store.set_status(db_path, applied['id'], 'applied', 'cli')
	worth = store.query(db_path, search='Worth a look')[0]
	store.set_status(db_path, worth['id'], 'to_apply', 'cli')

	app = _app(store.Tracker(db_path))

	assert app._state == 'ready'
	assert app._filter == 'to_apply'  # the slug, not the label
	assert [entry['title'] for entry in app._view] == ['Worth a look']
	assert all('id' in entry and 'row' not in entry for entry in app._jobs)


# -- status changes are written at once ---------------------------------


def test_a_status_key_writes_the_slug_and_moves_on(job, db_path):
	store.ingest(
		[
			job(title='Newest', company='A', published='2026-11-09'),
			job(title='Older', company='B', published='2026-11-01'),
		],
		db_path,
	)
	for entry in store.query(db_path):
		store.set_status(db_path, entry['id'], 'to_apply', 'cli')

	app = _app(store.Tracker(db_path))
	newest, older = app._view

	app.action_status('applied')  # leaves the to_apply filter immediately

	assert store.get(db_path, newest['id'])['status'] == 'applied'
	assert [entry['id'] for entry in app._view] == [older['id']]
	assert app._view[app._selection]['id'] == older['id']
	assert app._write_error is None


def test_skip_writes_the_slug_and_flashes(job, db_path):
	store.ingest([job(title='Skip me')], db_path)
	app = _app(store.Tracker(db_path))
	app._filter = 'all'
	app._rebuild()
	entry = app._view[0]
	app.action_status('skipped')

	assert store.get(db_path, entry['id'])['status'] == 'skipped'
	assert app._flash == 'skip · Skip me'


def test_space_twice_marks_applied(job, db_path):
	store.ingest(
		[
			job(title='First', company='A', published='2026-11-09'),
			job(title='Second', company='B', published='2026-11-01'),
		],
		db_path,
	)
	for entry in store.query(db_path):
		store.set_status(db_path, entry['id'], 'to_apply', 'cli')
	app = _app(store.Tracker(db_path))
	first = app._view[0]

	app.action_space()  # one space only arms the double press
	assert store.get(db_path, first['id'])['status'] == 'to_apply'
	app.action_space()

	assert store.get(db_path, first['id'])['status'] == 'applied'
	assert app._view and app._view[0]['title'] == 'Second'


# -- a failed write is a footer line, not a queue -----------------------


def test_a_failed_write_is_a_footer_line_and_the_job_keeps_its_status():
	tracker = FlakyTracker([_entry(title='Locked job', status='to_apply')])
	app = _app(tracker)

	app.action_status('skipped')  # the write raises

	entry = tracker.jobs[0]
	assert entry['status'] == 'to_apply'  # the database did not move
	assert 'saved' not in entry  # no staging marker survives
	assert 'write failed' in widgets.foot_line(app, 80).plain
	assert 'Locked job' in widgets.foot_line(app, 80).plain
	assert app._state == 'ready'  # a footer line, not a crash
	assert app._view[0] is entry  # the cursor stayed on the undecided job

	app.action_down()  # still drivable
	assert app._selection == 0  # one job, so the cursor clamps


# -- wording and painting -----------------------------------------------


def test_the_messages_say_database_not_sheet(db_path):
	app = TriageApp(store.Tracker(db_path), opener=Opener())

	assert 'loading the database…' in app.frame(80, 24).plain

	app._loaded([])

	assert 'sheet' not in app.frame(80, 24).plain.casefold()
	assert widgets.status_line(app, 80).plain.strip() == '0 jobs in the database'


def test_the_filters_and_rows_show_labels_not_slugs(job, db_path):
	store.ingest([job()], db_path)
	app = _app(store.Tracker(db_path))

	assert widgets.filters_line(app, 80).plain == ('new 1  to apply 0  applied 0  skip 0  all 1')
	app._filter = 'all'
	app._rebuild()
	app.action_status('skipped')  # written at once, cursor moves on
	line = widgets.job_line(app, app._view[0], 80, False).plain
	assert 'skip' in line
	assert '*' not in line  # the staging marker is gone
	assert store.get(db_path, app._view[0]['id'])['status'] == 'skipped'


def test_the_date_column_shows_the_day_not_the_timestamp(job, db_path):
	"""The sheet held dates; the database holds ISO timestamps. The
	row keeps the same eleven-character date column."""
	store.ingest([job(published='2026-11-10')], db_path)
	app = _app(store.Tracker(db_path))
	app._filter = 'all'
	app._rebuild()

	line = widgets.job_line(app, app._view[0], 80, False).plain

	assert '2026-11-10' in line
	assert 'T00:00:00' not in line


def test_undated_jobs_sort_after_every_date():
	jobs = [
		_entry(title='Undated', published='undated'),
		_entry(title='Old', published='2026-11-01'),
		_entry(title='New', published='2026-11-09'),
	]
	assert [entry['title'] for entry in TriageApp._sort(jobs)] == [
		'New',
		'Old',
		'Undated',
	]


# -- the composed screen ------------------------------------------------


def test_the_frame_is_the_widgets_lines_in_order(job, db_path):
	store.ingest([job(title='One')], db_path)
	app = _app(store.Tracker(db_path))

	lines = app.frame(80, 24).plain.split('\n')

	assert len(lines) == 24
	assert lines[0] == widgets.filters_line(app, 80).plain
	assert lines[1] == '─' * 80
	assert lines[-2] == widgets.status_line(app, 80).plain
	assert lines[-1] == widgets.foot_line(app, 80).plain


def test_a_terminal_too_small_says_so():
	app = TriageApp(store.Tracker('unused'), opener=Opener())

	assert app.frame(80, 4).plain == widgets.TOO_SMALL
	assert app.frame(10, 24).plain == widgets.TOO_SMALL


def test_the_frame_splits_for_the_details_pane_at_wide_widths(job, db_path):
	store.ingest([job(title='One', company='Acme')], db_path)
	app = _app(store.Tracker(db_path))
	app._filter = 'all'
	app._rebuild()

	# The width default applies until a toggle: 160 shows the pane, 80
	# does not, and the pane's columns sit beside the list.
	wide = app.frame(160, 40).plain
	assert '│' in wide
	assert 'Acme' in wide and 'first seen' in wide and 'history' in wide
	assert len(wide.split('\n')) == 40
	assert '│' not in app.frame(80, 24).plain

	app._details_user = False  # a hand toggle beats the width
	assert '│' not in app.frame(160, 40).plain
	app._details_user = True
	assert '│' in app.frame(80, 24).plain


# -- it mounts and loads from the database ------------------------------


def test_the_app_mounts_and_loads(job, db_path):
	"""One smoke test through Textual itself: the bindings, compose and
	the startup worker all survive being run for real, headlessly."""
	store.ingest([job(title='Mounted')], db_path)
	mounted = store.query(db_path)[0]
	store.set_status(db_path, mounted['id'], 'to_apply', 'cli')
	app = TriageApp(store.Tracker(db_path), opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			# A wall-clock deadline, not an iteration count: the budget
			# must not depend on how fast the runner pumps messages —
			# CI Windows needs real seconds for the startup worker.
			deadline = time.monotonic() + 30.0
			while app._state != 'ready' and time.monotonic() < deadline:
				await pilot.pause(0.05)
			assert app._state == 'ready'
			assert [entry['title'] for entry in app._jobs] == ['Mounted']
			# The live screen renders through the widgets, not one Panel.
			assert app._job_list is not None
			assert 'Mounted' in app._job_list.render().plain
			assert app._tabs_bar is not None and app._tabs_bar.size.height == 2
			# Tabs, body (the Horizontal's room for a side pane), status, footer.
			assert app._job_list.size == (80, 20)
			assert app._status_line is not None and app._status_line.size == (80, 1)
			assert app._footer is not None and app._footer.size == (80, 1)

	asyncio.run(scenario())


# -- the key table is the one source ------------------------------------


def test_every_key_row_has_a_help_line_and_a_unique_name():
	names = [entry.name for entry in keys.KEY_TABLE]

	assert len(names) == len(set(names))
	for entry in keys.KEY_TABLE:
		assert entry.help.strip(), f'{entry.name} has no help line'
		assert entry.keys, f'{entry.name} has no key'


def test_the_status_keys_are_named_by_slug_in_workflow_order():
	status_rows = [entry.name for entry in keys.KEY_TABLE[: len(keys.STATUSES)]]

	assert status_rows == [name for name in keys.FILTERS if name != 'all']
	assert all(entry.name in keys.STATUS_KEYS for entry in keys.KEY_TABLE[: len(keys.STATUSES)])


def test_every_row_becomes_a_priority_binding():
	bindings = keys.bindings(keys.KEY_TABLE)
	key_count = sum(len(entry.keys) for entry in keys.KEY_TABLE)

	assert len(bindings) == key_count
	assert all(binding.priority for binding in bindings)
	# q, ctrl+c and ctrl+q all quit; aliases are still bindings.
	assert {binding.key for binding in bindings if binding.action == 'quit'} == {
		'q',
		'ctrl+c',
		'ctrl+q',
	}


# -- the footer and the overlay come from the table ---------------------


def test_the_footer_fits_and_ends_in_help():
	app = TriageApp(store.Tracker('unused'), opener=Opener())
	line = widgets.foot_line(app, 80).plain

	assert len(line) <= 80
	assert line.endswith('? help')
	assert 's skip' in line  # the status keys are the everyday keys
	assert line == keys.footer(app._keys, 80)


def test_the_footer_shows_a_pending_g():
	app = TriageApp(store.Tracker('unused'), opener=Opener())
	app._pending_g = time.monotonic()

	line = widgets.foot_line(app, 80).plain

	assert line.startswith('g…')
	assert len(line) <= 80


def test_the_footer_follows_a_rebound_key():
	app = TriageApp(
		store.Tracker('unused'),
		opener=Opener(),
		config=config.TuiSettings(keys={'skipped': 'x'}),
	)

	line = widgets.foot_line(app, 80).plain

	assert 'x skip' in line
	assert 's skip' not in line


# -- config: defaults, rebinds, and fail-fast errors --------------------


def test_a_missing_config_file_means_defaults(tmp_path):
	settings = config.load_tui(tmp_path / 'jobtriage.toml')

	assert settings.default_filter == status.DEFAULT_FILTER
	assert settings.keys == {}


def test_an_empty_config_file_means_defaults(tmp_path):
	path = tmp_path / 'jobtriage.toml'
	path.write_text('# not written yet\n')

	settings = config.load_tui(path)

	assert settings.default_filter == status.DEFAULT_FILTER
	assert settings.keys == {}


def test_the_tui_table_is_read_and_rebinds_keys(tmp_path):
	path = tmp_path / 'jobtriage.toml'
	path.write_text(
		'[tui]\ndefault_filter = "new"\n\n[tui.keys]\nskipped = "x"\nhalf_down = "ctrl+f"\n'
	)

	settings = config.load_tui(path)

	assert settings.default_filter == 'new'
	assert settings.keys == {'skipped': 'x', 'half_down': 'ctrl+f'}
	resolved = {entry.name: entry.keys for entry in keys.resolve(settings.keys)}
	assert resolved['skipped'] == ('x',)
	assert resolved['half_down'] == ('ctrl+f',)


def test_an_unknown_action_names_the_file_and_the_action(tmp_path):
	path = tmp_path / 'jobtriage.toml'
	path.write_text('[tui.keys]\nskippd = "x"\n')

	try:
		config.load_tui(path)
	except config.ConfigError as error:
		message = str(error)
		assert str(path) in message
		assert 'skippd' in message
	else:
		raise AssertionError('an unknown action was accepted')


def test_an_invalid_key_names_the_file_and_the_key(tmp_path):
	path = tmp_path / 'jobtriage.toml'
	path.write_text('[tui.keys]\nskipped = "ctrl+wat"\n')

	try:
		config.load_tui(path)
	except config.ConfigError as error:
		message = str(error)
		assert str(path) in message
		assert 'ctrl+wat' in message
	else:
		raise AssertionError('an invalid key was accepted')


def test_a_colliding_key_names_both_actions(tmp_path):
	path = tmp_path / 'jobtriage.toml'
	path.write_text('[tui.keys]\nskipped = "S"\n')

	try:
		config.load_tui(path)
	except config.ConfigError as error:
		message = str(error)
		assert str(path) in message
		assert 'S' in message
		assert 'skipped' in message and 'skipped_shift' in message
	else:
		raise AssertionError('a colliding key was accepted')


def test_an_unknown_default_filter_is_refused(tmp_path):
	path = tmp_path / 'jobtriage.toml'
	path.write_text('[tui]\ndefault_filter = "everything"\n')

	try:
		config.load_tui(path)
	except config.ConfigError as error:
		assert 'everything' in str(error)
	else:
		raise AssertionError('an unknown tab was accepted')


def test_the_shipped_template_documents_key_rebinds():
	template = (importlib.resources.files('jobtriage') / 'assets' / 'jobtriage.toml').read_text()

	assert '[tui.keys]' in template
	assert '# skipped = "x"' in template
