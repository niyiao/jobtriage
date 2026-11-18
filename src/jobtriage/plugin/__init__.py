"""The public plugin API: the only import a scraper needs.

A scraper is a module with `NAME` and `recent_jobs(...)`; this package
is what it may import. Keeping that surface apart from the host's
internals (`jobtriage.registry`, `jobtriage.pipeline`) means a plugin
written against it keeps working while the app is refactored, and the
surface can be versioned: a module declares `API_VERSION` (default 1)
and the registry refuses one that targets a newer contract.

`Job` is the row contract and `LocationPrefs` what the location hooks
receive. The submodules cover the boilerplate every board would
otherwise repeat: `http` (a browser UA, a timeout, one retry, a polite
pause), `parse` (the SSR-state shapes boards ship), `dates` (the date
formats boards use), and `geo` (plan 03's location vocabulary). The
imports below are re-exports: a plugin writes
`from jobtriage.plugin import http` or `from jobtriage.plugin.http
import get_json` and needs nothing else.
"""

from collections.abc import Sequence
from typing import Protocol, TypedDict

from jobtriage.plugin import dates, geo, http, parse

# The contract this build understands. A plugin that does not declare
# its own targets 1, which is the shape the fifteen built-ins already
# have. Bump this only for a change that breaks an existing plugin.
API_VERSION = 1


class Job(TypedDict):
	"""One returned job, the same six keys every board has always used.

	`published` is an ISO 8601 string or None; the pipeline sorts by it
	as a string, which is why a board must convert its own date format
	rather than pass "3 days ago" through. `locations` is the board's
	own tags, not canonical geo tokens. `salary` is free text — boards
	bundle currency, interval and equity in it — or None.
	"""

	title: str
	company: str
	url: str
	published: str | None
	locations: list[str]
	salary: str | None


class LocationPrefs(Protocol):
	"""What `geo.board_tags` and a board's `server_locations` receive.

	`config.SearchSettings` satisfies this structurally: a resolved
	`locations` tuple of canonical geo tokens. The protocol is narrow
	on purpose — a plugin that needs the rest of the user's search
	(families, employment) should not read it through this object,
	because those are the host's, not the location vocabulary's.

	`geo.board_tags` also accepts a bare sequence of tokens; this type
	is the config-shaped one the registry actually passes.
	"""

	@property
	def locations(self) -> Sequence[str]: ...


__all__ = [
	'API_VERSION',
	'Job',
	'LocationPrefs',
	'dates',
	'geo',
	'http',
	'parse',
]
