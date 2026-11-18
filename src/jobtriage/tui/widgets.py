"""The screen, built from four widgets instead of one Rich Text.

TabBar keeps today's filter line and the rule under it; JobList a page
of jobs (or the loading/error notice that stands in for them);
StatusLine the selected job's URL and pager; Footer the help line or the
latest flash. Their rendering lives in module functions that take the
app and a width, so the live widgets and TriageApp.frame (the headless
whole-screen view tests use) share one implementation and cannot drift.

Why widgets at all: the list sits in a Horizontal by itself, and plan
06 step 8 / plan 07 put a second pane beside it. One Rich Text could
never grow a second column.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.widget import Widget
from textual.widgets import Input

from jobtriage.tui import keys

if TYPE_CHECKING:
	from jobtriage.tui.app import TriageApp


TOO_SMALL = 'terminal too small for the apply list'

# The details pane: how wide it is, and the terminal width at which it
# opens by itself. The threshold is the point where the list still gets
# a comfortable eighty-odd columns beside the pane; below it the pane
# would crowd the part of the screen the triage actually happens in.
DETAILS_WIDTH = 44
DETAILS_MIN_WIDTH = 140


def too_small(width: int, height: int) -> bool:
	"""Is the terminal too small to show a tab bar, a row and a footer?"""
	return height < 5 or width < 20


def too_small_text() -> Text:
	"""The whole screen's stand-in message when it cannot be drawn."""
	return Text(TOO_SMALL, style='red')


class TabBar(Widget):
	"""The filter tabs and the rule under them: two rows."""

	def render(self) -> Text:
		app: TriageApp = self.app  # type: ignore[assignment]
		if too_small(*app.size):
			return too_small_text()  # the one line the too-small screen shows
		return Text('\n').join([filters_line(app, self.size.width), rule_line(self.size.width)])

	def on_click(self, event: events.Click) -> None:
		"""A click on the tab row switches to the tab under the pointer."""
		app: TriageApp = self.app  # type: ignore[assignment]
		if event.y != 0:  # the rule is not a tab
			return
		index = tab_at(app, event.x)
		if index is None:
			return
		event.stop()
		app.action_tab(index)


class JobList(Widget):
	"""A page of jobs, or the notice that stands in for them."""

	def render(self) -> Text:
		app: TriageApp = self.app  # type: ignore[assignment]
		if too_small(*app.size):
			return Text()
		return Text('\n').join(body_lines(app, self.size.height, self.size.width))

	def on_click(self, event: events.Click) -> None:
		"""Clicking a row puts the cursor on that job.

		The widget's own page window decides which job a row means, so
		the same helper the renderer uses maps the click.
		"""
		app: TriageApp = self.app  # type: ignore[assignment]
		if too_small(*app.size) or app._state != 'ready':
			return
		rows = visible_jobs(app, self.size.height)
		if not 0 <= event.y < len(rows):
			return
		event.stop()
		app._select(rows[event.y])
		app._repaint()


class Details(Widget):
	"""The selected job in full, beside the list: the pane `d` toggles."""

	def render(self) -> Text:
		app: TriageApp = self.app  # type: ignore[assignment]
		if too_small(*app.size):
			return Text()
		return Text('\n').join(details_lines(app, self.size.height, self.size.width))


class StatusLine(Widget):
	"""The selected job's URL, with the cursor's place on the right."""

	def render(self) -> Text:
		app: TriageApp = self.app  # type: ignore[assignment]
		if too_small(*app.size):
			return Text()
		return status_line(app, self.size.width)


class Footer(Widget):
	"""The help line, or the latest flash, or a failed write."""

	def render(self) -> Text:
		app: TriageApp = self.app  # type: ignore[assignment]
		if too_small(*app.size):
			return Text()
		return foot_line(app, self.size.width)


class Prompt(Input):
	"""The one-line box `/` and `m` dock at the bottom.

	The app suspends its own bindings while this has focus (see
	TriageApp._open_prompt), so printable keys edit the box instead of
	reaching a status key. Escape is the box's own concern: Input has
	no binding for it, and with the app suspended nothing else would
	close the prompt.
	"""

	def __init__(self, **kwargs) -> None:
		super().__init__(compact=True, **kwargs)

	async def _on_key(self, event: events.Key) -> None:
		if event.key == 'escape':
			event.stop()
			event.prevent_default()
			self.app._cancel_prompt()
			return
		if event.key in ('tab', 'shift+tab'):
			# The screen would move focus away from the box and leave
			# the app's bindings suspended with nothing focused.
			event.stop()
			event.prevent_default()
			return
		await super()._on_key(event)


# -- rendering -----------------------------------------------------------


def tab_labels(app: TriageApp) -> list[tuple[str, str]]:
	"""Each tab as (name, `label count`) — the text and the hit regions
	the tab bar renders and clicks against."""
	return [(name, f'{keys.LABELS.get(name, name)} {app._count(name)}') for name in keys.FILTERS]


def filters_line(app: TriageApp, width: int) -> Text:
	"""Every filter with its count, the current one reversed; the active
	search, if any, follows the tabs as `/query`."""
	line = Text()
	for index, (name, label) in enumerate(tab_labels(app)):
		if index:
			line.append('  ')
		if name == app._filter:
			line.append(label, style='bold reverse')
		else:
			line.append(label, style='dim')
	if app._search:
		line.append(f'  /{app._search}', style='bold')
	return line


def tab_at(app: TriageApp, x: int) -> int | None:
	"""Which tab a click at column `x` hits, mirroring tab_labels' layout.

	The two-space gap between tabs belongs to no tab: a click there
	does nothing rather than picking a neighbour by accident.
	"""
	position = 0
	for index, (_, label) in enumerate(tab_labels(app)):
		if position <= x < position + len(label):
			return index
		position += len(label) + 2
	return None


def rule_line(width: int) -> Text:
	"""The rule that separates the tabs from the list."""
	return Text('─' * width, style='dim')


def job_line(app: TriageApp, job: dict, width: int, selected: bool) -> Text:
	"""One row: cursor marker, status, date, title · company, then the
	source right-aligned if the terminal is wide enough for it."""
	reverse = ' reverse' if selected else ''
	line = Text()
	line.append('▸ ' if selected else '  ')
	status_slug = job['status']
	label = keys.LABELS.get(status_slug, status_slug)
	mark = f'{label:<8}  '
	line.append(mark, style=keys.STATUS_STYLES.get(status_slug, 'white') + reverse)
	has_date = width >= 45
	if has_date:
		# The database stores a full ISO timestamp; the row's column
		# is the day the job was published.
		line.append(f'{job["published"][:10]:<11}', style='dim' + reverse)
	source = job.get('source') or ''
	show_source = bool(source) and width >= 60
	prefix = 12 + (11 if has_date else 0)
	budget = width - prefix
	if show_source:
		budget -= len(source) + 2
		if budget < 12:
			show_source, budget = False, width - prefix
	middle = f'{job["title"]} · {job["company"]}'
	if len(middle) > budget:
		middle = f'{middle[: budget - 1]}…'
	line.append(middle, style=reverse.strip())
	if show_source:
		room = width - prefix - len(middle) - len(source)
		if room >= 1:
			line.append(' ' * room + source, style='dim' + reverse)
	return line


def visible_jobs(app: TriageApp, body: int) -> list[dict]:
	"""The jobs the list is showing, top to bottom.

	One implementation for the renderer and the mouse: the page is
	PAGE jobs starting at the cursor's page, windowed on the cursor
	when the page out-talls the body.
	"""
	if not app._view or body <= 0:
		return []
	page_start = (app._selection // keys.PAGE) * keys.PAGE
	page = app._view[page_start : page_start + keys.PAGE]
	start = 0
	if len(page) > body:  # the page out-talls the terminal: window on the cursor
		in_page = app._selection - page_start
		start = max(0, min(in_page - body // 2, len(page) - body))
	return page[start : start + body]


def job_lines(app: TriageApp, body: int, width: int) -> list[Text]:
	"""A page of the view, windowed on the cursor when the page
	out-talls the terminal."""
	if not app._view:
		other = next(
			(name for name in keys.FILTERS if name != app._filter and app._count(name) > 0),
			None,
		)
		hint = f'no "{keys.LABELS.get(app._filter, app._filter)}" jobs'
		if other:
			hint += f' — tab to "{keys.LABELS.get(other, other)}"'
		line = Text(' ' * max(0, (width - len(hint)) // 2))
		line.append(hint, style='dim')
		return [line] + [Text() for _ in range(body - 1)]
	selected = app._view[app._selection]
	lines = [job_line(app, job, width, job is selected) for job in visible_jobs(app, body)]
	return lines + [Text() for _ in range(body - len(lines))]


def body_lines(app: TriageApp, body: int, width: int) -> list[Text]:
	"""The body area: a page of jobs once loaded, a centred notice
	before that (or after a load error)."""
	if app._state == 'ready':
		return job_lines(app, body, width)
	message = (
		f'{app._error} — r retries, q quits' if app._state == 'error' else 'loading the database…'
	)
	notice = Text(' ' * max(0, (width - len(message)) // 2))
	notice.append(message, style='red' if app._state == 'error' else 'dim')
	lines = [Text() for _ in range(max(0, body // 2 - 1))] + [notice]
	return lines + [Text() for _ in range(max(0, body - len(lines)))]


def _clip(text: str, width: int) -> str:
	"""One value cut to the pane's width, with an ellipsis when cut."""
	if width <= 0:
		return ''
	return text if len(text) <= width else f'{text[: width - 1]}…'


def _field(label: str, value: str, width: int) -> Text:
	"""One labelled fact; the labels' width is fixed so values line up."""
	line = Text()
	line.append(f'{label:<10} ', style='bold')
	line.append(_clip(value, max(0, width - 11)))
	return line


def details_lines(app: TriageApp, body: int, width: int) -> list[Text]:
	"""The selected job's full record, one line each, padded to `body`.

	Everything a decision needs that the one-line list cannot hold:
	where and when it was seen, what it pays, the note diary, who moved
	its status and when, and the cover letter a future step attaches.
	Reads are cached per job id by the app (TriageApp._detail_sections);
	the pane simply asks, so a cursor move updates it.
	"""
	if body <= 0:
		return []
	lines: list[Text] = []
	job = app._view[app._selection] if app._view else None
	if job is None:
		lines.append(Text('no job selected', style='dim'))
	else:
		history, attachments = app._detail_sections(job)
		locations = ', '.join(job.get('locations') or [])
		first_seen = (job.get('first_seen') or '—')[:10]
		last_seen = (job.get('last_seen') or '—')[:10]
		lines += [
			_field('company', job.get('company') or '—', width),
			_field('title', job.get('title') or '—', width),
			_field('published', (job.get('published') or '—')[:10], width),
			_field('first seen', first_seen, width),
			_field('last seen', last_seen, width),
			_field('locations', locations or '—', width),
			_field('salary', job.get('salary') or '—', width),
			_field('source', job.get('source') or '—', width),
			_field('url', job.get('url') or '—', width),
			_field('notes', '', width),
		]
		lines += _note_lines((job.get('notes') or '').splitlines(), width)
		lines.append(Text('history', style='bold'))
		lines += _history_lines(history, width)
		lines.append(Text('attachments', style='bold'))
		lines += _attachment_lines(attachments, width)
	lines = lines[:body]
	return lines + [Text() for _ in range(body - len(lines))]


def _note_lines(notes: list[str], width: int) -> list[Text]:
	"""The note diary under its label, one indented line per note."""
	if not notes:
		return [Text('  —', style='dim')]
	return [Text(f'  {_clip(note, max(0, width - 2))}', style='dim') for note in notes]


def _history_lines(history: list[dict], width: int) -> list[Text]:
	"""`when  old → new  (actor)` per change, in the order they were made."""
	if not history:
		return [Text('  —', style='dim')]
	lines = []
	for change in history:
		old = change.get('old')
		old_label = keys.LABELS.get(old, old or '—')
		new_label = keys.LABELS.get(change.get('new'), change.get('new') or '—')
		when = (change.get('at') or '')[:16].replace('T', ' ')
		lines.append(
			Text(
				_clip(f'  {when}  {old_label} → {new_label}  ({change.get("actor") or "—"})', width),
				style='dim',
			)
		)
	return lines


def _attachment_lines(attachments: list[dict], width: int) -> list[Text]:
	"""`kind: path` per file — the cover letter's path is the point."""
	if not attachments:
		return [Text('  —', style='dim')]
	return [
		Text(_clip(f'  {row.get("kind")}: {row.get("path")}', width), style='dim')
		for row in attachments
	]


def body_with_details(app: TriageApp, body: int, width: int) -> list[Text]:
	"""The list and the details pane side by side, for frame().

	The live screen gets the same split from the Horizontal's two
	widgets; this composes it without a screen so the headless tests
	can see the pane. The pane's separator is one column plus the
	space after it, so the left column gets `width - DETAILS_WIDTH`.
	"""
	list_width = max(20, width - DETAILS_WIDTH)
	pane_width = max(0, width - list_width - 2)
	if pane_width < 10:  # no room to split: the list keeps the screen
		return body_lines(app, body, width)
	left = body_lines(app, body, list_width)
	right = details_lines(app, body, pane_width)
	separator = Text('│ ', style='dim')
	return [job_line + separator + detail for job_line, detail in zip(left, right)]


def status_line(app: TriageApp, width: int) -> Text:
	"""The selected job's URL on the left, the cursor's place on the
	right; "N jobs in the database" when the filter is empty."""
	text = Text()
	pager = ''
	if app._view:
		pages = max(1, -(-len(app._view) // keys.PAGE))
		pager = f'{app._selection + 1}/{len(app._view)} page {app._selection // keys.PAGE + 1}/{pages}'
	elif app._state == 'ready':
		pager = f'{len(app._jobs)} jobs in the database'
	url = app._selected_url() or ''
	room = max(0, width - len(pager) - 2)
	url = url[:room]
	text.append(url, style='dim')
	if pager:
		text.append(' ' * max(1, width - len(url) - len(pager)))
		text.append(pager, style='dim')
	return text


def foot_line(app: TriageApp, width: int) -> Text:
	"""A failed write wins, then a running scrape's progress, then a
	pending `g`, then the latest flash, then the short key map generated
	from the resolved table.

	The short form is `keys.footer`, so a `[tui.keys]` rebind and a new
	row in keys.py show up here without this function knowing either.
	The scrape line outranks a flash because it reports the work in
	flight; the `N new` flash that replaces it is what the reload follows.
	"""
	if app._write_error:
		return Text(app._write_error[: max(0, width - 1)], style='bold red')
	if app._scraping:
		if app._scrape_progress is not None:
			done, total = app._scrape_progress
			line = f'scraping {done}/{total} boards…'
		else:
			line = 'scraping…'
		return Text(line[: max(0, width)], style='yellow')
	if app._g_pending():
		line = f'g… {keys.footer(app._keys, max(0, width - 3))}'
		return Text(line[: max(0, width)], style='dim')
	if app._flash:
		return Text(app._flash[: max(0, width - 1)], style='yellow')
	return Text(keys.footer(app._keys, width)[: max(0, width)], style='dim')
