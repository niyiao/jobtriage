"""The status table: every status a job can carry, defined once.

The database stores slugs (`to_apply`), the TUI shows labels
(`to apply`), and the spreadsheet this replaces held its own spellings
(`none` for what is now `new`). All three come from the one table
below, so a new status cannot exist in the TUI without the CLI and the
import knowing about it — plan 06's key map and step 6's CSV import
both read this module rather than keeping copies.

The table's order is meaningful: it is the TUI's ←/→ cycle, and
`new` comes first because it is where every job starts. Colours are
the ones tui.py already painted the sheet's statuses with (`none` was
dim, `skip` red, `to apply` cyan, `applied` green), so moving the TUI
onto the database does not change how a triaged queue looks.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Status:
	"""One row: the key the database stores, what the TUI shows, how it
	paints it, and the spreadsheet cell value it replaces.

	`sheet` lives here rather than in a second mapping because the
	import is the only consumer and a separate table would be one more
	place to forget when step 9 adds statuses. That is fine for those:
	a new status simply was not in the old sheet, and `from_sheet`
	never has to produce it.
	"""

	slug: str
	label: str
	colour: str
	sheet: str
	default_filter: bool = False


# Slugs first, in cycle order. Keeping this a tuple (not a dict keyed by
# slug) preserves that order for the TUI; the lookups below are built
# from it and cannot drift.
STATUSES: tuple[Status, ...] = (
	Status('new', 'new', 'dim', 'none'),
	Status('skipped', 'skip', 'red', 'skip'),
	Status('to_apply', 'to apply', 'cyan', 'to apply', default_filter=True),
	Status('applied', 'applied', 'green', 'applied'),
)

# The status a job gets when it is first ingested. This is not a row
# flag: exactly one status means "untriaged", and a flag on each row
# would suggest otherwise. db.py's migration writes it as the default,
# and store.ingest (step 3) will too.
DEFAULT = 'new'

# The TUI opens on the jobs still to handle (tab cycles the rest).
# Derived from the row's flag so the TUI's default and the table's
# marker cannot disagree.
DEFAULT_FILTER = next(entry.slug for entry in STATUSES if entry.default_filter)

_BY_SLUG = {entry.slug: entry for entry in STATUSES}
_BY_LABEL = {entry.label: entry for entry in STATUSES}

# A sheet cell is matched on its folded, stripped text, because the CSV
# export and hand-edited cells disagree about capitalisation and stray
# spaces but never about the words.
_BY_SHEET = {entry.sheet.casefold(): entry.slug for entry in STATUSES}


class UnknownSheetStatus(ValueError):
	"""A sheet cell held a status this build does not recognise.

	It gets its own class so the import (step 6) can catch exactly this
	per row, list the offending value, and leave that row alone. The
	message always names the value: falling back to `new` would put a
	job the user had already applied to back in the "to apply" queue,
	which is worse than refusing to import it.
	"""


def by_slug(slug: str) -> Status:
	"""The row for a stored slug, or KeyError if there is none.

	KeyError, not ValueError: the slug comes from the database or a CLI
	argument, so an unknown one is a failed lookup (the wrapper says
	which key), the way a missing dict key would be.
	"""
	try:
		return _BY_SLUG[slug]
	except KeyError:
		raise KeyError(f'unknown status slug {slug!r}') from None


def by_label(label: str) -> Status:
	"""The row for the label a human (or the TUI) uses, or KeyError.

	Exact match: labels are display text, not user input, and the only
	callers are the TUI and tests. `from_sheet` is the forgiving one.
	"""
	try:
		return _BY_LABEL[label]
	except KeyError:
		raise KeyError(f'unknown status label {label!r}') from None


def from_sheet(value: str | None) -> str:
	"""Map one Google Sheets status cell to a slug.

	Case and surrounding whitespace are ignored (`To Apply` and
	` applied ` both work): they are presentation, not a different
	status. An empty cell — `None`, `''`, or only spaces — means the
	sheet never wrote a status there, which is its `none`: those jobs
	are `new`.

	Anything else raises UnknownSheetStatus naming the value. The import
	catches it per row and reports the offender, rather than guessing:
	a silent `new` would mis-triage work already done.
	"""
	if value is None:
		return DEFAULT
	folded = value.strip().casefold()
	if not folded:
		return DEFAULT
	try:
		return _BY_SHEET[folded]
	except KeyError:
		raise UnknownSheetStatus(f'unknown sheet status {value!r}') from None
