"""Pilot tests: the key map driven through Textual itself.

Textual's Pilot needs a running event loop, and pytest-asyncio is not a
dev dependency, so every test wraps its scenario in asyncio.run(). The
tracker is an in-memory fake shaped like store.Tracker (load hands out
copies, set_status lands a row) and the opener is a fake Chrome, so a
test can see exactly what the keys did without a database or a browser.
The one exception is undo's history row: that test uses the real
database, because the point is the row store writes.

What is pinned here is plan 06's key map: vim navigation and its
chords, tabs on the arrows, one key per status with undo, the help
overlay, and the footer generated from the same table the keys are.
"""

import asyncio
import json
import threading

from jobtriage import chrome, config, db, store, workspace
from jobtriage.cli.tui import opener_for
from jobtriage.config import TuiSettings
from jobtriage.tui import TriageApp, keys, widgets
from jobtriage.tui.help import HelpScreen


class FakeTracker:
	"""store.Tracker's shape without SQLite.

	load() returns fresh dicts so the app cannot mutate the store behind
	set_status's back — a real Tracker re-reads every time — and
	set_status records the decision. `fail_next()` makes the next write
	raise, the way a locked database would.
	"""

	def __init__(self, entries):
		self.rows = [dict(entry) for entry in entries]
		self.calls = []
		self.loads = 0
		self.failures = 0
		self.histories = {}  # job id → the rows the details pane reads
		self.attachments_by_job = {}
		self.notes = []

	def load(self):
		self.loads += 1
		return [dict(row) for row in self.rows]

	def set_status(self, job_id, status, actor='tui'):
		if self.failures:
			self.failures -= 1
			raise RuntimeError('database is locked')
		row = next(row for row in self.rows if row['id'] == job_id)
		row['status'] = status
		self.calls.append((job_id, status, actor))

	def history(self, job_id):
		return [dict(row) for row in self.histories.get(job_id, [])]

	def attachments(self, job_id):
		return [dict(row) for row in self.attachments_by_job.get(job_id, [])]

	def add_note(self, job_id, text):
		row = next(row for row in self.rows if row['id'] == job_id)
		row['notes'] = f'{row["notes"]}\n{text}' if row.get('notes') else text
		self.notes.append((job_id, text))
		return row['notes']

	def fail_next(self):
		self.failures = 1


class Opener:
	"""The browser stand-in: remembers the tab it was told about, mints
	one otherwise, and keeps every call so a test can see the remembered
	id arrive on the second enter."""

	def __init__(self):
		self.calls = []

	def open_or_focus(self, url, remembered=None):
		self.calls.append((url, remembered))
		return remembered or f'tab-{len(self.calls)}'


class FakeScrape:
	"""The pipeline's stand-in for `R`, with no network and no threads of
	its own.

	It reports every board the way the real one does, then waits on
	`release` so a test can hold a scrape mid-flight and look at the
	footer (and press R a second time) before it lands. On release it
	appends `new_jobs` to the tracker's rows — the effect a real ingest
	has — and returns how many it added, which is what the app flashes.
	"""

	def __init__(self, tracker, new_jobs=(), boards=('aa', 'bb')):
		self.tracker = tracker
		self.new_jobs = list(new_jobs)
		self.boards = boards
		self.calls = 0
		self.release = threading.Event()

	def __call__(self, on_board=None):
		self.calls += 1
		total = len(self.boards)
		for done, name in enumerate(self.boards, 1):
			if on_board is not None:
				on_board(name, done, total)
		if not self.release.wait(5):  # never released: the test hung
			raise RuntimeError('the fake scrape was never released')
		for job in self.new_jobs:
			self.tracker.rows.append(dict(job))
		return len(self.new_jobs)


async def _scraped(pilot, app):
	"""Wait for the scrape's reload to land: not scraping, view ready."""
	await _until(pilot, lambda: not app._scraping and app._state == 'ready')


def entry(job_id=1, status='new', title='Job', company='Acme', published='2026-11-01'):
	"""One job as Tracker.load() hands it over."""
	return {
		'id': job_id,
		'title': title,
		'company': company,
		'url': f'https://example.com/{job_id}',
		'published': published,
		'status': status,
		'source': 'test',
	}


def _run(scenario):
	asyncio.run(scenario())


async def _until(pilot, condition):
	for _ in range(300):
		if condition():
			return
		await pilot.pause(0.01)
	raise AssertionError('the app never reached the expected state')


async def _ready(pilot, app):
	await _until(pilot, lambda: app._state == 'ready')


async def _settled(pilot, app):
	await _until(pilot, lambda: not app._opening)


def _titles(app):
	return [job['title'] for job in app._view]


# -- it opens on the configured filter ----------------------------------


def test_it_opens_on_to_apply():
	tracker = FakeTracker(
		[
			entry(1, 'new', 'Fresh'),
			entry(2, 'to_apply', 'Worth a look'),
			entry(3, 'applied', 'Already applied'),
		]
	)
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert app._filter == 'to_apply'
			assert _titles(app) == ['Worth a look']

	_run(scenario)


def test_it_opens_on_the_configured_default_filter():
	tracker = FakeTracker([entry(1, 'new', 'Fresh'), entry(2, 'to_apply', 'Queue')])
	app = TriageApp(
		tracker,
		opener=Opener(),
		config=TuiSettings(default_filter='new'),
	)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert app._filter == 'new'
			assert _titles(app) == ['Fresh']

	_run(scenario)


# -- moving -------------------------------------------------------------


def test_j_and_k_move_and_clamp_at_both_ends():
	tracker = FakeTracker([entry(index, 'to_apply', f'Job {index}') for index in range(1, 41)])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert app._selection == 0

			await pilot.press('j', 'j')
			assert app._selection == 2
			assert app._view[app._selection]['title'] == 'Job 3'

			await pilot.press('k')
			assert app._selection == 1

			await pilot.press('k', 'k')  # clamped at the top
			assert app._selection == 0

			await pilot.press('down')
			assert app._selection == 1
			await pilot.press('up')
			assert app._selection == 0

	_run(scenario)


def test_pages_and_half_screens_move_and_clamp():
	tracker = FakeTracker([entry(index, 'to_apply', f'Job {index}') for index in range(1, 41)])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			# 80×24: four rows outside the list, so the body is 20 and
			# half a screen is 10.
			assert app._job_list is not None and app._job_list.size.height == 20
			assert app._half_page() == 10

			await pilot.press('ctrl+d')
			assert app._selection == 10
			await pilot.press('ctrl+u')
			assert app._selection == 0

			await pilot.press('pagedown')
			assert app._selection == 30
			await pilot.press('pagedown')
			assert app._selection == 39  # clamped at the bottom
			await pilot.press('pageup')
			assert app._selection == 9
			await pilot.press('pageup')
			assert app._selection == 0

	_run(scenario)


def test_gg_and_G_work_from_any_position():
	tracker = FakeTracker([entry(index, 'to_apply', f'Job {index}') for index in range(1, 41)])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)

			await pilot.press('G')
			assert app._selection == 39
			await pilot.press('g', 'g')
			assert app._selection == 0

			await pilot.press('j', 'j', 'j')
			await pilot.press('G')
			assert app._selection == 39
			await pilot.press('g', 'g')
			assert app._selection == 0

	_run(scenario)


def test_a_lone_g_does_not_jump_and_j_clears_it():
	tracker = FakeTracker([entry(index, 'to_apply', f'Job {index}') for index in range(1, 6)])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('j', 'j')
			assert app._selection == 2

			await pilot.press('g')
			assert app._g_pending()
			assert 'g…' in app.frame(80, 24).plain.split('\n')[-1]

			await pilot.press('j')  # not the chord: move one, don't jump
			assert app._selection == 3
			assert not app._g_pending()
			assert 'g…' not in app.frame(80, 24).plain.split('\n')[-1]

			await pilot.press('g', 'g')  # a fresh g starts a fresh chord
			assert app._selection == 0

	_run(scenario)


# -- tabs ---------------------------------------------------------------


def test_tab_and_shift_tab_cycle_the_filters_in_workflow_order():
	tracker = FakeTracker([entry()])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert app._filter == 'to_apply'

			await pilot.press('tab')
			assert app._filter == 'applied'
			await pilot.press('tab')
			assert app._filter == 'skipped'
			await pilot.press('tab')
			assert app._filter == 'all'
			await pilot.press('tab')
			assert app._filter == 'new'

			await pilot.press('shift+tab')
			assert app._filter == 'all'
			await pilot.press('shift+tab')
			assert app._filter == 'skipped'

	_run(scenario)


def test_arrows_and_h_l_cycle_tabs_and_never_change_a_status():
	tracker = FakeTracker(
		[entry(1, 'new', 'Fresh'), entry(2, 'to_apply', 'Queue'), entry(3, 'applied', 'Done')]
	)
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert app._filter == 'to_apply'

			await pilot.press('right')
			assert app._filter == 'applied'
			await pilot.press('l')
			assert app._filter == 'skipped'
			await pilot.press('left')
			assert app._filter == 'applied'
			await pilot.press('h')
			assert app._filter == 'to_apply'

			assert tracker.calls == []  # arrows never write a status

	_run(scenario)


def test_number_keys_jump_to_tabs():
	tracker = FakeTracker([entry()])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)

			for number, expected in enumerate(keys.FILTERS, 1):
				await pilot.press(str(number))
				assert app._filter == expected

			assert tracker.calls == []

	_run(scenario)


# -- status keys and undo -----------------------------------------------


def test_t_saves_and_moves_the_cursor_to_the_job_below_then_u_puts_both_back():
	tracker = FakeTracker(
		[
			entry(1, 'new', 'First'),
			entry(2, 'new', 'Second'),
			entry(3, 'new', 'Third'),
		]
	)
	app = TriageApp(tracker, opener=Opener(), config=TuiSettings(default_filter='new'))

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert _titles(app) == ['First', 'Second', 'Third']

			await pilot.press('t')

			assert tracker.calls == [(1, 'to_apply', 'tui')]
			assert _titles(app) == ['Second', 'Third']
			assert app._selection == 0  # 'Second' was below 'First'

			await pilot.press('u')

			assert tracker.calls[-1] == (1, 'new', 'tui-undo')
			assert _titles(app) == ['First', 'Second', 'Third']
			assert app._view[app._selection]['title'] == 'First'

			await pilot.press('u')  # the stack is repeatable: this one was undone
			assert app._flash == 'nothing to undo'

	_run(scenario)


def test_space_twice_is_the_hidden_applied_alias():
	tracker = FakeTracker([entry(1, 'to_apply', 'First'), entry(2, 'to_apply', 'Second')])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)

			await pilot.press('space')
			assert tracker.calls == []  # one space only arms the double press

			await pilot.press('space')
			assert tracker.calls == [(1, 'applied', 'tui')]
			assert _titles(app) == ['Second']
			assert app._flash == 'applied · First'

	_run(scenario)


def test_shift_s_is_the_hidden_skip_alias():
	tracker = FakeTracker([entry(1, 'to_apply', 'First'), entry(2, 'to_apply', 'Second')])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)

			await pilot.press('S')

			assert tracker.calls == [(1, 'skipped', 'tui')]
			assert _titles(app) == ['Second']

	_run(scenario)


def test_undo_restores_the_tab_it_was_changed_on():
	tracker = FakeTracker(
		[entry(1, 'new', 'Fresh'), entry(2, 'to_apply', 'Queue'), entry(3, 'applied', 'Done')]
	)
	app = TriageApp(tracker, opener=Opener(), config=TuiSettings(default_filter='new'))

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('t')  # Fresh leaves the new tab
			assert app._filter == 'new' and _titles(app) == []

			await pilot.press('2')  # wander to to_apply
			assert app._filter == 'to_apply'

			await pilot.press('u')  # undo restores the status *and* the tab

			assert app._filter == 'new'
			assert _titles(app) == ['Fresh']
			assert app._view[app._selection]['title'] == 'Fresh'

	_run(scenario)


def test_a_status_key_on_the_all_tab_moves_on_too():
	tracker = FakeTracker([entry(1, 'new', 'First'), entry(2, 'to_apply', 'Second')])
	app = TriageApp(tracker, opener=Opener(), config=TuiSettings(default_filter='all'))

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert app._view[app._selection]['title'] == 'First'

			await pilot.press('a')

			assert tracker.calls == [(1, 'applied', 'tui')]
			assert _titles(app) == ['First', 'Second']  # all keeps both
			assert app._view[app._selection]['title'] == 'Second'

	_run(scenario)


def test_a_failed_write_is_a_footer_line_and_is_not_undoable():
	tracker = FakeTracker([entry(1, 'to_apply', 'Locked job')])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			tracker.fail_next()
			await pilot.press('s')

			assert 'write failed' in app.frame(80, 24).plain
			assert 'Locked job' in app.frame(80, 24).plain
			assert app._undo == []  # nothing landed, so nothing to undo

	_run(scenario)


# -- undo's history row, with the real store ----------------------------


def test_undo_writes_a_tui_undo_history_row(job, db_path):
	store.ingest(
		[
			job(title='First', company='A', published='2026-11-09'),
			job(title='Second', company='B', published='2026-11-01'),
		],
		db_path,
	)
	first = store.query(db_path, search='First')[0]
	app = TriageApp(
		store.Tracker(db_path),
		opener=Opener(),
		config=TuiSettings(default_filter='new'),
	)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert app._view[app._selection]['id'] == first['id']

			await pilot.press('t')
			await pilot.press('u')

			assert store.get(db_path, first['id'])['status'] == 'new'

			history = _history(db_path, first['id'])
			assert [row['actor'] for row in history] == ['tui', 'tui-undo']
			assert (history[-1]['old'], history[-1]['new']) == ('to_apply', 'new')

	_run(scenario)


def _history(db_path, job_id):
	"""One job's status_history rows, oldest first — the way cli/show reads
	them."""
	conn = db.connect(db_path)
	try:
		return [
			dict(row)
			for row in conn.execute(
				'SELECT old, new, at, actor FROM status_history WHERE job_id = ? ORDER BY id',
				(job_id,),
			)
		]
	finally:
		conn.close()


# -- help and the footer ------------------------------------------------


def test_question_mark_shows_the_generated_overlay_and_any_key_dismisses():
	tracker = FakeTracker([entry(1, 'to_apply', 'Help me')])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)

			await pilot.press('?')
			await pilot.pause()
			assert isinstance(app.screen, HelpScreen)

			text = app.screen.query_one('#help-text').render().plain
			for row in app._keys:
				assert row.help in text, row.name
			assert any(
				line.startswith('q/^c/^q') and line.rstrip().endswith('quit') for line in text.split('\n')
			)

			await pilot.press('x')  # any key pops it
			await pilot.pause()
			assert not isinstance(app.screen, HelpScreen)

			await pilot.press('?')
			await pilot.pause()
			await pilot.press('q')  # q closes the overlay, it does not quit
			await pilot.pause()
			assert not isinstance(app.screen, HelpScreen)
			assert app._state == 'ready'

	_run(scenario)


def test_the_footer_ends_in_help_and_fits():
	tracker = FakeTracker([entry(1, 'to_apply', 'Fine')])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			for width in (60, 80, 120):
				line = app.frame(width, 24).plain.split('\n')[-1]
				assert line.endswith('? help')
				assert len(line) <= width

	_run(scenario)


# -- chrome, reload, quit ----------------------------------------------


def test_enter_opens_and_a_second_enter_focuses_the_remembered_tab():
	opener = Opener()
	tracker = FakeTracker([entry(7, 'to_apply', 'Open me')])
	app = TriageApp(tracker, opener=opener)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			url = app._view[0]['url']

			await pilot.press('enter')
			await _settled(pilot, app)
			assert opener.calls == [(url, None)]
			assert app._tabs[url] == 'tab-1'

			await pilot.press('enter')
			await _settled(pilot, app)
			assert opener.calls == [(url, None), (url, 'tab-1')]

	_run(scenario)


def test_r_reloads_from_the_tracker():
	tracker = FakeTracker([entry(1, 'to_apply', 'First'), entry(2, 'to_apply', 'Second')])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert _titles(app) == ['First', 'Second']

			tracker.rows[0]['status'] = 'applied'  # the database moved under us
			await pilot.press('r')
			await _ready(pilot, app)
			assert _titles(app) == ['Second']

	_run(scenario)


# -- the background scrape ----------------------------------------------


def test_R_scrapes_in_the_background_and_reloads_when_it_is_done():
	tracker = FakeTracker([entry(1, 'to_apply', 'First')])
	fake = FakeScrape(
		tracker,
		new_jobs=[entry(2, 'to_apply', 'Scraped', published='2026-11-05')],
	)
	app = TriageApp(tracker, opener=Opener(), scrape=fake)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)

			await pilot.press('R')
			await _until(pilot, lambda: app._scrape_progress == (2, 2))

			# The footer counts boards as they land, not at the end.
			assert app._scraping
			assert 'scraping 2/2 boards…' in app.frame(80, 24).plain.split('\n')[-1]

			# A second R during the run is refused: two ingests would race.
			await pilot.press('R')
			assert fake.calls == 1
			assert app._flash == 'already scraping'

			fake.release.set()
			await _until(pilot, lambda: app._flash == '1 new')
			await _scraped(pilot, app)

			# The r path ran: the new job is in the view, cursor kept.
			assert _titles(app) == ['Scraped', 'First']
			assert fake.calls == 1

	_run(scenario)


def test_R_says_zero_new_when_nothing_landed():
	tracker = FakeTracker([entry(1, 'to_apply', 'Only')])
	fake = FakeScrape(tracker, new_jobs=[], boards=('aa',))
	app = TriageApp(tracker, opener=Opener(), scrape=fake)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('R')
			await _until(pilot, lambda: fake.calls == 1)
			fake.release.set()
			await _until(pilot, lambda: app._flash == '0 new')
			await _scraped(pilot, app)
			assert _titles(app) == ['Only']

	_run(scenario)


def test_a_failed_scrape_is_a_footer_line_and_the_app_keeps_going():
	tracker = FakeTracker([entry(1, 'to_apply', 'First')])
	app = TriageApp(tracker, opener=Opener(), scrape=lambda on_board: 1 / 0)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('R')
			await _until(pilot, lambda: not app._scraping)
			assert 'scrape failed' in (app._flash or '')
			assert 'division by zero' in (app._flash or '')
			assert app._state == 'ready'
			assert _titles(app) == ['First']

	_run(scenario)


def test_scrape_is_rebindable_through_the_config(tmp_path):
	path = tmp_path / 'jobtriage.toml'
	path.write_text('[tui.keys]\nscrape = "ctrl+s"\n')
	settings = config.load_tui(path)
	tracker = FakeTracker([entry(1, 'to_apply', 'Only')])
	fake = FakeScrape(tracker, boards=('aa',))
	app = TriageApp(tracker, opener=Opener(), config=settings, scrape=fake)

	async def scenario():
		async with app.run_test(size=(240, 24)) as pilot:
			await _ready(pilot, app)
			assert 'ctrl+s scrape' in keys.footer(app._keys, 240)

			await pilot.press('R')  # unbound now: nothing happens
			assert fake.calls == 0

			await pilot.press('ctrl+s')
			await _until(pilot, lambda: fake.calls == 1)
			fake.release.set()
			await _scraped(pilot, app)

	_run(scenario)


def test_q_quits():
	tracker = FakeTracker([entry(1, 'to_apply', 'Bye')])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('q')
			await pilot.pause()
			assert app._exit

	_run(scenario)


# -- remember where I was ------------------------------------------------


def _state_app(tracker, state_path):
	return TriageApp(
		tracker,
		opener=Opener(),
		config=TuiSettings(default_filter='all'),
		state_path=state_path,
	)


def test_the_tab_search_and_selected_job_are_remembered_across_restarts(tmp_path):
	tracker = FakeTracker(
		[
			entry(1, 'new', 'Platform Engineer', 'Acme', '2026-11-10'),
			entry(2, 'new', 'Designer', 'Globex', '2026-11-09'),
			entry(3, 'new', 'Backend Developer', 'ACME Labs', '2026-11-01'),
		]
	)
	state = tmp_path / 'tui.json'
	first = _state_app(tracker, state)

	async def before():
		async with first.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, first)
			await pilot.press('1')  # the new tab
			await pilot.press('/')
			await pilot.press(*'acme')
			await pilot.press('enter')
			await pilot.press('j')  # the second match
			selected = first._view[first._selection]['id']
			assert first._view[first._selection]['title'] == 'Backend Developer'
			await pilot.press('q')
			return selected

	selected = asyncio.run(before())
	assert json.loads(state.read_text()) == {
		'filter': 'new',
		'search': 'acme',
		'job_id': selected,
	}

	second = _state_app(tracker, state)

	async def after():
		async with second.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, second)
			assert second._filter == 'new'
			assert second._search == 'acme'
			assert '/acme' in second._tabs_bar.render().plain
			assert second._view[second._selection]['id'] == selected

	asyncio.run(after())


def test_a_corrupt_state_file_is_ignored(tmp_path):
	state = tmp_path / 'tui.json'
	state.write_text('{not json at all')
	tracker = FakeTracker([entry(1, 'to_apply', 'Only')])
	app = _state_app(tracker, state)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert app._filter == 'all'  # the config's default, not a crash
			assert app._search == ''
			assert app._view[app._selection]['title'] == 'Only'

	asyncio.run(scenario())


def test_stale_state_degrades_to_the_defaults(tmp_path):
	"""A tab this build no longer has and a job that is gone are the
	same thing: start where a fresh install would."""
	state = tmp_path / 'tui.json'
	state.write_text(json.dumps({'filter': 'interviewing', 'search': '', 'job_id': 999}))
	tracker = FakeTracker([entry(1, 'applied', 'Only')])
	app = _state_app(tracker, state)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert app._filter == 'all'
			assert app._selection == 0

	asyncio.run(scenario())


def test_no_state_path_means_no_persistence_and_no_crash(tmp_path):
	"""The existing tests build the app this way; so does anything that
	does not want a bookmark file."""
	tracker = FakeTracker([entry(1, 'to_apply', 'Only')])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('q')
			await pilot.pause()
			assert app._exit

	asyncio.run(scenario())
	assert list(tmp_path.iterdir()) == []


# -- the browser fallback -----------------------------------------------


def test_a_found_chrome_is_still_the_opener(monkeypatch, tmp_path):
	chrome_path = tmp_path / 'chrome'
	chrome_path.write_text('')  # the fake finder's answer
	monkeypatch.setattr(chrome, 'find_browser', lambda executable='': chrome_path)
	opener = opener_for(workspace.Workspace(tmp_path))
	assert isinstance(opener, chrome.Browser)
	assert opener.focuses_tabs is True


def test_no_chromium_uses_the_default_browser_and_says_focusing_is_unavailable(
	monkeypatch, tmp_path
):
	monkeypatch.setattr(chrome, 'find_browser', lambda executable='': None)
	opener = opener_for(workspace.Workspace(tmp_path))
	assert isinstance(opener, chrome.DefaultBrowser)

	opened = []
	monkeypatch.setattr(chrome.webbrowser, 'open', opened.append)
	tracker = FakeTracker([entry(7, 'to_apply', 'Open me')])
	app = TriageApp(tracker, opener=opener)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('enter')
			await _settled(pilot, app)

			assert opened == ['https://example.com/7']
			assert app._flash is not None and 'focusing' in app._flash
			assert app._tabs == {}  # no tab id to remember

	asyncio.run(scenario())


# -- [tui.keys] rebinds every place the key appears ---------------------


def test_rebinding_skipped_changes_the_key_the_help_and_the_footer():
	tracker = FakeTracker([entry(1, 'to_apply', 'First'), entry(2, 'to_apply', 'Second')])
	app = TriageApp(
		tracker,
		opener=Opener(),
		config=TuiSettings(keys={'skipped': 'x'}),
	)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			line = app._footer.render().plain
			assert 'x skip' in line and 's skip' not in line

			await pilot.press('?')
			await pilot.pause()
			text = app.screen.query_one('#help-text').render().plain
			help_lines = text.split('\n')
			assert any(line.startswith('x') and 'set skip and move on' in line for line in help_lines)
			assert not any(line.startswith('s') and 'set skip and move on' in line for line in help_lines)
			await pilot.press('escape')
			await pilot.pause()

			await pilot.press('x')
			assert tracker.calls == [(1, 'skipped', 'tui')]
			assert _titles(app) == ['Second']

			before = list(tracker.calls)
			await pilot.press('s')  # unbound now: nothing happens
			assert tracker.calls == before

	_run(scenario)


# -- details: the pane and its data -------------------------------------


def _attach(db_path, job_id, kind, path):
	"""A file row, the way plan 05's cover-letter step will write one."""
	conn = db.connect(db_path)
	try:
		with conn:
			conn.execute(
				'INSERT INTO attachments (job_id, kind, path, created_at) VALUES (?, ?, ?, ?)',
				(job_id, kind, path, '2026-11-18T10:00:00+00:00'),
			)
	finally:
		conn.close()


def _details_app(db_path, filter_slug='all'):
	return TriageApp(
		store.Tracker(db_path),
		opener=Opener(),
		config=TuiSettings(default_filter=filter_slug),
	)


def test_details_on_at_140_columns_and_off_at_80(job, db_path):
	store.ingest([job()], db_path)

	def shown_at(width):
		app = _details_app(db_path)

		async def scenario():
			async with app.run_test(size=(width, 30)) as pilot:
				await _ready(pilot, app)
				return app._details_on(), app._details_widget.display

		return asyncio.run(scenario())

	assert shown_at(widgets.DETAILS_MIN_WIDTH) == (True, True)
	assert shown_at(139) == (False, False)
	assert shown_at(80) == (False, False)


def test_d_toggles_the_pane_and_it_shows_every_field_of_the_selected_job(job, db_path):
	created = store.ingest(
		[
			job(
				title='Platform Engineer',
				company='Acme',
				published='2026-11-10',
				url='https://example.com/platform',
				locations=('Worldwide', 'EMEA'),
				salary='$120k',
				source='test',
			),
			job(
				title='Designer',
				company='Globex',
				published='2026-11-01',
				url='https://example.com/designer',
			),
		],
		db_path,
	)
	target = created.new[0]
	store.add_note(db_path, target['id'], 'recruiter called')
	store.set_status(db_path, target['id'], 'to_apply', 'cli')
	_attach(db_path, target['id'], 'cover_letter', '/ops/letters/acme.pdf')
	saved = store.get(db_path, target['id'])
	app = _details_app(db_path)

	async def scenario():
		async with app.run_test(size=(160, 40)) as pilot:
			await _ready(pilot, app)
			selected = app._view[app._selection]
			assert selected['id'] == target['id']  # newest first
			text = app._details_widget.render().plain
			for expected in (
				'Acme',
				'Platform Engineer',
				'2026-11-10',
				'first seen',
				saved['first_seen'][:10],
				'last seen',
				'Worldwide, EMEA',
				'$120k',
				'test',
				'https://example.com/platform',
				'recruiter called',
				'new → to apply',
				'cli',
				'cover_letter',
				'/ops/letters/acme.pdf',
			):
				assert expected in text, expected

			await pilot.press('d')
			assert not app._details_widget.display
			await pilot.press('r')  # the toggle survives a reload
			await _ready(pilot, app)
			assert not app._details_widget.display

			await pilot.press('d')
			assert app._details_widget.display
			await pilot.press('tab')  # and a tab switch
			assert app._details_widget.display

	_run(scenario)


def test_the_details_pane_follows_the_cursor(job, db_path):
	store.ingest(
		[
			job(title='First Job', company='A', published='2026-11-09'),
			job(title='Second Job', company='B', published='2026-11-01'),
		],
		db_path,
	)
	app = _details_app(db_path)

	async def scenario():
		async with app.run_test(size=(160, 30)) as pilot:
			await _ready(pilot, app)
			assert 'First Job' in app._details_widget.render().plain
			await pilot.press('j')
			assert 'Second Job' in app._details_widget.render().plain
			assert 'First Job' not in app._details_widget.render().plain

	_run(scenario)


# -- search: live, casefolded, and never a status key -------------------


def _search_app():
	tracker = FakeTracker(
		[
			entry(1, 'new', 'Platform Engineer', 'Acme', '2026-11-10'),
			entry(2, 'new', 'Designer', 'Globex', '2026-11-01'),
			entry(3, 'new', 'Backend Developer', 'ACME Labs', '2026-10-01'),
		]
	)
	return tracker, TriageApp(
		tracker,
		opener=Opener(),
		config=TuiSettings(default_filter='all'),
	)


def test_search_filters_as_you_type_without_triggering_a_status():
	tracker, app = _search_app()

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('/')
			assert app._mode == 'search'
			assert app._prompt is not None and app._prompt.has_focus

			# "acme": the a would mark applied if the app kept its keys.
			await pilot.press('a', 'c', 'm', 'e')

			assert tracker.calls == []
			assert app._search == 'acme'
			assert _titles(app) == ['Platform Engineer', 'Backend Developer']
			assert '/acme' in app._tabs_bar.render().plain

			# j/k and the arrows belong to the input while it is open.
			await pilot.press('j')
			assert app._prompt.value == 'acmej'
			assert _titles(app) == []
			assert tracker.calls == []
			await pilot.press('backspace')

			before = app._selection
			await pilot.press('down')
			await pilot.press('up')
			assert app._selection == before
			assert app._search == 'acme'

			await pilot.press('left')
			assert app._prompt.cursor_position == 3
			await pilot.press('right')
			assert app._prompt.cursor_position == 4

			# esc clears the search
			await pilot.press('escape')
			assert app._mode is None
			assert app._search == ''
			assert not app._prompt.display
			assert _titles(app) == ['Platform Engineer', 'Designer', 'Backend Developer']
			assert '/acme' not in app._tabs_bar.render().plain

	_run(scenario)


def test_enter_keeps_the_search_and_it_survives_tab_switches():
	_tracker, app = _search_app()

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('/')
			await pilot.press(*'globex')
			await pilot.press('enter')

			assert app._mode is None
			assert app._search == 'globex'
			assert not app._prompt.display
			assert _titles(app) == ['Designer']
			assert '/globex' in app._tabs_bar.render().plain

			# The search narrows whatever tab is current; the tab it
			# was typed on is not part of the state.
			await pilot.press('2')
			assert app._filter == 'to_apply'
			assert app._search == 'globex'
			assert _titles(app) == []

			await pilot.press('1')
			assert app._filter == 'new'
			assert _titles(app) == ['Designer']
			assert '/globex' in app._tabs_bar.render().plain

	_run(scenario)


def test_search_matches_a_casefolded_substring_of_title_or_company():
	_tracker, app = _search_app()

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('/')
			await pilot.press(*'ACME')
			assert _titles(app) == ['Platform Engineer', 'Backend Developer']

			# A title match, not just a company one.
			await pilot.press('escape')
			await pilot.press('/')
			await pilot.press(*'design')
			assert _titles(app) == ['Designer']

	_run(scenario)


# -- copy and note ------------------------------------------------------


class ClipboardApp(TriageApp):
	"""TriageApp with copy_to_clipboard spied, so the test sees the URL."""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.copied = []

	def copy_to_clipboard(self, text):
		self.copied.append(text)
		super().copy_to_clipboard(text)


def test_y_copies_the_selected_url_and_flashes():
	tracker = FakeTracker([entry(1, 'to_apply', 'First'), entry(2, 'to_apply', 'Second')])
	app = ClipboardApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('j')
			url = app._view[app._selection]['url']

			await pilot.press('y')

			assert app.copied == [url]
			assert app._flash is not None and 'copied' in app._flash
			assert url in app._flash

	_run(scenario)


def test_m_writes_a_note_to_the_database_and_shows_it(job, db_path):
	store.ingest([job(title='Note me')], db_path)
	target = store.query(db_path)[0]
	store.set_status(db_path, target['id'], 'to_apply', 'cli')
	app = TriageApp(
		store.Tracker(db_path),
		opener=Opener(),
		config=TuiSettings(default_filter='to_apply'),
	)

	async def scenario():
		async with app.run_test(size=(160, 40)) as pilot:
			await _ready(pilot, app)
			before = store.get(db_path, target['id'])

			await pilot.press('m')
			assert app._mode == 'note'
			assert app._prompt is not None and app._prompt.has_focus

			await pilot.press(*'ask')
			await pilot.press('space')
			await pilot.press(*'again')
			await pilot.press('enter')

			saved = store.get(db_path, target['id'])
			assert saved['notes'] == 'ask again'
			# "a", "s" and "t" are status keys: in the note they were text.
			assert saved['status'] == before['status']
			assert app._mode is None
			assert not app._prompt.display
			assert 'ask again' in app._details_widget.render().plain
			assert app._flash is not None and 'note' in app._flash

	_run(scenario)


# -- mouse --------------------------------------------------------------


def test_clicking_a_row_selects_that_job():
	tracker = FakeTracker([entry(index, 'to_apply', f'Job {index}') for index in range(1, 11)])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			assert await pilot.click('#jobs', offset=(4, 2))
			assert app._selection == 2

			assert await pilot.click('#jobs', offset=(4, 4))
			assert app._selection == 4

	_run(scenario)


def test_clicking_a_tab_switches_to_that_tab():
	tracker = FakeTracker([entry(1, 'to_apply', 'Queue')])
	app = TriageApp(tracker, opener=Opener())

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			line = app._tabs_bar.render().plain
			x = line.index('applied')

			assert await pilot.click('#tabs', offset=(x, 0))

			assert app._filter == 'applied'

	_run(scenario)


# -- the new actions are rebindable like every other row ----------------


def test_rebinding_details_to_D_passes_validation_and_works(tmp_path):
	path = tmp_path / 'jobtriage.toml'
	path.write_text('[tui.keys]\ndetails = "D"\n')
	settings = config.load_tui(path)
	assert settings.keys == {'details': 'D'}

	tracker = FakeTracker([entry(1, 'to_apply', 'One')])
	app = TriageApp(tracker, opener=Opener(), config=settings)

	async def scenario():
		async with app.run_test(size=(80, 24)) as pilot:
			await _ready(pilot, app)
			await pilot.press('D')
			assert app._details_user is True
			await pilot.press('d')  # unbound now: nothing happens
			assert app._details_user is True

	_run(scenario)


# -- every binding in the table is exercised ---------------------------


def _expected_filter(name):
	return keys.FILTERS[int(name.split('_')[1]) - 1]


def test_every_binding_in_the_table_does_something():
	"""One fresh app per key: press it and assert the table's promise.

	The Table is the contract, so this iterates it rather than a hand
	written list; a new row without a case here fails loudly instead of
	being silently uncovered.
	"""
	names = set()
	for row in keys.KEY_TABLE:
		for key in row.keys:
			names.add(row.name)
			tracker = FakeTracker([entry(index, 'to_apply', f'Job {index}') for index in range(1, 41)])
			app = TriageApp(tracker, opener=Opener())

			async def scenario(row=row, key=key, tracker=tracker, app=app):
				async with app.run_test(size=(80, 24)) as pilot:
					await _ready(pilot, app)
					await _cover(row, key, app, tracker, pilot)

			_run(scenario)

	assert names == set(keys.NAMES)


async def _cover(row, key, app, tracker, pilot):
	"""Drive one binding and check the effect its table row promises."""
	name = row.name
	before = app._selection

	if name in keys.STATUS_KEYS:
		await pilot.press(key)
		assert tracker.calls == [(1, name, 'tui')], name
	elif name == 'skipped_shift':
		await pilot.press(key)
		assert tracker.calls == [(1, 'skipped', 'tui')]
	elif name == 'applied_space':
		await pilot.press(key, key)
		assert tracker.calls == [(1, 'applied', 'tui')]
	elif name == 'down':
		await pilot.press(key)
		assert app._selection == before + 1
	elif name == 'up':
		app._selection = 2
		await pilot.press(key)
		assert app._selection == 1
	elif name == 'first':
		await pilot.press(key, key)
		assert app._selection == 0
	elif name == 'last':
		await pilot.press(key)
		assert app._selection == len(app._view) - 1
	elif name == 'half_down':
		await pilot.press(key)
		assert app._selection == app._half_page()
	elif name == 'half_up':
		app._selection = 20
		await pilot.press(key)
		assert app._selection == 20 - app._half_page()
	elif name == 'page_down':
		await pilot.press(key)
		assert app._selection == keys.PAGE
	elif name == 'page_up':
		app._selection = 39
		await pilot.press(key)
		assert app._selection == 39 - keys.PAGE
	elif name == 'tab_prev':
		await pilot.press(key)
		assert app._filter == 'new'  # to_apply wraps backwards
	elif name == 'tab_next':
		await pilot.press(key)
		assert app._filter == 'applied'
	elif name.startswith('tab_'):
		await pilot.press(key)
		assert app._filter == _expected_filter(name)
	elif name == 'undo':
		await pilot.press(key)
		assert app._flash == 'nothing to undo'
	elif name == 'open':
		await pilot.press(key)
		await _settled(pilot, app)
		assert app._opener.calls
	elif name == 'details':
		await pilot.press(key)
		assert app._details_user is not None
		assert app._details_on() == app._details_user
		assert app._details_widget is not None and app._details_widget.display == app._details_on()
	elif name == 'search':
		await pilot.press(key)
		assert app._mode == 'search'
		assert app._prompt is not None and app._prompt.has_focus
	elif name == 'copy_url':
		await pilot.press(key)
		assert app._clipboard == app._view[app._selection]['url']
		assert 'copied' in (app._flash or '')
	elif name == 'note':
		await pilot.press(key)
		assert app._mode == 'note'
		assert app._prompt is not None and app._prompt.has_focus
	elif name == 'reload':
		loads = tracker.loads
		await pilot.press(key)
		await _ready(pilot, app)
		assert tracker.loads > loads
	elif name == 'scrape':
		await pilot.press(key)
		assert app._flash == 'no scrape configured'  # this app has no pipeline
	elif name == 'help':
		await pilot.press(key)
		await pilot.pause()
		assert isinstance(app.screen, HelpScreen)
	elif name == 'quit':
		await pilot.press(key)
		await pilot.pause()
		assert app._exit
	else:  # a row with no case here fails the test, on purpose
		raise AssertionError(f'no coverage for {name}')
