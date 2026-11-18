"""The TUI's fixed tables: the status vocabulary it paints and the key
map it listens to.

Everything here is data. The status slugs, labels and colours are
derived from jobtriage.status — the one table the CLI and the TUI share
— so the TUI cannot show a status the database does not have. The key
map is one tuple of `Key` rows, and every piece of keyboard furniture
is generated from it: Textual's bindings, the footer's short form and
the `?` overlay. That is the point of the table: a rebind in
`[tui.keys]` changes all three at once, so help and footer cannot go
stale.

Each row carries the canonical action name (what the config writes),
the Textual action it runs, the default key(s), one help line and —
when `footer` is not None — the short phrase the footer shows. Status
keys are generated from FILTERS in workflow order and named by their
slug (`skipped = "x"`), so adding a status later is one line in
STATUS_KEYS (plus its row in jobtriage.status).

`PAGE`, `DOUBLE_PRESS` and `G_CHORD` live here because they are key
semantics, not layout: a page is what pgup/pgdn move, and the chords
need a window to land in.

The table's fourth consumer is the README: `readme_section()` renders
it as the key-map markdown the docs carry, and a test regenerates and
compares it, so the install docs cannot describe a keyboard the TUI no
longer has.
"""

from dataclasses import dataclass, replace

from textual.binding import Binding

from jobtriage import status

# The status table's vocabulary. FILTERS is the tab order, which follows
# the workflow rather than the status table's cycle: new jobs first, then
# the queue, then what happened to them, then everything.
STATUSES = tuple(entry.slug for entry in status.STATUSES)
FILTERS = ('new', 'to_apply', 'applied', 'skipped', 'all')
DEFAULT_FILTER = status.DEFAULT_FILTER

# What each slug is called on screen and painted with — the table's
# values, so the TUI shows exactly what the CLI prints.
LABELS = {entry.slug: entry.label for entry in status.STATUSES}
STATUS_STYLES = {entry.slug: entry.colour for entry in status.STATUSES}

# A page of the list, how fast space×2 has to land, and the window the
# second `g` has to arrive in.
PAGE = 30
DOUBLE_PRESS = 0.4
G_CHORD = 0.5

# The key each status gets. Adding a status is one row here; every
# status key is generated below from this mapping in FILTERS order.
STATUS_KEYS = {
	'new': 'n',
	'to_apply': 't',
	'applied': 'a',
	'skipped': 's',
}

# FILTERS must cover every status exactly once, or a slug would be
# unreachable from the tab bar and a tab would show nothing.
assert set(FILTERS) == {*STATUSES, 'all'}, 'FILTERS must be the statuses plus all'
assert set(STATUS_KEYS) == set(STATUSES), 'every status needs a key'


@dataclass(frozen=True, slots=True)
class Key:
	"""One row of the key map.

	`name` is the canonical action name — the name `[tui.keys]` uses —
	and `action` is the Textual action string, which may carry an
	argument (`status("skipped")`). `footer` is the short phrase the
	footer shows; None keeps a binding out of the short form (it is
	still in `?` help, which always lists the whole table).
	"""

	name: str
	action: str
	keys: tuple[str, ...]
	help: str
	footer: str | None = None


def _status_keys() -> tuple[Key, ...]:
	"""The four status keys, in the tab bar's workflow order."""
	return tuple(
		Key(
			slug,
			f'status("{slug}")',
			(STATUS_KEYS[slug],),
			f'set {LABELS[slug]} and move on',
			footer=LABELS[slug],
		)
		for slug in FILTERS
		if slug != 'all'
	)


def _tab_keys() -> tuple[Key, ...]:
	"""`1`–`5` jump straight to a tab; generated from FILTERS so the
	numbers follow the tab order."""
	return tuple(
		Key(
			f'tab_{index + 1}',
			f'tab({index})',
			(str(index + 1),),
			f'jump to the {LABELS.get(name, name)} tab',
		)
		for index, name in enumerate(FILTERS)
	)


# The one table. Order is the help overlay's order and the footer's
# preference order (the footer fills from the top until the width runs
# out), so the everyday keys — status and undo — come first.
KEY_TABLE: tuple[Key, ...] = (
	*_status_keys(),
	Key('undo', 'undo', ('u',), 'undo the last status change', footer='undo'),
	Key('down', 'down', ('j', 'down'), 'move down one job', footer='move'),
	Key('up', 'up', ('k', 'up'), 'move up one job'),
	Key('first', 'g', ('g',), 'press twice for the first job', footer='first'),
	Key('last', 'last', ('G',), 'go to the last job', footer='last'),
	Key('half_down', 'half_page_down', ('ctrl+d',), 'move half a screen down'),
	Key('half_up', 'half_page_up', ('ctrl+u',), 'move half a screen up'),
	Key('page_down', 'page_down', ('pagedown',), 'move a page down'),
	Key('page_up', 'page_up', ('pageup',), 'move a page up'),
	Key('tab_prev', 'filter_prev', ('left', 'h', 'shift+tab'), 'previous tab', footer='tabs'),
	Key('tab_next', 'filter_next', ('right', 'l', 'tab'), 'next tab'),
	*_tab_keys(),
	# aliases kept for one release of muscle memory; hidden from the
	# footer, present in ? so nobody has to guess what space does
	Key('applied_space', 'space', ('space',), 'space twice: applied (alias for a)'),
	Key('skipped_shift', 'status("skipped")', ('S',), 'skip and move on (alias for s)'),
	Key('open', 'open', ('enter',), 'open the job in Chrome', footer='open'),
	Key('details', 'details', ('d',), 'toggle the details pane', footer='details'),
	Key('search', 'search', ('/',), 'search title and company', footer='search'),
	Key('copy_url', 'copy_url', ('y',), 'copy the job URL', footer='copy'),
	Key('note', 'note', ('m',), 'add a note to the job', footer='note'),
	Key('reload', 'reload', ('r',), 'reload from the database', footer='reload'),
	Key('scrape', 'scrape', ('R',), 'run a scrape in the background', footer='scrape'),
	Key('help', 'help', ('?',), 'show this help', footer='help'),
	Key('quit', 'quit', ('q', 'ctrl+c', 'ctrl+q'), 'quit', footer='quit'),
)

NAMES = tuple(entry.name for entry in KEY_TABLE)

# The keys as the footer and the help overlay spell them. Textual's key
# identifiers are precise but not pretty; this is presentation only.
_DISPLAY = {
	'down': '↓',
	'up': '↑',
	'left': '←',
	'right': '→',
	'pagedown': 'pgdn',
	'pageup': 'pgup',
	'shift+tab': '⇧tab',
	'ctrl+d': '^d',
	'ctrl+u': '^u',
	'ctrl+c': '^c',
	'ctrl+q': '^q',
}


def display_key(key: str) -> str:
	"""One key as the screen writes it."""
	return _DISPLAY.get(key, key)


def display_keys(entry: Key) -> str:
	"""A row's keys, joined for the footer or the help overlay."""
	return '/'.join(display_key(key) for key in entry.keys)


def resolve(overrides: dict[str, str] | None = None) -> tuple[Key, ...]:
	"""The table with `[tui.keys]` applied.

	A configured key replaces all of an action's defaults: `quit = "x"`
	means x quits, not x and q. Unknown names raise ValueError — the
	config loader validates first so it can name the file, this is the
	last line of defence for direct callers.
	"""
	overrides = overrides or {}
	unknown = set(overrides) - set(NAMES)
	if unknown:
		raise ValueError(f'unknown key action(s): {", ".join(sorted(unknown))}')
	return tuple(
		replace(entry, keys=(overrides[entry.name],)) if entry.name in overrides else entry
		for entry in KEY_TABLE
	)


def bindings(entries: tuple[Key, ...]) -> list[Binding]:
	"""The Textual bindings for a resolved table.

	Every binding is priority so focus — now or in plan 07's panes —
	never eats a triage key. `show` mirrors the footer's short form,
	which is all Textual's own footer would need if we used it.
	"""
	return [
		Binding(
			key,
			entry.action,
			description=entry.help,
			show=entry.footer is not None,
			priority=True,
		)
		for entry in entries
		for key in entry.keys
	]


def footer(entries: tuple[Key, ...], width: int) -> str:
	"""The footer's short form: rows with a `footer` phrase, greedy
	from the top, always ending in the help row (so `? help` survives
	every width)."""
	tail_entry = next(entry for entry in entries if entry.name == 'help')
	tail = f'{display_keys(tail_entry)} {tail_entry.footer}'
	chosen = []
	for entry in entries:
		if entry is tail_entry or entry.footer is None:
			continue
		item = f'{display_keys(entry)} {entry.footer}'
		if len(' · '.join([*chosen, item, tail])) <= width:
			chosen.append(item)
	if not chosen:
		return tail[:width]
	return ' · '.join([*chosen, tail])


# -- the README's copy ------------------------------------------------
#
# The key map is documented twice because it is useful twice: on screen
# (help, footer) and in the README, where someone deciding whether to
# install reads it. The README block is generated from the same table
# and fenced by these markers, so the two copies are one table plus a
# checked-in rendering — tests/tui/test_readme.py fails on any drift,
# and readme_section() is what a maintainer re-runs to update it.
README_START = '<!-- tui keys: generated from src/jobtriage/tui/keys.py — do not edit by hand -->'
README_END = '<!-- end tui keys -->'


def readme_section(entries: tuple[Key, ...] | None = None) -> str:
	"""The README's key map: the resolved default table as markdown,
	markers included so the drift test compares a self-contained block.

	`entries` defaults to the shipped table (`resolve()`), not to
	whatever an op folder rebound: the README documents the defaults,
	and `[tui.keys]` is the per-user overlay on them.
	"""
	rows = resolve() if entries is None else entries
	lines = [
		README_START,
		'',
		'| Key | Action |',
		'|---|---|',
	]
	for entry in rows:
		spelled = ' / '.join(f'`{display_key(key)}`' for key in entry.keys)
		lines.append(f'| {spelled} | {entry.help} |')
	lines += ['', README_END]
	return '\n'.join(lines)
