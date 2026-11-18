"""`jobtriage sources` — the boards: list them, inspect one, scaffold,
test, and switch one off.

The registry (plan 04) makes the board set dynamic: built-ins, installed
packages and `<op>/scrapers/*.py` all arrive at load time, and a broken
file is a line in the listing rather than an import crash. This command
is the human and agent surface over that set:

* plain `sources` is the inventory: every board with its aliases, its
  origin, whether the config disables it, and what its recorded runs
  say. `--json` is one document (`_document` has the shape), for
  scripts.
* `info` is one board's file as prose: the module docstring (the
  research notes), the terms, the location vocabulary, and the keyword
  arguments `recent_jobs` accepts — read from its signature, so it can
  never go stale against the code.
* `new` writes `<op>/scrapers/NAME.py` from the shipped `_example.py`
  with `NAME` filled in. That is the whole onboarding: one file, no
  registry to edit.
* `test` is the feedback loop: limit 5 through `registry.run`, so the
  output, the timing and every contract rejection are the real ones.
  It records nothing — a test is not a scrape, and board health should
  not move because someone checked a plugin.
* `enable` / `disable` edit `[sources].disabled` in jobtriage.toml
  through tomlkit, keeping the user's comments and layout, and they
  canonicalise aliases: `disable rm` writes `remotive`.

Exit codes follow the rest of the CLI: 0 success, 1 a board failed its
test or is broken (and `info` on a broken board), 2 an unknown name or
a bad NAME. The inventory itself does not fail on a broken board —
listing it, with its reason, is the point. Broken boards are marked
with the literal `broken:` prefix, the convention `scrape` and
`pipeline` already use; there is no colour, because a pipe or a log
file would only see escape codes.
"""

import argparse
import inspect
import json
import re
import sys
from pathlib import Path

import tomlkit
from tomlkit import exceptions as tomlkit_errors

from jobtriage import config, registry, store, workspace
from jobtriage.cli.output import print_json

# The limit a connector test asks for. Five is enough to see the shape
# of a board (titles, companies, dates, locations) and small enough that
# a hand-written plugin under development is cheap to re-run.
TEST_LIMIT = 5

# `sources new` refuses these names: `all` is the CLI's meta-board for
# `scrape -s all`, and a board named after it could never be selected.
RESERVED = {'all'}

# The NAME line the template carries; `sources new` replaces exactly it,
# so the rest of the file (comments included) is copied verbatim.
_NAME_LINE = re.compile(r'^NAME = .*$', re.MULTILINE)


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser(
		'sources',
		help='the job boards: list, inspect, scaffold, test and enable/disable',
	)
	parser.add_argument(
		'--json',
		action='store_true',
		help='print the listing as one JSON document: boards plus the broken map',
	)
	parser.set_defaults(run=run_list)
	actions = parser.add_subparsers(dest='sources_command', metavar='ACTION')

	info = actions.add_parser(
		'info',
		help='one board: docstring, terms, location vocabulary and recent_jobs options',
	)
	info.add_argument('name', metavar='NAME', help='board name or alias')
	info.add_argument(
		'--json',
		action='store_true',
		help='print the board as one JSON document (docstring, vocabulary, options)',
	)
	info.set_defaults(run=run_info)

	new = actions.add_parser(
		'new',
		help='write <op>/scrapers/NAME.py from the shipped _example.py template',
	)
	new.add_argument('name', metavar='NAME', help='the new board id ([a-z0-9-]+)')
	new.set_defaults(run=run_new)

	test = actions.add_parser(
		'test',
		help='run a board (or every enabled board) with limit 5 and report contract problems',
	)
	test.add_argument('name', nargs='?', metavar='NAME', help='board name or alias')
	test.add_argument(
		'--all',
		action='store_true',
		help='test every enabled board, one block each',
	)
	test.add_argument(
		'--json',
		action='store_true',
		help='print one JSON document (boards, broken map, ok) for scripts and the canary',
	)
	test.set_defaults(run=run_test)

	enable = actions.add_parser(
		'enable',
		help='remove a board from [sources].disabled (aliases are canonicalised)',
	)
	enable.add_argument('name', metavar='NAME', help='board name or alias')
	enable.set_defaults(run=run_enable)

	disable = actions.add_parser(
		'disable',
		help='add a board to [sources].disabled (aliases are canonicalised)',
	)
	disable.add_argument('name', metavar='NAME', help='board name or alias')
	disable.set_defaults(run=run_disable)
	return parser


# -- the inventory ---------------------------------------------------------


def run_list(args, ws) -> int:
	"""Every board, its origin, whether it is on, and what its runs say."""
	try:
		settings = config.load(ws)
	except config.ConfigError as error:
		return _failure(error)
	reg = registry.load(ws, settings)
	_print_notices(reg)
	disabled = reg.disabled(settings)
	health = _health(ws)

	if args.json:
		print_json(_document(reg, disabled, health))
	else:
		_print_listing(reg, disabled, health)
	_warn_unknown_disabled(reg, settings.sources.disabled)
	return 0


def _document(reg: registry.Registry, disabled: set[str], health: dict) -> dict:
	"""The `--json` document: `boards` in load order, then `broken`.

	Each board carries the fields the table shows plus the ones a script
	wants without a second command (`title`, `homepage`, `terms`,
	`broken`). `health` is null until the board's first recorded run;
	otherwise it is the `store.HealthSummary` fields plus the computed
	`success_rate` (null with no runs).

	`broken` is the flattened name -> reason map, so it also holds a
	file that never imported and therefore has no board entry. A board
	whose own `broken` is set appears in both places; that duplication
	is deliberate — `boards` is the inventory, `broken` is the repair
	list.
	"""
	boards = []
	for board in reg.all():
		boards.append(
			{
				'name': board.name,
				'title': board.title,
				'aliases': list(board.aliases),
				'origin': board.origin,
				'enabled': _is_enabled(board, disabled),
				'broken': board.broken,
				'homepage': board.homepage,
				'terms': board.terms,
				'health': _health_document(health.get(board.name)),
			}
		)
	return {'boards': boards, 'broken': dict(sorted(reg.broken.items()))}


def _health_document(summary: store.HealthSummary | None) -> dict | None:
	"""One board's health as JSON, or None when it has never run."""
	if summary is None:
		return None
	return {
		'runs': summary.runs,
		'failures': summary.failures,
		'streak': summary.streak,
		'success_rate': summary.success_rate,
		'last_run': summary.last_started_at,
		'last_ok': summary.last_ok,
		'last_error': summary.last_error,
		'last_duration_ms': summary.last_duration_ms,
		'last_fetched': summary.last_fetched,
		'fetched': summary.fetched,
		'dropped_invalid': summary.dropped_invalid,
	}


def _print_listing(reg: registry.Registry, disabled: set[str], health: dict) -> None:
	"""The aligned table, then one indented line per broken board or error.

	The error text gets its own line instead of a column: a board's last
	error is a sentence, and a wide column of sentences stops being a
	table. Broken and failing boards keep the plain markers the rest of
	the CLI prints, so the output is greppable and identical in a log.
	"""
	rows = []
	for board in reg.all():
		summary = health.get(board.name)
		rows.append(
			[
				board.name,
				', '.join(board.aliases) or '-',
				board.origin,
				'yes' if _is_enabled(board, disabled) else 'no',
				_when(summary),
				_rate(summary),
			]
		)
	for name in reg.broken:
		if name not in reg.boards:  # a file that never imported has no Board
			rows.append([name, '-', '-', 'no', '-', '-'])

	for line in _table(rows, ('NAME', 'ALIASES', 'ORIGIN', 'ENABLED', 'LAST RUN', 'RATE')):
		print(line)

	for board in reg.all():
		if board.broken:
			print(f'  broken: {board.name}: {board.broken}')
		summary = health.get(board.name)
		if summary is not None and summary.last_error:
			print(f'  last error: {board.name}: {summary.last_error}')
	for name, reason in reg.broken.items():
		if name not in reg.boards:
			print(f'  broken: {name}: {reason}')


def _table(rows: list[list[str]], headers: tuple[str, ...]) -> list[str]:
	"""Rows padded to one width per column, two spaces joined."""
	widths = [
		max([len(header)] + [len(row[index]) for row in rows]) for index, header in enumerate(headers)
	]
	lines = ['  '.join(header.ljust(widths[index]) for index, header in enumerate(headers))]
	for row in rows:
		lines.append('  '.join(cell.ljust(widths[index]) for index, cell in enumerate(row)))
	return [line.rstrip() for line in lines]


def _is_enabled(board: registry.Board, disabled: set[str]) -> bool:
	"""Can a run use this board: it is not broken and not disabled."""
	return not board.broken and board.name not in disabled


def _when(summary: store.HealthSummary | None) -> str:
	"""The last run's start, to the minute; '-' before the first run."""
	if summary is None or not summary.last_started_at:
		return '-'
	return summary.last_started_at[:16].replace('T', ' ')


def _rate(summary: store.HealthSummary | None) -> str:
	"""Successes over runs in the health window; '-' before the first."""
	if summary is None or not summary.runs:
		return '-'
	return f'{summary.runs - summary.failures}/{summary.runs}'


def _health(ws) -> dict:
	"""Board health, or none when there is no database or it cannot answer.

	Health is an accessory to the listing, exactly as it is to a scrape:
	a database from a newer build (or a corrupt file) warns once and
	the board inventory still prints. A workspace nobody has scraped
	into has no runs and no file; listing boards must not create one.
	"""
	if not ws.db.exists():
		return {}
	try:
		return store.board_health(ws.db)
	except Exception as error:  # noqa: BLE001 — a health read must not hide the boards
		print(f'warning: could not read board health: {error}', file=sys.stderr)
		return {}


# -- info ------------------------------------------------------------------


def run_info(args, ws) -> int:
	"""One board as prose plus the fields a plugin author needs."""
	try:
		settings = config.load(ws)
	except config.ConfigError as error:
		return _failure(error)
	reg = registry.load(ws, settings)
	_print_notices(reg)
	board = reg.by_alias(args.name)
	if board is None:
		reason = reg.broken.get(args.name)
		if reason is not None:  # a file that never imported: no Board to inspect
			print(f'error: {args.name} is broken: {reason}', file=sys.stderr)
			return 1
		return _unknown(args.name)

	if args.json:
		print_json(_info_document(board))
		return 1 if board.broken else 0

	print(f'{board.name} — {board.title} ({board.origin})')
	docstring = inspect.getdoc(board.module)
	if docstring:
		print()
		print(docstring)
	print()
	print(f'  aliases: {", ".join(board.aliases) or "-"}')
	print(f'  homepage: {board.homepage or "-"}')
	print(f'  terms: {board.terms or "-"}')
	print(f'  API version: {getattr(board.module, "API_VERSION", 1)}')
	print(f'  polite delay: {_seconds(getattr(board.module, "POLITE_DELAY", 0))}')
	if board.broken:
		print(f'  broken: {board.broken}')
	for line in _location_lines(board.module):
		print(f'  {line}')
	for line in _family_lines(board.module):
		print(f'  {line}')
	if callable(getattr(board.module, 'server_locations', None)):
		print('  server_locations: yes — the board builds its own request params')
	for line in _option_lines(board.module, board.name):
		print(f'  {line}')
	return 1 if board.broken else 0


def _info_document(board: registry.Board) -> dict:
	"""One board as JSON: the prose view's data, with the same meanings.

	The human command prints the docstring and a few aligned blocks; this
	is that information in a shape a skill can branch on. `locations`
	and `options` are null when the board declares none / has no readable
	signature, mirroring the prose fallbacks rather than pretending an
	empty table is data.

	`polite_delay` is a number (seconds) where the prose prints '0s';
	`api_version` is the contract number the registry checked.
	"""
	module = board.module
	return {
		'name': board.name,
		'title': board.title,
		'origin': board.origin,
		'aliases': list(board.aliases),
		'broken': board.broken,
		'homepage': board.homepage,
		'terms': board.terms,
		'api_version': getattr(module, 'API_VERSION', 1),
		'polite_delay': _polite_delay(module),
		'docstring': inspect.getdoc(module),
		'locations': _location_document(module),
		'families': _family_document(module),
		'server_locations': callable(getattr(module, 'server_locations', None)),
		'options': _option_entries(module, board.name),
	}


def _polite_delay(module) -> float | None:
	"""POLITE_DELAY as seconds, or None for a value that is not a number."""
	try:
		return float(getattr(module, 'POLITE_DELAY', 0))
	except (TypeError, ValueError):
		return None


def _location_document(module) -> dict | None:
	"""The declared location vocabulary, structured; None when none.

	Regions map to the number of tags they carry — the full tag tuples
	are in the plugin file, and `sources info` exists to say the shape of
	the vocabulary, not to reprint 60 words. `country_aliases` keeps the
	board's own wordings because they are what a filter author needs.
	"""
	declared = any(
		hasattr(module, name) for name in ('WORLDWIDE', 'REGIONS', 'COUNTRY_STYLE', 'COUNTRY_ALIASES')
	)
	if not declared:
		return None
	regions = getattr(module, 'REGIONS', {}) or {}
	return {
		'worldwide': _values(getattr(module, 'WORLDWIDE', ())),
		'regions': {token: len(tags) for token, tags in regions.items()},
		'country_style': getattr(module, 'COUNTRY_STYLE', 'none'),
		'country_aliases': dict(getattr(module, 'COUNTRY_ALIASES', {}) or {}),
		'covers': _values(getattr(module, 'COVERS', ())),
	}


def _family_document(module) -> dict | None:
	"""FAMILIES as family -> params; None when the board has no table.

	`FAMILIES = {}` is a declaration — the board takes no category
	parameter and serves everything — so it stays an empty object rather
	than collapsing into the same null as a missing attribute.
	"""
	families = getattr(module, 'FAMILIES', None)
	if families is None:
		return None
	return {family: dict(params or {}) for family, params in families.items()}


def _option_entries(module, name: str) -> list[dict] | None:
	"""`recent_jobs` parameters as rows, or None with no readable signature.

	One walk feeds the prose and the JSON: `_option_lines` prints each
	row's `label` and `note`, and `--json` serialises the same rows. That
	way the two views cannot describe different signatures.

	`default` keeps its JSON type when it has one and falls back to repr
	for an exotic object, so a plugin with a tuple default still produces
	valid JSON. `supplied_by_run` marks the two parameters the registry
	fills itself, which is the distinction a plugin author most often
	needs when reading `[sources.NAME]` options.
	"""
	recent = getattr(module, 'recent_jobs', None)
	if not callable(recent):
		return None
	try:
		signature = inspect.signature(recent)
	except (TypeError, ValueError):
		return None

	section = f'[sources.{name}]'
	entries = []
	for parameter in signature.parameters.values():
		entry = {
			'name': parameter.name,
			'kind': parameter.kind.name.lower(),
			'label': parameter.name,
			'required': parameter.default is inspect.Parameter.empty,
			'supplied_by_run': False,
		}
		if parameter.kind is inspect.Parameter.VAR_KEYWORD:
			entry['label'] = f'**{parameter.name}'
			entry['required'] = False
			entry['note'] = f'{section} keys arrive here'
		elif parameter.kind is inspect.Parameter.VAR_POSITIONAL:
			entry['label'] = f'*{parameter.name}'
			entry['required'] = False
			entry['note'] = 'positional; the registry passes none'
		else:
			entry['supplied_by_run'] = parameter.name in ('locations', 'limit') and not entry['required']
			if entry['required']:
				entry['note'] = f'required — {section} must set it'
			elif entry['supplied_by_run']:
				entry['note'] = f'default {parameter.default!r}; the run supplies it'
			else:
				entry['note'] = f'default {parameter.default!r}'
			if not entry['required']:
				entry['default'] = _json_default(parameter.default)
		entries.append(entry)
	return entries


def _json_default(value):
	"""A signature default that json can carry, or its repr."""
	if value is None or isinstance(value, (str, int, float, bool)):
		return value
	return repr(value)


def _location_lines(module) -> list[str]:
	"""The declared location vocabulary, summarised, one line per part.

	The full mapping can be 60 country wordings long (wellfound), which
	is a file to read, not a line to print; the summary says the shape
	and gives examples, and `sources info` is for orientation.
	"""
	declared = any(
		hasattr(module, name) for name in ('WORLDWIDE', 'REGIONS', 'COUNTRY_STYLE', 'COUNTRY_ALIASES')
	)
	if not declared:
		return [
			'locations: none declared — the board tags stay as served and the central filter narrows'
		]

	worldwide = _values(getattr(module, 'WORLDWIDE', ()))
	lines = [f'locations worldwide: {", ".join(worldwide) or "-"}']
	regions = getattr(module, 'REGIONS', {}) or {}
	lines.append(
		'locations regions: '
		+ (', '.join(f'{token} ({len(tags)} tags)' for token, tags in regions.items()) or '-')
	)
	style = getattr(module, 'COUNTRY_STYLE', 'none')
	if style == 'none':
		lines.append("locations countries: none — COUNTRY_STYLE is 'none'")
	else:
		worded = [
			wording for wording in (getattr(module, 'COUNTRY_ALIASES', {}) or {}).values() if wording
		]
		base = 'display names' if style == 'name' else 'ISO-2 codes'
		detail = (
			f'; {len(worded)} worded by the board, e.g. {", ".join(map(repr, worded[:3]))}'
			if worded
			else ''
		)
		lines.append(f'locations countries: {base}{detail}')
	covers = _values(getattr(module, 'COVERS', ()))
	lines.append(f'locations covers: {", ".join(covers) or "everything"}')
	return lines


def _family_lines(module) -> list[str]:
	"""The `FAMILIES` table, one line per family with the params it sends."""
	families = getattr(module, 'FAMILIES', None)
	if not families:
		return ['families: none declared — the board takes no category parameter']
	return [f'families: {family} → {_params(params)}' for family, params in families.items()]


def _option_lines(module, name: str) -> list[str]:
	"""`recent_jobs` parameters and defaults, straight from its signature.

	This is the answer to "what may I put in `[sources.NAME]`?" without
	a second documentation site: the keyword-only parameters with their
	defaults, `locations`/`limit` marked as the run's to supply, and
	`**options` named as where a config table's extra keys land. The
	signature is the only source of truth, so the list cannot go stale
	against the code; its rows are `_option_entries`'s, the same ones
	`--json` serialises.
	"""
	entries = _option_entries(module, name)
	if entries is None:
		recent = getattr(module, 'recent_jobs', None)
		if not callable(recent):
			return ['options: recent_jobs is missing, so there is no signature to read']
		return ['options: recent_jobs has no readable signature']
	section = f'[sources.{name}]'
	lines = [f'options (from recent_jobs; {section} is where you set them):']
	width = max((len(entry['label']) for entry in entries), default=0)
	for entry in entries:
		lines.append(f'  {entry["label"].ljust(width)}  {entry["note"]}')
	return lines


# -- new -------------------------------------------------------------------


def run_new(args, ws) -> int:
	"""Copy the shipped template to `<op>/scrapers/NAME.py` with NAME filled.

	The template is the documentation: a working JSON-API board with
	every optional hook spelled and the research questions in comments.
	The command's only job is the copy and the NAME line, so what lands
	is exactly what the package ships.
	"""
	problem = _name_problem(args.name)
	if problem is not None:
		print(f'error: {problem}', file=sys.stderr)
		return 2

	reg = registry.load(ws, config.Config())
	existing = reg.by_alias(args.name)
	if existing is not None and existing.name != args.name:
		print(
			f'error: {args.name!r} is an alias of {existing.name!r}; '
			"a board NAME cannot be another board's alias",
			file=sys.stderr,
		)
		return 2

	template = workspace.assets_dir() / 'scrapers' / '_example.py'
	if not template.is_file():
		print(f'error: the template is missing: {template}', file=sys.stderr)
		return 1
	source = template.read_text(encoding='utf-8')
	if _NAME_LINE.search(source) is None:
		print(f'error: {template} has no NAME line to fill in', file=sys.stderr)
		return 1

	target = ws.scrapers / f'{args.name}.py'
	if target.exists():
		print(
			f'error: {target.relative_to(ws.root)} already exists — edit it, or choose another NAME',
			file=sys.stderr,
		)
		return 1
	if existing is not None and existing.origin == registry.ORIGIN_WORKSPACE:
		# A different file already declares this NAME. Copying the
		# template under a second stem would leave two definitions and a
		# load-order override nobody asked for.
		where = Path(getattr(existing.module, '__file__', '?'))
		print(
			f'error: the workspace already defines {args.name!r} in {where.name}; '
			'edit that file, or choose another NAME',
			file=sys.stderr,
		)
		return 1

	if existing is not None:
		print(f'note: {args.name} is already a {existing.origin} board; this file overrides it')
	target.parent.mkdir(parents=True, exist_ok=True)
	target.write_text(_NAME_LINE.sub(f'NAME = {args.name!r}', source, count=1), encoding='utf-8')
	print(f'created {target.relative_to(ws.root)}')
	print(f'next: point API_URL and parse() at the board, then `jobtriage sources test {args.name}`')
	return 0


def _name_problem(name: str) -> str | None:
	"""Why this NAME cannot be a new board, or None when it can."""
	if name in RESERVED:
		return f"{name!r} is the CLI's meta-board name (scrape -s all); choose another NAME"
	if registry.NAME_PATTERN.fullmatch(name) is None:
		return f'NAME {name!r} must match [a-z0-9-]+ (lowercase letters, digits, hyphens)'
	return None


# -- test ------------------------------------------------------------------


def run_test(args, ws) -> int:
	"""Run one board (or every enabled board) and report what came back."""
	if args.all and args.name:
		print('error: give a NAME or --all, not both', file=sys.stderr)
		return 2
	if not args.all and not args.name:
		print('error: sources test needs a NAME, or --all for every enabled board', file=sys.stderr)
		return 2

	try:
		settings = config.load(ws)
	except config.ConfigError as error:
		return _failure(error)
	reg = registry.load(ws, settings)
	_print_notices(reg)

	if args.all:
		return _test_all(reg, settings, json_output=args.json)

	board = reg.by_alias(args.name)
	if board is None:
		reason = reg.broken.get(args.name)
		if reason is not None:  # a file that never imported: no Board to run
			print(f'error: {args.name} is broken: {reason}', file=sys.stderr)
			return 1
		return _unknown(args.name)
	if board.broken:
		# `registry.run` would raise BrokenBoard; the command's own line
		# names the board and the same reason, without the class name.
		print(f'error: {board.name} is broken: {board.broken}', file=sys.stderr)
		return 1
	if board.name in reg.disabled(settings):
		# An explicit request is the choice, exactly as `scrape -s NAME`
		# runs a disabled board; saying so keeps the absence from the
		# `--all` run from looking like a bug.
		print(
			f'note: {board.name} is disabled in [sources].disabled; testing it anyway', file=sys.stderr
		)
	if args.json:
		row = _test_row(reg, board)
		print_json({'boards': [row], 'broken': {}, 'ok': row['ok']})
		return 0 if row['ok'] else 1
	return 0 if _test_board(reg, board) else 1


def _test_all(reg: registry.Registry, settings: config.Config, *, json_output: bool = False) -> int:
	"""Every enabled board, one block each; broken ones named first.

	`--json` is the same run as one document: a `boards` row per board
	with the numbers and the first contract problems, the `broken` map,
	and `ok`. The nightly canary reads it, which is why the exit code
	and `ok` always agree.
	"""
	rows = []
	broken = dict(reg.broken)
	boards = reg.enabled(settings)
	if not json_output:
		for name, reason in broken.items():
			print(f'broken: {name}: {reason}', file=sys.stderr)
	if not boards:
		print('error: no enabled boards to test', file=sys.stderr)
		return 1
	failed = len(broken)
	for board in boards:
		if json_output:
			row = _test_row(reg, board)
			rows.append(row)
			if not row['ok']:
				failed += 1
		elif not _test_board(reg, board):
			failed += 1
	if json_output:
		print_json({'boards': rows, 'broken': broken, 'ok': failed == 0})
	return 0 if failed == 0 else 1


def _test_board(reg: registry.Registry, board: registry.Board) -> bool:
	"""One real run, its numbers, its jobs and every contract rejection.

	The registry's `run` is the contract boundary, so this is exactly
	what a scrape would have accepted from the board — same limit
	truncation, same validation, same one-line error. Nothing is
	recorded: a test is not a scrape, and board health should not move
	because someone checked a plugin.
	"""
	jobs, stats = reg.run(board.name, limit=TEST_LIMIT)
	if stats.error:
		print(
			f'error: {board.name} failed after {stats.duration:.2f}s: {stats.error}',
			file=sys.stderr,
		)
		return False

	print(
		f'{board.name} ({board.origin}) · {stats.fetched} fetched · {stats.valid} valid · '
		f'{stats.dropped_invalid} dropped · {stats.duration:.2f}s'
	)
	for job in jobs:
		print(f'  {_job_line(job)}')
	for problem in stats.problems:
		print(f'  {problem}')
	if stats.dropped_invalid:
		print(
			f'  the contract rejected {stats.dropped_invalid} row(s); '
			'the registry never writes them, a scrape only warns',
			file=sys.stderr,
		)
	return stats.dropped_invalid == 0


def _test_row(reg: registry.Registry, board: registry.Board) -> dict:
	"""One board's `--json` result: the same run `_test_board` prints.

	`ok` is the command's verdict — the board returned, and every row
	it returned was valid. `error` carries the board's own exception (a
	site that changed shape, a rate limit), `problems` the validator's
	first complaints, so the canary's issue body can say what broke
	without a second diagnostic command.
	"""
	jobs, stats = reg.run(board.name, limit=TEST_LIMIT)
	ok = not stats.error and stats.dropped_invalid == 0
	return {
		'name': board.name,
		'origin': board.origin,
		'ok': ok,
		'fetched': stats.fetched,
		'valid': stats.valid,
		'dropped': stats.dropped_invalid,
		'duration_ms': round(stats.duration * 1000),
		'error': stats.error or None,
		'problems': list(stats.problems),
		'jobs': [_job_url(job) for job in jobs],
	}


def _job_url(job: dict) -> str:
	"""The one job field the canary may want in an issue: the link."""
	return job['url']


def _job_line(job: dict) -> str:
	"""One job on one line: title — company · date · tags · url."""
	parts = [
		f'{job["title"]} — {job["company"]}',
		(job.get('published') or 'undated')[:10],
		', '.join(job.get('locations') or []),
		job['url'],
	]
	return ' · '.join(part for part in parts if part)


# -- enable / disable ------------------------------------------------------


def run_enable(args, ws) -> int:
	"""Switch a board on by removing it from `[sources].disabled`."""
	return _set_disabled(args.name, ws, on=True)


def run_disable(args, ws) -> int:
	"""Switch a board off by adding it to `[sources].disabled`."""
	return _set_disabled(args.name, ws, on=False)


def _set_disabled(name: str, ws, on: bool) -> int:
	"""The shared body: canonicalise, decide whether anything changes, write.

	An alias is resolved through the registry before the file is
	touched: the disabled list stores canonical NAMEs, so `disable rm`
	and `disable remotive` are the same edit and a later board rename
	does not leave a second spelling behind. Enabling also removes any
	alias spellings of the same board — a list written by hand before
	this command existed may still carry them.

	An `enable` for a name no board answers to is allowed when it is
	already in the list: that stale entry is exactly what the user is
	trying to remove. A `disable` of an unknown name is a usage error.
	"""
	try:
		settings = config.load(ws)
	except config.ConfigError as error:
		return _failure(error)
	reg = registry.load(ws, settings)
	_print_notices(reg)
	current = list(settings.sources.disabled)

	board = reg.by_alias(name)
	if board is None:
		if on and name in current:
			updated = [entry for entry in current if entry != name]
			print(f'note: {name!r} names no board; removing the stale entry')
			written = _write_disabled(ws, updated)
			if written == 0:
				_warn_unknown_disabled(reg, updated)
			return written
		return _unknown(name)

	spellings = {board.name, *board.aliases}
	if name != board.name:
		print(f'note: {name} is an alias of {board.name}')
	if on:
		updated = [entry for entry in current if entry not in spellings]
		if len(updated) == len(current):
			print(f'{board.name}: already enabled')
			return 0
	else:
		if board.name in current:
			print(f'{board.name}: already disabled')
			return 0
		# An alias spelling already in the list still switches the board
		# off; the write canonicalises it rather than adding a duplicate.
		updated = [entry for entry in current if entry not in spellings]
		updated.append(board.name)
	written = _write_disabled(ws, updated)
	if written == 0:
		_warn_unknown_disabled(reg, updated)
		# Turning a board on is the moment its terms matter: euremotejobs
		# ships off precisely because enabling it accepts them, and
		# `sources info` is a second command a person may not think to run.
		if on and board.terms:
			print(f'terms: {board.terms}')
	return written


def _write_disabled(ws, values: list[str]) -> int:
	"""Write `sources.disabled` through tomlkit, then validate the file.

	The array is emptied and refilled in place rather than replaced, so
	the assignment and any inline comment stay exactly where the user
	left them — `disabled = []  # boards I muted` survives the edit.
	The whole config is loaded after the write and the original text is
	restored if this build cannot honour the result, the same rule
	`config set` follows.
	"""
	path = ws.config
	original = path.read_text(encoding='utf-8')
	try:
		document = tomlkit.parse(original)
	except tomlkit_errors.ParseError as error:
		return _failure(f'{path}: {error}')

	sources = document.get('sources')
	if sources is None:
		sources = tomlkit.table()
		document['sources'] = sources
	if not isinstance(sources, dict):
		return _failure(f'{path}: sources must be a table, not {type(sources).__name__}')
	array = sources.get('disabled')
	if array is None:
		array = tomlkit.array()
		sources['disabled'] = array
	if not isinstance(array, list):
		return _failure(f'{path}: sources.disabled must be an array')

	array.clear()
	array.extend(values)
	path.write_text(tomlkit.dumps(document), encoding='utf-8')
	try:
		config.load(ws, env={})
	except config.ConfigError as error:
		path.write_text(original, encoding='utf-8')
		return _failure(error)

	print(f'sources.disabled = {_list_display(values)}')
	return 0


def _list_display(values: list[str]) -> str:
	"""A TOML array of names, the form `config set` prints."""
	return '[' + ', '.join(json.dumps(value) for value in values) + ']'


# -- shared ----------------------------------------------------------------


def _print_notices(reg: registry.Registry) -> None:
	"""Override notices on stderr, once, like the pipeline's `_load`."""
	for notice in reg.notices:
		print(f'scraper: {notice}', file=sys.stderr)


def _warn_unknown_disabled(reg: registry.Registry, names) -> None:
	"""Say when `[sources].disabled` names a board that no longer exists."""
	for name in names:
		if reg.by_alias(name) is None:
			print(
				f'warning: sources.disabled lists {name!r}, which is not a known board; '
				f'`jobtriage sources enable {name}` removes it',
				file=sys.stderr,
			)


def _values(declared) -> list[str]:
	"""A tuple/string declaration as a list of strings for display."""
	if not declared:
		return []
	if isinstance(declared, str):
		return [declared]
	return [str(value) for value in declared]


def _params(params) -> str:
	"""A board's family parameters, `key='value'` shaped and readable."""
	if not params:
		return 'no parameters'
	return ', '.join(f'{key}={value!r}' for key, value in params.items())


def _seconds(value) -> str:
	"""A POLITE_DELAY as seconds, with 0 kept short."""
	try:
		return f'{float(value):g}s'
	except (TypeError, ValueError):
		return f'{value!r}'


def _unknown(name: str) -> int:
	"""An unknown board is a usage error: exit 2, the scrape convention."""
	print(
		f'error: unknown board {name!r}; run `jobtriage sources` to see the boards',
		file=sys.stderr,
	)
	return 2


def _failure(message: object) -> int:
	print(f'error: {message}', file=sys.stderr)
	return 1
