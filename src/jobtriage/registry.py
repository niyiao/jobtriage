"""The scraper registry: every board the tool can run, however it arrived.

A plugin is a module with `NAME` and `recent_jobs`; it can come from
three places, in load order:

1. **built-ins** — `jobtriage.scrapers.*`, found with
   `pkgutil.iter_modules` (never a hand-written list, so a new module
   in the package is a new board);
2. **installed packages** — the entry-point group
   `jobtriage.scrapers`, so a board pack can be `pip install`ed;
3. **workspace plugins** — `<op>/scrapers/*.py`, one file per board,
   loaded by path under `jobtriage_workspace_plugins.<stem>`. Files
   starting with `_` are skipped (the template is `_example.py`).

Nothing is ever loaded from the current directory: a plugin is
arbitrary code with the user's permissions, so the only two places it
may come from are the installed environment and the op folder.

A later source overrides an earlier one with the same `NAME`, and the
registry records a notice (`using workspace scraper "remotive" instead
of the built-in`). That is the point of the workspace tier: a user can
patch a board the day it breaks. Notices are collected on
`Registry.notices` rather than printed here — the CLI/pipeline owns
stderr, and a library that prints is hard to test. The caller prints
them once after `load`.

A plugin that cannot be used is **broken**, never fatal: an import
error, a missing or malformed `NAME`, a missing `recent_jobs`, an
`API_VERSION` newer than this build, or an alias that clashes with
another board. `Registry.broken` maps each name to its reason (for a
file that never imported, the key is its stem), and `Board.broken`
carries the same reason for an importable plugin. `enabled()` and
`run()` skip broken boards; the rest of the run goes on.

`run` is also the contract boundary (plan 04 step 3): it times the
call, isolates a board's exception, validates every returned row,
drops the bad ones with a numbered reason, truncates to `limit` and
tags `source`. Nothing invalid ever reaches the database.
"""

import re
import sys
import time
import types
import urllib.parse
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from importlib import import_module
from importlib.metadata import entry_points
from importlib.util import find_spec, module_from_spec, spec_from_file_location
from pkgutil import iter_modules

from jobtriage.config import Config
from jobtriage.plugin import API_VERSION, http
from jobtriage.workspace import Workspace

BUILTIN_PACKAGE = 'jobtriage.scrapers'
ENTRY_POINT_GROUP = 'jobtriage.scrapers'
WORKSPACE_MODULE = 'jobtriage_workspace_plugins'

ORIGIN_BUILTIN = 'built-in'
ORIGIN_PACKAGE = 'package'
ORIGIN_WORKSPACE = 'workspace'

# The id a board is addressed by: `-s`, jobs.source, [sources.<id>].
NAME_PATTERN = re.compile(r'^[a-z0-9-]+$')

# The six keys `jobs.plugin.Job` declares. Kept as a tuple so the
# "missing key" message names the first one in the documented order;
# `test_registry` asserts it matches the TypedDict.
JOB_KEYS = ('title', 'company', 'url', 'published', 'locations', 'salary')

clock = time.perf_counter


@dataclass(frozen=True)
class Board:
	"""One discovered board: its module plus what the listings need.

	`module` is the imported plugin. `origin` is 'built-in', 'package'
	or 'workspace'. `aliases` and `title` come from the module's
	declarations, with `title` defaulting to the name title-cased.
	`broken` is the reason this board cannot run, or ''.
	"""

	name: str
	module: types.ModuleType
	origin: str
	aliases: tuple[str, ...] = ()
	title: str = ''
	homepage: str = ''
	terms: str = ''
	broken: str = ''


@dataclass
class RunStats:
	"""One board's numbers for one `run`, for `sources test` and health.

	`fetched` is what the board returned; `valid` is what survived
	validation and the limit; `dropped_invalid` is the difference,
	explained row by row in `problems`. `duration` is seconds around
	the whole wrapper (plan 02's table stores milliseconds). `error`
	is the board's exception, in one line, and means the run produced
	no jobs at all.
	"""

	fetched: int = 0
	valid: int = 0
	dropped_invalid: int = 0
	duration: float = 0.0
	error: str = ''
	problems: list[str] = field(default_factory=list)


class RegistryError(Exception):
	"""A registry lookup or run that cannot start."""


class UnknownBoard(RegistryError, LookupError):
	"""No board (or alias) by that name."""


class BrokenBoard(RegistryError):
	"""The board exists but cannot run; `reason` says why."""

	def __init__(self, name: str, reason: str):
		self.name = name
		self.reason = reason
		super().__init__(f'{name} is broken: {reason}')


class Registry:
	"""The boards one `load` found, keyed by canonical name.

	`boards` keeps load order, which is built-ins, packages, workspace
	and then file order — so two runs are diffable. `broken` is the
	flat name -> reason view, including plugins that never imported
	(no `Board` exists for those). `notices` holds override messages
	for the caller to print once.
	"""

	def __init__(self):
		self.boards: dict[str, Board] = {}
		self.broken: dict[str, str] = {}
		self.notices: list[str] = []
		self._aliases: dict[str, str] = {}

	def __contains__(self, name: object) -> bool:
		return isinstance(name, str) and self.by_alias(name) is not None

	def __iter__(self) -> Iterator[Board]:
		return iter(self.boards.values())

	def __len__(self) -> int:
		return len(self.boards)

	def by_alias(self, name: str) -> Board | None:
		"""The board a `-s` value names, canonical or alias, or None.

		A canonical name wins over an alias; the loader refuses to create
		the ambiguity in the first place (a name that is already an alias
		makes the later board broken).
		"""
		board = self.boards.get(name)
		if board is not None:
			return board
		owner = self._aliases.get(name)
		return self.boards.get(owner) if owner is not None else None

	def all(self) -> list[Board]:
		"""Every discovered board, including broken ones, in load order."""
		return list(self.boards.values())

	def canonical(self, name: str) -> str:
		"""The canonical NAME a name or alias refers to, or `name` unchanged.

		Config keys are written by hand — `[sources].disabled` and
		`[sources.<board>]` — and a user reasonably reaches for the short
		alias `-s` accepts. Passing them through here once means the rest
		of the tool only ever compares canonical names, while a name no
		board answers to is kept as written so the caller can say so.
		"""
		board = self.by_alias(name)
		return board.name if board is not None else name

	def disabled(self, config: Config) -> set[str]:
		"""`config.sources.disabled` as canonical names.

		`disabled = ["rm"]` and `disabled = ["remotive"]` are the same
		choice; a stale name (a plugin that was removed) stays in the
		set, which is what lets `sources` warn about it and `sources
		enable` clean it up.
		"""
		return {self.canonical(name) for name in config.sources.disabled}

	def enabled(self, config: Config) -> list[Board]:
		"""The boards a run may use: not broken, not disabled by config."""
		disabled = self.disabled(config)
		return [
			board for board in self.boards.values() if not board.broken and board.name not in disabled
		]

	def run(
		self,
		name: str,
		*,
		locations: Sequence[str] | None = None,
		limit: int | None = 50,
		**options: object,
	) -> tuple[list[dict], RunStats]:
		"""Run one board and return its validated jobs plus the numbers.

		`options` are the board's own knobs (family parameters and
		`[sources.<board>]` values), passed through untouched. A board's
		exception becomes `stats.error` and an empty list — one broken
		board must not sink a whole scrape. An unknown name raises
		`UnknownBoard`; a broken one raises `BrokenBoard`, because that
		is the caller asking for something that cannot start, not a
		board failing mid-run.
		"""
		board = self.by_alias(name)
		if board is None:
			raise UnknownBoard(f'unknown board {name!r}')
		if board.broken:
			raise BrokenBoard(board.name, board.broken)

		stats = RunStats()
		started = clock()
		try:
			with http.polite(_polite_delay(board.module)):
				rows = board.module.recent_jobs(locations=locations, limit=limit, **options)
			if not isinstance(rows, list):
				raise TypeError(f'recent_jobs returned {type(rows).__name__}, expected a list')
		except Exception as error:  # noqa: BLE001 — a board's failure is its own
			stats.error = _reason(error)
			stats.duration = clock() - started
			return [], stats

		stats.fetched = len(rows)
		jobs, stats.problems = validate(rows)
		stats.dropped_invalid = len(stats.problems)
		if limit is not None:
			jobs = jobs[:limit]
		for job in jobs:
			job['source'] = board.name
		stats.valid = len(jobs)
		stats.duration = clock() - started
		return jobs, stats

	def _add(self, module: types.ModuleType, origin: str, fallback: str) -> None:
		"""Register one imported module, or record why it is unusable."""
		name = getattr(module, 'NAME', None)
		if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None:
			self.broken[fallback] = _name_problem(name)
			return

		old = self.boards.get(name)
		if old is not None:
			self.notices.append(_override_notice(old, origin, name))
			self._forget_aliases(name)

		problem: str | None = _contract_problem(module) or None
		aliases, alias_problem = _board_aliases(module, name)
		problem = problem or alias_problem or None
		if problem is None and self._aliases.get(name) is not None:
			problem = f'NAME "{name}" is already an alias of "{self._aliases[name]}"'
		if problem is None:
			problem = self._alias_problem(name, aliases) or None

		board = Board(
			name=name,
			module=module,
			origin=origin,
			aliases=aliases,
			title=_text(module, 'TITLE', name.replace('-', ' ').title()),
			homepage=_text(module, 'HOMEPAGE', ''),
			terms=_text(module, 'TERMS', ''),
			broken=problem or '',
		)
		self.boards[name] = board
		if board.broken:
			self.broken[name] = board.broken
			return
		self.broken.pop(name, None)
		for alias in aliases:
			self._aliases[alias] = name

	def _alias_problem(self, name: str, aliases: tuple[str, ...]) -> str:
		"""The first alias another board (or alias) already owns."""
		for alias in aliases:
			owner = self._aliases.get(alias)
			if owner is not None and owner != name:
				return f'alias "{alias}" is already used by "{owner}"'
			if alias in self.boards and alias != name:
				return f'alias "{alias}" is already a board name'
		return ''

	def _forget_aliases(self, name: str) -> None:
		"""Release the aliases of the board being overridden."""
		for alias in [key for key, owner in self._aliases.items() if owner == name]:
			del self._aliases[alias]


def load(ws: Workspace, config: Config) -> Registry:
	"""Discover every board from the three sources, later overriding earlier.

	`config` is part of the signature because the caller has one and
	discovery reads one setting here: `[network] user_agent` becomes the
	User-Agent every board's requests will send (plan 08 step 14).
	Enablement is `Registry.enabled(config)`, so one load serves
	`sources`, a scrape and a test run.
	"""
	http.set_user_agent(config.network.user_agent)
	registry = Registry()
	_load_builtins(registry)
	_load_packages(registry)
	_load_workspace(registry, ws)
	return registry


def validate(rows: Sequence[object]) -> tuple[list[dict], list[str]]:
	"""The rows that honour the contract, plus one reason per dropped row.

	Each reason is numbered by the row's position in what the board
	returned (`job 3: ...`), so `sources test` can point at the offending
	output. Extra keys are kept (the pipeline adds `role` later); only
	the six contract keys are checked, and a tuple of strings is
	normalised to the list `Job` declares. Accepted rows are copied so
	the board's own objects are not mutated with the source tag.
	"""
	kept: list[dict] = []
	problems: list[str] = []
	for index, row in enumerate(rows, 1):
		problem = _job_problem(row)
		if problem is not None:
			problems.append(f'job {index}: {problem}')
			continue
		job = dict(row)
		job['locations'] = list(job['locations'])
		kept.append(job)
	return kept, problems


def _job_problem(row: object) -> str | None:
	"""Why this row cannot be written, or None when it is valid."""
	if not isinstance(row, dict):
		return f'not a job table ({type(row).__name__})'
	for key in JOB_KEYS:
		if key not in row:
			return f'missing "{key}"'

	for key in ('title', 'company'):
		value = row[key]
		if not isinstance(value, str):
			return f'{key} is not a string'
		if not value.strip():
			return f'{key} is empty'

	url = row['url']
	if not isinstance(url, str):
		return 'url is not a string'
	parts = urllib.parse.urlsplit(url)
	if parts.scheme not in ('http', 'https') or not parts.netloc:
		return f'url {url!r} is not an absolute http(s) URL'

	published = row['published']
	if published is not None:
		if not isinstance(published, str):
			return f'published {published!r} is not an ISO 8601 string or None'
		if not _is_iso(published):
			return (
				f'published {published!r} is not ISO 8601 — convert it in parse() '
				'with jobtriage.plugin.dates.iso() or dates.relative()'
			)

	locations = row['locations']
	if isinstance(locations, (str, bytes)) or not isinstance(locations, Sequence):
		return f'locations is {type(locations).__name__}, expected a list of strings'
	for position, tag in enumerate(locations):
		if not isinstance(tag, str):
			return f'locations[{position}] is not a string'

	salary = row['salary']
	if salary is not None and not isinstance(salary, str):
		return f'salary is {type(salary).__name__}, expected a string or None'
	return None


def _is_iso(value: str) -> bool:
	"""Can `datetime` read this as an ISO 8601 date or datetime?"""
	text = value[:-1] + '+00:00' if value[-1:] in ('Z', 'z') else value
	try:
		datetime.fromisoformat(text)
	except ValueError:
		return False
	return True


def _load_builtins(registry: Registry) -> None:
	"""The package's own modules, discovered rather than listed."""
	for short in _builtin_names():
		fullname = f'{BUILTIN_PACKAGE}.{short}'
		try:
			module = import_module(fullname)
		except Exception as error:  # noqa: BLE001 — a bad built-in is one broken board
			registry.broken[short] = f'import failed: {_reason(error)}'
			continue
		registry._add(module, ORIGIN_BUILTIN, short)


def _builtin_names() -> list[str]:
	"""(Sub)module names in the scraper package, sorted, private ones out."""
	spec = find_spec(BUILTIN_PACKAGE)
	if spec is None or spec.submodule_search_locations is None:
		return []
	return sorted(
		info.name
		for info in iter_modules(spec.submodule_search_locations)
		if not info.name.startswith('_')
	)


def _load_packages(registry: Registry) -> None:
	"""Boards installed as packages, via the `jobtriage.scrapers` group."""
	try:
		found = list(entry_points(group=ENTRY_POINT_GROUP))
	except Exception as error:  # noqa: BLE001 — broken metadata must not stop a run
		registry.notices.append(f'could not read installed scraper packages: {_reason(error)}')
		return
	for entry in found:
		try:
			module = entry.load()
		except Exception as error:  # noqa: BLE001 — one broken package is one broken board
			registry.broken[entry.name] = f'import failed: {_reason(error)}'
			continue
		registry._add(module, ORIGIN_PACKAGE, entry.name)


def _load_workspace(registry: Registry, ws: Workspace) -> None:
	"""`<op>/scrapers/*.py`, one file per board, never from the cwd."""
	folder = ws.scrapers
	if not folder.is_dir():
		return
	for file in sorted(folder.glob('*.py')):
		if file.name.startswith('_'):
			continue  # _example.py is a template, not a board
		fullname = f'{WORKSPACE_MODULE}.{file.stem}'
		spec = spec_from_file_location(fullname, file)
		if spec is None or spec.loader is None:
			registry.broken[file.stem] = f'cannot import {file}'
			continue
		module = module_from_spec(spec)
		# The module has to be importable by name while it executes:
		# dataclasses and picklers look themselves up in sys.modules.
		sys.modules[fullname] = module
		try:
			spec.loader.exec_module(module)
		except Exception as error:  # noqa: BLE001 — a user's typo is one broken board
			sys.modules.pop(fullname, None)
			registry.broken[file.stem] = f'import failed: {_reason(error)}'
			continue
		registry._add(module, ORIGIN_WORKSPACE, file.stem)


def _name_problem(name: object) -> str:
	"""The reason a plugin's NAME cannot be used."""
	if name is None:
		return 'missing NAME (a [a-z0-9-]+ id, e.g. NAME = "board")'
	if not isinstance(name, str):
		return f'NAME is {type(name).__name__}, expected a [a-z0-9-]+ string'
	return f'NAME {name!r} must match [a-z0-9-]+'


def _contract_problem(module: types.ModuleType) -> str:
	"""The first required attribute/api-version problem, or ''."""
	recent = getattr(module, 'recent_jobs', None)
	if recent is None:
		return 'missing recent_jobs(*, locations, limit, **options)'
	if not callable(recent):
		return 'recent_jobs is not callable'

	version = getattr(module, 'API_VERSION', 1)
	if isinstance(version, bool) or not isinstance(version, int):
		return f'API_VERSION {version!r} is not an integer'
	if version > API_VERSION:
		return f'targets plugin API {version}, this build speaks {API_VERSION}'
	return ''


def _board_aliases(module: types.ModuleType, name: str) -> tuple[tuple[str, ...], str]:
	"""The module's ALIASES, deduplicated and without its own name."""
	raw = getattr(module, 'ALIASES', ())
	if raw is None:
		return (), ''
	if isinstance(raw, str):
		raw = (raw,)
	try:
		values = tuple(raw)
	except TypeError:
		return (), 'ALIASES must be a tuple of names'
	aliases: list[str] = []
	for value in values:
		if not isinstance(value, str) or NAME_PATTERN.fullmatch(value) is None:
			return (), f'ALIASES has {value!r}, which is not a [a-z0-9-]+ name'
		if value != name and value not in aliases:
			aliases.append(value)
	return tuple(aliases), ''


def _text(module: types.ModuleType, attribute: str, default: str) -> str:
	"""An optional string attribute, with a default when absent/None."""
	value = getattr(module, attribute, default)
	return default if value is None else str(value)


def _polite_delay(module: types.ModuleType) -> float:
	"""The module's POLITE_DELAY, coerced to a usable number of seconds."""
	try:
		return max(0.0, float(getattr(module, 'POLITE_DELAY', 0) or 0))
	except (TypeError, ValueError):
		return 0.0


def _override_notice(old: Board, origin: str, name: str) -> str:
	"""The one line saying a later source replaced an earlier board."""
	was = {
		ORIGIN_BUILTIN: 'the built-in',
		ORIGIN_PACKAGE: 'the installed package',
		ORIGIN_WORKSPACE: 'an earlier workspace scraper',
	}.get(old.origin, old.origin)
	return f'using {origin} scraper "{name}" instead of {was}'


def _reason(error: BaseException) -> str:
	"""One line for a board's failure: its type and message."""
	text = str(error)
	return f'{type(error).__name__}: {text}' if text else type(error).__name__
