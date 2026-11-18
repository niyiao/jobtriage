"""The triage TUI, split into data, rendering and behaviour.

`keys.py` holds the status vocabulary and the key map, `widgets.py` the
four pieces of the screen, and `app.py` the TriageApp that reads the
tracker and responds to the keys. This module is the package's public
surface, so `from jobtriage.tui import TriageApp` works whether the
caller wants the app (cli/tui.py) or the vocabulary (the tests, the
help text, a future config reader).
"""

from jobtriage.tui.app import TriageApp
from jobtriage.tui.keys import (
	DEFAULT_FILTER,
	FILTERS,
	LABELS,
	STATUS_STYLES,
	STATUSES,
)

__all__ = [
	'DEFAULT_FILTER',
	'FILTERS',
	'LABELS',
	'STATUSES',
	'STATUS_STYLES',
	'TriageApp',
]
