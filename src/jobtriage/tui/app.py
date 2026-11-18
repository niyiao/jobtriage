"""The triage TUI: the database's jobs, triaged from the keyboard
while Chrome holds the postings. Formerly apply.py (and ApplyApp),
renamed: the TUI triages — the applying happens in Chrome.

`jobtriage` or `jobtriage tui` (jobtriage/cli/tui.py builds this app)
— every job in the database under one tab at a time, pages of thirty.
Tabs follow the workflow: new → to apply → applied → skipped → all,
opening on the configured `tui.default_filter`. `j`/`k` (or the
arrows) walk the list, `gg`/`G` jump to the ends, `ctrl+d`/`ctrl+u`
move half a visible screen and pgup/pgdn a page. `←`/`→`/`h`/`l`
change tabs; they never change a status.

A status key (`n`, `t`, `a`, `s`) writes that status the moment it is
pressed and moves on to the next job — triage is a stream of
decisions, and `u` undoes the last one, restoring both the status and
the cursor, repeatable for the session. Writes are synchronous:
store.Tracker takes a SQLite write, which is a millisecond, so there is
no staging, writer thread, queue or flush on exit, and a failed write
is a footer line rather than a retry. Space×2 and `S` survive as hidden
aliases for applied and skipped.

The key map, the footer and the `?` overlay all come from one table in
tui/keys.py, resolved against `[tui.keys]`; a rebind changes every one
of them. Enter opens the job in the session's Chrome (the workspace's
.browser-profile one, docs/scrapers.md's launch): each job remembers
its tab, a second enter focuses that tab, and a new one opens only when
the old has been closed.

`d` opens a details pane beside the list — company, title, dates,
locations, pay, source, URL, notes, status history and attachments —
on by itself from 140 columns, and remembered once toggled. `/` and
`m` dock a one-line input at the bottom: search title and company as
you type, or write a note. Textual checks the app's priority bindings
before a focused widget sees a key, so while that input is open the app
suspends its bindings (see _open_prompt); otherwise typing "app" would
mark the job applied. `y` copies the URL, OSC 52, and says so.

`R` runs a scrape in a background thread: the callable is injected the
way the tracker and the opener are (cli/tui.py passes the pipeline's,
the tests a fake), the footer counts boards as they land, and the view
reloads when the scrape does so new jobs appear without losing the
cursor. A second R while one is running is refused — two scrapes would
race each other's ingest.

When the app is given a state path (`ws.tui_state`) it remembers the
tab, the search and the selected job on quit, and restores them at the
next start. Only steady-state values are saved: never the open prompt,
a pending g, or a scrape's progress. A missing, unreadable or stale
file is simply the defaults — remembering your place must never be a
reason the TUI refuses to start.

Enter opens the job in the session's Chrome (the workspace's
.browser-profile one, docs/scrapers.md's launch): each job remembers
its tab, a second enter focuses that tab, and a new one opens only when
the old has been closed. With no Chromium on the machine the opener is
chrome.DefaultBrowser instead, and enter says tab focusing isn't
available rather than failing to launch something that isn't there.

The screen is composed (tui/widgets.py): tabs, a Horizontal holding the
job list and the details pane, the URL line and the footer, with the
prompt below. Slugs are the database's (`to_apply`), labels are the
TUI's (`to apply`) and both — plus the colours — come from
jobtriage.status, so the two can never drift. The TUI leaves Chrome
running — the application in it is the point.
"""

import json
import time
from pathlib import Path

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import BindingsMap
from textual.containers import Horizontal
from textual.widgets import Input

from jobtriage import store
from jobtriage.tui import help, keys, widgets

# The two size constants the actions need; the status vocabulary is
# read from keys.py where it is defined, so there is only one copy.
PAGE = keys.PAGE
DOUBLE_PRESS = keys.DOUBLE_PRESS
G_CHORD = keys.G_CHORD


class TriageApp(App):
	"""The keyboard over the database, one tab at a time."""

	CSS = """
	#tabs { height: 2; }
	#body { height: 1fr; }
	#jobs { height: 1fr; }
	/* widgets.DETAILS_WIDTH; the literal keeps the CSS a plain string */
	#details { width: 44; height: 1fr; padding: 0 1; border-left: solid $panel-darken-1; }
	#status { height: 1; }
	#footer { height: 1; }
	#prompt { height: 1; display: none; }
	"""

	def __init__(self, tracker: store.Tracker, opener, config=None, scrape=None, state_path=None):
		"""`config` is jobtriage.config.TuiSettings or None for defaults.

		The None path is the tests', and the one `jobtriage tui` takes
		before any config loader exists; either way the resolved table
		lives on the instance, so two apps in one process can listen to
		different keys.

		`scrape` is the callable `R` runs in a worker: `scrape(on_board)`
		returns how many jobs were new. None (the tests' default) makes R
		say so instead of failing. `state_path` is `ws.tui_state`; None
		means remember nothing, which is how every test that does not
		care about the state file builds the app.
		"""
		super().__init__()
		self._tracker = tracker
		self._opener = opener  # anything with open_or_focus(url, remembered)
		self._config = config
		self._scrape = scrape
		self._state_path = Path(state_path) if state_path is not None else None

		# The key table after [tui.keys]. Bindings are regenerated per
		# instance because BINDINGS is a class attribute: Textual merges
		# it at class creation, and a rebind must not leak into another
		# TriageApp.
		self._keys = keys.resolve(config.keys if config is not None else None)
		self._bindings = BindingsMap(keys.bindings(self._keys))

		# Where the last session left off, read once and applied to the
		# first load. A value the file cannot honestly supply ('filter':
		# a tab this build no longer has) falls back here, not later.
		remembered = _read_state(self._state_path)
		default = config.default_filter if config is not None else keys.DEFAULT_FILTER
		self._filter = remembered.get('filter', default)
		self._search = remembered.get('search', '')
		self._restored_job = remembered.get('job_id')
		self._first_load = True
		self._loaded_once = False

		self._tabs_bar: widgets.TabBar | None = None
		self._job_list: widgets.JobList | None = None
		self._details_widget: widgets.Details | None = None
		self._status_line: widgets.StatusLine | None = None
		self._footer: widgets.Footer | None = None
		self._prompt: widgets.Prompt | None = None
		self._state = 'loading'
		self._error: str | None = None
		self._write_error: str | None = None  # the latest failed status write
		self._jobs: list[dict] = []
		self._view: list[dict] = []
		self._selection = 0
		self._flash: str | None = None
		self._last_space = float('-inf')
		self._pending_g: float | None = None
		self._undo: list[tuple[int, str, int, str]] = []
		self._tabs: dict[str, str] = {}  # job url → DevTools tab id
		self._opening: set[str] = set()  # urls with an enter still in flight

		# The background scrape's session state: is one running, and how
		# many of the boards have landed ((done, total), None before the
		# first one answers). The footer renders the pair; the worker is
		# the only writer, through call_from_thread.
		self._scraping = False
		self._scrape_progress: tuple[int, int] | None = None

		# The pane's session state: None until `d` is pressed, then the
		# user's choice (so the width default only applies before the
		# first toggle). `_mode` is 'search', 'note' or None while the
		# prompt is open; `_search` is the kept query after enter.
		self._details_user: bool | None = None
		self._details_cache: dict[int, tuple[list[dict], list[dict]]] = {}
		self._mode: str | None = None

	# -- lifecycle ---------------------------------------------------

	def compose(self) -> ComposeResult:
		"""Tabs on top, then the body area (a Horizontal, so the details
		pane can sit beside the list), then the status line, footer and
		the prompt docked at the bottom."""
		self._tabs_bar = widgets.TabBar(id='tabs')
		self._job_list = widgets.JobList(id='jobs')
		self._details_widget = widgets.Details(id='details')
		self._status_line = widgets.StatusLine(id='status')
		self._footer = widgets.Footer(id='footer')
		self._prompt = widgets.Prompt(id='prompt')
		# A hidden Input is still focusable, and Textual focuses the
		# first focusable widget on mount — a Prompt that could focus
		# while hidden would swallow every printable triage key. It
		# becomes focusable only while _open_prompt shows it.
		self._prompt.can_focus = False
		yield self._tabs_bar
		with Horizontal(id='body'):
			yield self._job_list
			yield self._details_widget
		yield self._status_line
		yield self._footer
		yield self._prompt

	def on_mount(self) -> None:
		self._sync_details()  # the 140-column default, before the first paint
		self.run_worker(self._startup, thread=True)

	def on_resize(self, event: events.Resize) -> None:
		"""Follow the terminal width until the pane is toggled by hand.

		The event carries the new width; self.size can still be the old
		one while this handler runs.
		"""
		self._sync_details(event.size.width)

	def on_unmount(self) -> None:
		"""Remember the place even when the exit did not go through `q`
		(the terminal closing, the app returned from run_test): the same
		values are written, so a second write is harmless."""
		self._save_state()

	def _startup(self) -> None:
		try:
			jobs = self._tracker.load()
		except Exception as error:  # noqa: BLE001 — the r-retryable notice screen
			self.call_from_thread(self._load_failed, str(error))
			return
		self.call_from_thread(self._loaded, jobs)

	def _loaded(self, jobs: list[dict]) -> None:
		keep = self._selected_url()
		self._jobs = self._sort(jobs)
		self._state = 'ready'
		self._error = None
		self._write_error = None  # the reload shows the database's truth
		self._details_cache.clear()  # history and notes may have moved too
		self._rebuild(keep=keep)
		if self._first_load and self._restored_job is not None:
			# The remembered place is a job id, and _rebuild has no view
			# to keep on the first load (the cursor was at 0). If the job
			# is gone — deleted, or moved out of the remembered tab — the
			# cursor simply stays where a fresh start would put it.
			found = next(
				(index for index, job in enumerate(self._view) if job['id'] == self._restored_job),
				None,
			)
			if found is not None:
				self._selection = found
		self._first_load = False
		self._restored_job = None
		self._loaded_once = True
		self._repaint()

	def _load_failed(self, error: str) -> None:
		self._state = 'error'
		self._error = error
		self._repaint()

	def _reload(self) -> None:
		self._startup()  # the database is already as written: no flush to wait on

	# -- the list ----------------------------------------------------

	@staticmethod
	def _sort(jobs: list[dict]) -> list[dict]:
		"""Newest first: published is ISO text, so it sorts as text;
		'undated' would sort above every date, so it gets its own
		bucket at the bottom."""
		return sorted(
			jobs,
			key=lambda job: (job['published'] != 'undated', job['published']),
			reverse=True,
		)

	def _rebuild(self, keep: str | None = None) -> None:
		"""Re-apply the tab and the search, keeping the cursor on the
		same job when it survives, on the nearest index otherwise.

		The search is a casefolded substring of the title or the
		company — the two fields the list shows — and it narrows the
		current tab rather than replacing it, so switching tabs keeps
		the query in force.
		"""
		old = self._selection
		needle = self._search.casefold()
		self._view = [
			job
			for job in self._jobs
			if (self._filter == 'all' or job['status'] == self._filter)
			and (not needle or needle in job['title'].casefold() or needle in job['company'].casefold())
		]
		target = None
		if keep:
			target = next((i for i, job in enumerate(self._view) if job['url'] == keep), None)
		if target is None:
			target = min(old, len(self._view) - 1) if self._view else 0
		self._selection = target

	def _selected_url(self) -> str | None:
		if not self._view:
			return None
		return self._view[self._selection]['url']

	def _select(self, job: dict) -> None:
		"""Point the cursor at this job, wherever the rebuilt view put
		it."""
		index = next((i for i, other in enumerate(self._view) if other is job), None)
		if index is None:
			index = min(self._selection, len(self._view) - 1) if self._view else 0
		self._selection = index

	# -- writes ------------------------------------------------------

	def _write(self, job: dict, slug: str, actor: str = 'tui') -> bool:
		"""Land one status change now, and say whether it landed.

		The write is synchronous — one SQLite statement, so waiting for
		it is cheaper than any queue. On failure the job keeps the
		status the screen showed and the footer explains: the TUI never
		shows a decision the database did not take. `actor` goes into
		status_history; undo says 'tui-undo' so the record shows who
		changed it back.
		"""
		try:
			self._tracker.set_status(job['id'], slug, actor)
		except Exception as error:  # noqa: BLE001 — a footer line, not a crash
			self._write_error = f'write failed ({error}) — {job["title"][:30]} not saved'
			return False
		self._write_error = None
		job['status'] = slug
		self._details_cache.pop(job['id'], None)  # its history gained a row
		return True

	# -- moving ------------------------------------------------------

	def _move(self, delta: int) -> None:
		if not self._view:
			return
		index = self._selection
		self._selection = min(max(index + delta, 0), len(self._view) - 1)

	def action_down(self) -> None:
		self._move(1)
		self._repaint()

	def action_up(self) -> None:
		self._move(-1)
		self._repaint()

	def action_last(self) -> None:
		"""G: the bottom of the view, from wherever the cursor was."""
		if self._view:
			self._selection = len(self._view) - 1
		self._repaint()

	def action_g(self) -> None:
		"""`gg`: the second g within G_CHORD goes to the first job.

		A lone g only arms the chord; every other key clears the
		pending timestamp (see _on_key), so the footer's `g…` and the
		chord always agree about whether a g is waiting.
		"""
		now = time.monotonic()
		if self._pending_g is not None and now - self._pending_g <= G_CHORD:
			self._pending_g = None
			if self._view:
				self._selection = 0
			self._repaint()
			return
		self._pending_g = now
		self._repaint()

	def _body_rows(self) -> int:
		"""How many job rows the layout is currently showing.

		The JobList is the body (the four rows outside it are the tab
		bar, rule, status line and footer); before compose — the
		headless tests — fall back to what frame() would give it.
		"""
		if self._job_list is not None and self._job_list.size.height:
			return self._job_list.size.height
		return max(1, self.size.height - 4)

	def _half_page(self) -> int:
		"""Half a screen, in rows, derived from the visible body."""
		return max(1, self._body_rows() // 2)

	def action_half_page_down(self) -> None:
		self._move(self._half_page())
		self._repaint()

	def action_half_page_up(self) -> None:
		self._move(-self._half_page())
		self._repaint()

	def action_page_down(self) -> None:
		self._move(PAGE)
		self._repaint()

	def action_page_up(self) -> None:
		self._move(-PAGE)
		self._repaint()

	# -- tabs --------------------------------------------------------

	def _cycle_filter(self, step: int) -> None:
		if self._state != 'ready':
			return
		index = keys.FILTERS.index(self._filter)
		self._filter = keys.FILTERS[(index + step) % len(keys.FILTERS)]
		self._rebuild(keep=self._selected_url())
		self._repaint()

	def action_filter_next(self) -> None:
		self._cycle_filter(1)

	def action_filter_prev(self) -> None:
		self._cycle_filter(-1)

	def action_tab(self, index: int) -> None:
		"""`1`–`5`: jump straight to the tab at that position."""
		if self._state != 'ready' or not 0 <= index < len(keys.FILTERS):
			return
		self._filter = keys.FILTERS[index]
		self._rebuild(keep=self._selected_url())
		self._repaint()

	# -- status ------------------------------------------------------

	def action_status(self, slug: str) -> None:
		"""A status letter: save it and move on."""
		self._mark_and_next(slug)

	def action_space(self) -> None:
		"""Space twice within DOUBLE_PRESS is the old applied alias."""
		now = time.monotonic()
		double = now - self._last_space <= DOUBLE_PRESS
		self._last_space = now
		if not double or not self._view:
			return
		self._last_space = float('-inf')  # a third space starts a fresh pair
		self._mark_and_next('applied')

	def _mark_and_next(self, slug: str) -> None:
		"""Mark the job under the cursor with `slug`, write it at once,
		and move straight on to the next one.

		The decision is recorded on the undo stack before the view is
		rebuilt: the job may vanish from this tab, and undo needs to
		know its old status, where it sat and which tab it was on. A
		failed write does not move on and is not undoable: the decision
		did not land, so the cursor stays on the job and the footer says
		why. If it was the last job, the cursor stays on whatever is
		left.
		"""
		if not self._view:
			return
		job = self._view[self._selection]
		index = self._selection
		old_status = job['status']
		following = self._view[index + 1] if index + 1 < len(self._view) else None
		if not self._write(job, slug):
			self._repaint()
			return
		if old_status != slug:  # a no-op write is not something to undo
			self._undo.append((job['id'], old_status, index, self._filter))
		self._rebuild(keep=job['url'])
		if following is not None:
			self._select(following)
		else:  # it was the last job: stay put on whatever is left
			self._selection = min(index, len(self._view) - 1) if self._view else 0
		self._flash = f'{keys.LABELS[slug]} · {job["title"][:60]}'
		self._repaint()

	def action_undo(self) -> None:
		"""Put the last status change back, status and cursor both.

		The stack is session-scoped and repeatable. The job may have
		disappeared (deleted in another window) or no longer fit the
		tab it was changed on (something else moved it): then the status
		is still restored if the row exists, the tab is still restored,
		and the cursor lands at the recorded index, clamped. A failed
		write leaves the entry on the stack so the user can try again.
		"""
		if not self._undo:
			self._flash = 'nothing to undo'
			self._repaint()
			return
		job_id, old_status, index, filter_slug = self._undo[-1]
		job = next((row for row in self._jobs if row['id'] == job_id), None)
		if job is not None:
			if not self._write(job, old_status, actor='tui-undo'):
				self._repaint()
				return
		else:
			try:
				self._tracker.set_status(job_id, old_status, 'tui-undo')
			except Exception as error:  # noqa: BLE001 — a footer line, not a crash
				self._write_error = f'undo failed ({error}) — job {job_id} not saved'
				self._repaint()
				return
			self._details_cache.pop(job_id, None)
		self._undo.pop()
		self._filter = filter_slug
		self._rebuild()
		found = next((i for i, row in enumerate(self._view) if row['id'] == job_id), None)
		if found is not None:
			self._selection = found
		else:
			self._selection = min(index, len(self._view) - 1) if self._view else 0
		self._flash = f'undo · {job["title"][:60]}' if job is not None else 'undo'
		self._repaint()

	# -- other keys --------------------------------------------------

	def action_help(self) -> None:
		"""`?`: the overlay generated from the table this app uses."""
		self.push_screen(help.HelpScreen(self._keys))

	def action_details(self) -> None:
		"""`d`: show or hide the pane; the choice outlives reloads."""
		self._details_user = not self._details_on()
		self._repaint()

	def action_copy_url(self) -> None:
		"""`y`: the selected URL on the clipboard, via OSC 52."""
		url = self._selected_url()
		if url is None:
			return
		self.copy_to_clipboard(url)
		self._flash = f'copied {url[:60]}'
		self._repaint()

	def action_search(self) -> None:
		"""`/`: edit the search, starting from the query still in force."""
		if self._state != 'ready':
			return
		self._open_prompt('search', 'search title or company', self._search)

	def action_note(self) -> None:
		"""`m`: one line, appended to the selected job's notes."""
		if self._state != 'ready' or not self._view:
			return
		title = self._view[self._selection]['title'][:40]
		self._open_prompt('note', f'note for {title}', '')

	# -- the prompt --------------------------------------------------

	def _open_prompt(self, mode: str, placeholder: str, initial: str) -> None:
		"""Show the bottom input and hand it the keyboard.

		Every app binding is priority, and Textual checks those before
		a focused widget sees the key. Rather than teach each action to
		ignore itself, the app suspends its whole binding map while the
		prompt is up: the Input's own bindings get enter, the arrows
		and editing keys, and printable characters edit the box instead
		of marking a job. Help's modal screen makes the same point by
		claiming every key; this is the non-modal version.

		The table itself is untouched, so closing the prompt rebuilds
		the same bindings, rebinds included.
		"""
		if self._prompt is None:
			return
		self._mode = mode
		self._bindings = BindingsMap([])
		self._prompt.placeholder = placeholder
		self._prompt.value = initial  # Changed fires for search; note ignores it
		self._prompt.display = True
		self._prompt.can_focus = True
		self._prompt.focus()
		self._repaint()

	def _close_prompt(self) -> None:
		"""Hide the input and give the app its keys back."""
		self._mode = None
		if self._prompt is not None:
			self._prompt.display = False
			self._prompt.value = ''
			self.set_focus(None)
			self._prompt.can_focus = False  # never Textual's default focus
		self._bindings = BindingsMap(keys.bindings(self._keys))

	def _cancel_prompt(self) -> None:
		"""Escape: a search is cleared as well as closed; a note is dropped."""
		if self._mode == 'search':
			self._search = ''
			self._rebuild()
		self._close_prompt()
		self._repaint()

	def on_input_changed(self, event: Input.Changed) -> None:
		"""Search as you type: every keystroke narrows the current tab."""
		if self._mode != 'search':
			return
		self._search = event.value
		self._rebuild(keep=self._selected_url())
		self._repaint()

	def on_input_submitted(self, event: Input.Submitted) -> None:
		"""Enter closes the prompt: it keeps the search, and lands a note."""
		if self._mode == 'search':
			self._search = event.value
			self._close_prompt()
			self._rebuild(keep=self._selected_url())
			self._flash = f'search · {self._search}' if self._search else None
			self._repaint()
		elif self._mode == 'note':
			self._submit_note(event.value)

	def _submit_note(self, text: str) -> None:
		"""Write one note through the tracker, then show it in the pane."""
		text = text.strip()
		if not text or not self._view:
			self._close_prompt()
			self._repaint()
			return
		job = self._view[self._selection]
		try:
			notes = self._tracker.add_note(job['id'], text)
		except Exception as error:  # noqa: BLE001 — a footer line, not a crash
			self._write_error = f'note failed ({error}) — {job["title"][:30]} not saved'
			self._close_prompt()
			self._repaint()
			return
		job['notes'] = notes
		self._details_cache.pop(job['id'], None)
		self._close_prompt()
		self._flash = f'note · {job["title"][:60]}'
		self._repaint()

	def _detail_sections(self, job: dict) -> tuple[list[dict], list[dict]]:
		"""The job's history and attachments, read once per job id.

		The list already carries every jobs column (Tracker.load is
		SELECT *); the two child tables are the only reads the pane
		adds, so caching them means moving the cursor costs nothing
		after the first look. Writes clear the entry.
		"""
		cached = self._details_cache.get(job['id'])
		if cached is None:
			cached = (
				self._tracker.history(job['id']),
				self._tracker.attachments(job['id']),
			)
			self._details_cache[job['id']] = cached
		return cached

	def action_open(self) -> None:
		if self._state != 'ready' or not self._view:
			return
		url = self._selected_url()
		if url in self._opening:
			return
		self._opening.add(url)
		self._flash = f'opening {url[:70]}…'
		self._repaint()
		self.run_worker(lambda: self._open(url), thread=True)

	def _open(self, url: str) -> None:
		tab = None
		error = None
		try:
			tab = self._opener.open_or_focus(url, self._tabs.get(url))
		except Exception as caught:  # noqa: BLE001 — a chrome failure is a footer line
			error = f'chrome: {caught}'

		def done() -> None:
			self._opening.discard(url)
			if error is not None:
				self._flash = error
			else:
				if tab:
					self._tabs[url] = tab
				if not getattr(self._opener, 'focuses_tabs', True):
					# DefaultBrowser: the page opened, but the next enter
					# cannot focus the tab it made, so say why.
					self._flash = 'opened in your default browser — tab focusing unavailable'
				elif self._flash == f'opening {url[:70]}…':
					self._flash = None
			self._repaint()

		try:
			self.call_from_thread(done)
		except Exception:  # noqa: BLE001, S110 — the app exited mid-open; nothing to tell
			pass

	def action_reload(self) -> None:
		if self._state == 'loading':
			return
		self._state = 'loading'
		self._repaint()
		self.run_worker(self._reload, thread=True)

	def action_scrape(self) -> None:
		"""`R`: the pipeline in a background thread, the footer counting
		boards as they land.

		A second R is refused while the first runs: two scrapes would
		ingest the same jobs concurrently, and the second's "N new" would
		be a lie about a race. A missing callable (the tests' plain app)
		is a footer line, not a traceback.
		"""
		if self._scraping:
			self._flash = 'already scraping'
			self._repaint()
			return
		if self._scrape is None:
			self._flash = 'no scrape configured'
			self._repaint()
			return
		self._scraping = True
		self._scrape_progress = None
		self._flash = None
		self._repaint()
		self.run_worker(self._run_scrape, thread=True)

	def _run_scrape(self) -> None:
		"""The worker half: run the injected scrape, report as it goes.

		Progress crosses threads through call_from_thread, the pattern
		_open already uses; a report after the app has exited is dropped
		rather than raised, because there is no footer left to tell.
		"""

		def on_board(name: str, done: int, total: int) -> None:
			try:
				self.call_from_thread(self._scrape_progressed, done, total)
			except Exception:  # noqa: BLE001, S110 — the app exited mid-scrape
				pass

		try:
			new = self._scrape(on_board)
		except Exception as error:  # noqa: BLE001 — a footer line, not a crash
			try:
				self.call_from_thread(self._scrape_failed, str(error))
			except Exception:  # noqa: BLE001, S110 — the app exited mid-scrape
				pass
			return
		try:
			self.call_from_thread(self._scrape_done, new)
		except Exception:  # noqa: BLE001, S110 — the app exited mid-scrape
			pass

	def _scrape_progressed(self, done: int, total: int) -> None:
		self._scrape_progress = (done, total)
		self._repaint()

	def _scrape_done(self, new: int) -> None:
		"""Say what landed, then reload so the new jobs are the view.

		The reload is the `r` path, so it keeps the cursor on the same
		job and re-applies the tab and the search — a scrape must not
		throw away where the user was working.
		"""
		self._scraping = False
		self._scrape_progress = None
		self._flash = f'{new} new'
		self.action_reload()

	def _scrape_failed(self, error: str) -> None:
		self._scraping = False
		self._scrape_progress = None
		self._flash = f'scrape failed ({error})'
		self._repaint()

	# -- the remembered place ----------------------------------------

	def _remember(self) -> dict:
		"""The steady state worth keeping across restarts.

		Only what a fresh app can apply: the tab, the query in force and
		the selected job's id. The open prompt, a pending g, the flash
		and a running scrape are facts about a moment, not about how the
		session was set up, so they are never written.
		"""
		job = self._view[self._selection] if self._view else None
		return {
			'filter': self._filter,
			'search': self._search,
			'job_id': job['id'] if job is not None else None,
		}

	def _save_state(self) -> None:
		"""Write the remembered place, if this app was given somewhere
		to write it.

		A failure (read-only folder, full disk) is swallowed on purpose:
		remembering your place is a convenience, and a quit that raises
		because of it would be worse than one that forgets.

		Nothing is written before the first load: there would be no view
		to take a selection from, and writing the defaults over a real
		bookmark (a quit during startup) would forget the place it was
		never asked to change.
		"""
		if self._state_path is None or not self._loaded_once:
			return
		try:
			self._state_path.parent.mkdir(parents=True, exist_ok=True)
			self._state_path.write_text(json.dumps(self._remember(), indent='\t') + '\n')
		except OSError:  # a convenience, never a reason to fail a quit
			pass

	def action_quit(self) -> None:
		"""Go: every decision was written when it was made, so there is
		nothing to flush on the way out — only where we were, which is
		for the next start."""
		self._save_state()
		self.exit()

	# -- key bookkeeping ---------------------------------------------

	async def on_event(self, event: events.Event) -> None:
		"""Any key that is not the chord's `g` cancels a pending g.

		Textual checks the app's priority bindings here, before a key
		ever reaches an action method, so this is the one place every
		key passes through. `action_g` owns the g ↔ gg decision;
		everything else (including the tab and status keys) clears the
		chord, which is what the footer's `g…` promises.
		"""
		if isinstance(event, events.Key) and event.key != 'g' and self._pending_g is not None:
			self._pending_g = None
			self._repaint()
		await super().on_event(event)

	def _g_pending(self) -> bool:
		"""True while a g is waiting for its partner (and not stale)."""
		return self._pending_g is not None and time.monotonic() - self._pending_g <= G_CHORD

	# -- rendering ---------------------------------------------------

	def _details_on(self, width: int | None = None) -> bool:
		"""Is the pane showing? A hand toggle beats the width default.

		`width` lets frame() answer for a size the app is not at (the
		headless tests); live callers pass nothing and read self.size.
		"""
		if self._details_user is not None:
			return self._details_user
		return (self.size.width if width is None else width) >= widgets.DETAILS_MIN_WIDTH

	def _sync_details(self, width: int | None = None) -> None:
		"""Make the live widget's visibility agree with _details_on."""
		if self._details_widget is None:
			return
		shown = self._details_on(width)
		if self._details_widget.display != shown:
			self._details_widget.display = shown

	def _repaint(self) -> None:
		"""Refresh the widgets; before compose() there are none, so the
		headless tests can drive the app with no screen."""
		self._sync_details()
		for widget in (
			self._tabs_bar,
			self._job_list,
			self._details_widget,
			self._status_line,
			self._footer,
		):
			if widget is not None:
				widget.refresh()

	def _count(self, name: str) -> int:
		if name == 'all':
			return len(self._jobs)
		return sum(1 for job in self._jobs if job['status'] == name)

	def frame(self, width: int, height: int) -> Text:
		"""The whole screen as one text, one line per terminal row.

		The widgets render the live screen; this composes the same
		functions without a screen, for the headless tests. Keeping it
		means a test can pin the screen's text without Pilot.
		"""
		if widgets.too_small(width, height):
			return widgets.too_small_text()
		lines = [widgets.filters_line(self, width), widgets.rule_line(width)]
		if self._details_on(width):
			lines += widgets.body_with_details(self, height - 4, width)
		else:
			lines += widgets.body_lines(self, height - 4, width)
		lines += [widgets.status_line(self, width), widgets.foot_line(self, width)]
		return Text('\n').join(lines)


def _read_state(path: Path | None) -> dict:
	"""The remembered place, sanitised; {} when there is nothing usable.

	A missing file, a corrupt one, a value of the wrong type and a tab
	this build no longer has are all the same thing to the app: start
	where a fresh install would. It is validated here rather than
	trusted because a hand-edited file must never crash the TUI on
	startup — remembering your place is a convenience, not a contract.
	"""
	if path is None:
		return {}
	try:
		loaded = json.loads(path.read_text(encoding='utf-8'))
	except (OSError, ValueError):
		return {}
	if not isinstance(loaded, dict):
		return {}
	remembered = {}
	filter_slug = loaded.get('filter')
	if isinstance(filter_slug, str) and filter_slug in keys.FILTERS:
		remembered['filter'] = filter_slug
	search = loaded.get('search')
	if isinstance(search, str):
		remembered['search'] = search
	job_id = loaded.get('job_id')
	if isinstance(job_id, int) and not isinstance(job_id, bool):
		remembered['job_id'] = job_id
	return remembered
