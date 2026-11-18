"""The `?` overlay: one modal screen generated from the key table.

There is no second copy of the key map here. `help_text` walks the
resolved rows the app is using — after `[tui.keys]` — so a rebound key
is what the overlay shows, and a row added to keys.py appears without
anyone remembering to update a help string.

The screen deliberately earns its "any key dismisses" behaviour:
`check_consume_key` claims every key, which makes Textual filter the
app's priority bindings out of the binding chain while the modal is
up. Without it, `q` would quit the application from behind the overlay
instead of closing it, because the app's bindings are priority (they
must be, so triage keys beat focus).
"""

from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from jobtriage.tui import keys

CSS = """
HelpScreen {
	align: center middle;
}
#help {
	width: 64;
	max-width: 95%;
	height: auto;
	max-height: 90%;
	padding: 1 2;
	background: $panel;
	border: round $accent;
}
#help-text {
	width: auto;
}
"""


def help_text(entries: tuple[keys.Key, ...]) -> Text:
	"""Every binding as `key  help`, one row each, in table order."""
	lines = [Text('keys · any key closes', style='bold'), Text()]
	for entry in entries:
		line = Text()
		line.append(f'{keys.display_keys(entry):<12}', style='bold')
		line.append(entry.help, style='dim')
		lines.append(line)
	return Text('\n').join(lines)


class HelpScreen(ModalScreen):
	"""The overlay itself. Dismiss with any key, including q and esc."""

	# Inherited tab/focus bindings would swallow a key instead of
	# dismissing; the overlay wants all of them.
	_inherit_bindings = False
	BINDINGS: ClassVar[list[Binding]] = []

	CSS = CSS

	def __init__(self, entries: tuple[keys.Key, ...]):
		super().__init__()
		self.entries = entries

	def compose(self) -> ComposeResult:
		with VerticalScroll(id='help'):
			yield Static(help_text(self.entries), id='help-text')

	def check_consume_key(self, key: str, character: str | None) -> bool:
		"""Claim every key, which hides the app's priority bindings."""
		return True

	def on_key(self, event) -> None:
		"""The first key of any kind closes the overlay."""
		self.dismiss()
		event.stop()
