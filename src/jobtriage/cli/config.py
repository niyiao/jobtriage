"""`jobtriage config` — look at, edit and validate the op folder's
jobtriage.toml.

The commands are deliberately different sizes of tool:

* `show` reads the resolved config through `config.load_with_provenance`
  and prints every value with the layer that set it — code default,
  preset, jobtriage.toml, `JOBTRIAGE_*` environment or a CLI flag. That
  provenance is the answer to "why is the limit 20?" without reading
  the loader; `--json` is the same document (config sections plus a
  `provenance` map) for a script or an agent.
* `get` is the one-value version of `show`, and it reads the *effective*
  value: `jobtriage config get search.limit` answers with the limit the
  next scrape will use, not the number in the file, which an environment
  variable or a flag may have overridden.
* `set` edits the file itself, through tomlkit, so the user's comments
  and layout survive. The value is parsed as a TOML value first and
  falls back to a bare string, which is what makes both
  `set search.locations '["emea"]'` and `set filter.preset any` work.
  The whole config is loaded after the write; a value this build cannot
  honour is rolled back rather than left in the file.
* `edit` opens the file in `$EDITOR` (falling back to `vi`, which POSIX
  guarantees) and validates it afterwards, because the one thing more
  likely than a typo is an editor that autosaves halfway through one.
* `validate` is the exhaustive check: the same loader every command
  uses, with nothing else happening, for scripts and for the moment
  after a hand edit.

Every command turns a ConfigError into `error: ...` and exit 1, the
convention `scrape` already follows; argparse's own misuse (a missing
KEY) stays exit 2.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import tomllib
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path

import tomlkit
from tomlkit import exceptions as tomlkit_errors

from jobtriage import config, workspace
from jobtriage.cli.output import print_json

# The `sources` fields that are real dataclass fields; every other name
# under `sources.` is a board and lives in `sources.overrides`.
_SOURCES_FIELDS = ('disabled', 'overrides')


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser(
		'config',
		help='the op folder config: show, edit, get, set, validate',
	)
	actions = parser.add_subparsers(dest='config_command', metavar='ACTION', required=True)

	show = actions.add_parser(
		'show',
		help='print the effective config and the layer each value came from',
	)
	show.add_argument(
		'--json',
		action='store_true',
		help='print one JSON document: the config sections plus a dotted-key provenance map',
	)
	show.set_defaults(run=run_show)

	edit = actions.add_parser(
		'edit',
		help=f'open {workspace.CONFIG_NAME} in $EDITOR (or vi) and validate it afterwards',
	)
	edit.set_defaults(run=run_edit)

	get = actions.add_parser(
		'get',
		help='print one effective value: the loaded config, not just the file',
	)
	get.add_argument(
		'key',
		metavar='KEY',
		help='dotted key, like search.limit or sources.wellfound.max_pages',
	)
	get.add_argument(
		'--json',
		action='store_true',
		help='print the value as JSON (a string stays a JSON string) for scripts',
	)
	get.set_defaults(run=run_get)

	set_ = actions.add_parser(
		'set',
		help='set one file value, keeping comments (TOML value, else a bare string)',
	)
	set_.add_argument(
		'key',
		metavar='KEY',
		help='dotted key, like search.locations or sources.wellfound.max_pages',
	)
	set_.add_argument(
		'value',
		metavar='VALUE',
		help='TOML value (["emea"]) or a bare string (any); quote nested quotes for the shell',
	)
	set_.set_defaults(run=run_set)

	validate = actions.add_parser(
		'validate',
		help='load the config and print ok, or the first error',
	)
	validate.set_defaults(run=run_validate)
	return parser


def run_show(args, ws) -> int:
	"""The effective config, human table or one JSON document."""
	try:
		settings, origins = config.load_with_provenance(ws)
	except config.ConfigError as error:
		return _failure(error)

	if args.json:
		document = asdict(settings)
		# Sections stay at the top level so a reader can ask for
		# `document['search']['limit']` as it would from `asdict`;
		# provenance is one flat map beside them, which is impossible
		# to confuse with a section name the loader accepts.
		document['provenance'] = dict(sorted(origins.items()))
		print_json(document)
		return 0

	_print_human(settings, origins)
	return 0


def run_get(args, ws) -> int:
	"""One effective value, from the fully merged config."""
	try:
		settings, _origins = config.load_with_provenance(ws)
	except config.ConfigError as error:
		return _failure(error)
	try:
		value = _effective(settings, args.key)
	except KeyError as error:
		return _failure(error.args[0] if error.args else args.key)
	if args.json:
		# A bare JSON value: `get` is one value, and quoting a string is the
		# whole difference an agent needs. A section is a dataclass, so it
		# is converted the way `show --json` converts the whole config.
		print_json(asdict(value) if is_dataclass(value) else value)
		return 0
	print(_display(value, bare=True))
	return 0


def run_set(args, ws) -> int:
	"""Write one value into the file, then validate the whole config.

	The original text is kept in memory until the loader accepts the
	result: a write that produces a config this build cannot honour is
	undone, so `set` never leaves the op folder less usable than it
	found it. That is also why validation reads the file with `env={}`:
	the command is about whether the file is valid, not whether the
	caller's shell happens to have a broken `JOBTRIAGE_*` variable.
	"""
	path = ws.config
	existed = path.is_file()
	original = path.read_text(encoding='utf-8') if existed else ''
	try:
		document = tomlkit.parse(original)
	except tomlkit_errors.ParseError as error:
		return _failure(f'{path}: {error}')

	parts = args.key.split('.')
	if len(parts) < 2 or not all(parts):
		return _failure(f'{args.key}: a key is section.name, like search.limit')

	value = _parse_value(args.value)
	try:
		_assign(document, parts, value)
	except config.ConfigError as error:
		return _failure(error)

	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(tomlkit.dumps(document), encoding='utf-8')
	try:
		config.load(ws, env={})
	except config.ConfigError as error:
		if existed:
			path.write_text(original, encoding='utf-8')
		else:
			path.unlink(missing_ok=True)
		return _failure(error)

	print(f'{args.key} = {_display(value)}')
	return 0


def run_edit(args, ws) -> int:
	"""Hand the file to `$EDITOR`, then validate it.

	A workspace normally cannot resolve without jobtriage.toml, but
	`Workspace` can be built directly (and a future `init` path may ask
	for edits before the marker exists); an absent file is created from
	the shipped template so the editor opens a documented file rather
	than an empty one.
	"""
	path = ws.config
	if not path.is_file():
		_create_from_template(path)

	editor = os.environ.get('EDITOR') or 'vi'
	try:
		command = shlex.split(editor) or ['vi']
	except ValueError as error:
		return _failure(f'$EDITOR is not a command: {error}')
	try:
		done = subprocess.run([*command, str(path)], check=False)
	except FileNotFoundError:
		return _failure(f'editor {command[0]!r} not found — set $EDITOR to your editor')
	if done.returncode != 0:
		return _failure(f'{command[0]} exited with status {done.returncode}; config not checked')

	try:
		config.load(ws)
	except config.ConfigError as error:
		return _failure(error)
	print('ok')
	return 0


def run_validate(args, ws) -> int:
	"""The whole config, checked exactly the way every command reads it."""
	try:
		config.load(ws)
	except config.ConfigError as error:
		return _failure(error)
	print('ok')
	return 0


def _failure(message: object) -> int:
	print(f'error: {message}', file=sys.stderr)
	return 1


def _print_human(settings: config.Config, origins: dict[str, str]) -> None:
	"""A TOML-ish table: sections, assignments, and a provenance comment.

	Leaves are grouped by their table first and emitted in the order
	the tables first appear. That is what keeps a section's own keys
	from landing after its sub-table (`[tui] page_size` after
	`[tui.keys]` would read as a `tui.keys` key in real TOML).
	"""
	print('# The effective config — code default < preset < jobtriage.toml < JOBTRIAGE_* < flags.')
	print('# Each value ends with the layer that set it.')
	groups: dict[str, list[tuple[str, object, str]]] = {}
	for key, value in _leaves(settings):
		parent, _, name = key.rpartition('.')
		groups.setdefault(parent, []).append((name, value, origins.get(key, 'default')))
	for parent, entries in groups.items():
		print()
		print(f'[{parent}]')
		for name, value, source in entries:
			print(f'{name} = {_display(value)}  # {source}')


def _leaves(value: object, prefix: str = ''):
	"""Every non-table value as (dotted key, value), in field order.

	Dataclass field order and dict insertion order are stable, which
	keeps `show`'s output diffable between runs; tuples are leaves,
	not tables, so a location list prints on one line. The one table
	whose dotted spelling differs from its dataclass field is
	`sources.overrides`: the user writes `[sources.wellfound]`, so the
	board's name replaces the field's in the key.
	"""
	if is_dataclass(value):
		for entry in fields(value):
			item = getattr(value, entry.name)
			if isinstance(value, config.SourcesSettings) and entry.name == 'overrides':
				for board, table in item.items():
					yield from _leaves(table, f'{prefix}{board}.')
				continue
			yield from _leaves(item, f'{prefix}{entry.name}.')
	elif isinstance(value, dict):
		for key, item in value.items():
			yield from _leaves(item, f'{prefix}{key}.')
	else:
		yield prefix[:-1], value


def _effective(settings: config.Config, key: str) -> object:
	"""The merged value at a dotted key; KeyError with a sentence."""
	parts = key.split('.')
	if not all(parts):
		raise KeyError(f'{key}: a key is section.name, like search.limit')

	if parts[0] == 'sources' and len(parts) > 1 and parts[1] not in _SOURCES_FIELDS:
		value: object = settings.sources.overrides
		for part in parts[1:]:
			if not isinstance(value, dict) or part not in value:
				raise KeyError(f'{key}: no such per-board config value')
			value = value[part]
		return value

	value = settings
	for part in parts:
		if is_dataclass(value):
			if part not in {entry.name for entry in fields(value)}:
				raise KeyError(f'{key}: no such config key')
			value = getattr(value, part)
		elif isinstance(value, dict):
			if part not in value:
				raise KeyError(f'{key}: no such config key')
			value = value[part]
		else:
			raise KeyError(f'{key}: no such config key')
	return value


def _parse_value(text: str) -> object:
	"""A TOML value if it is one, else the text as a bare string.

	`["emea"]` and `50` are TOML; `any` is not, and treating it as a
	string is what lets `set filter.preset any` work without quoting.
	A value that must stay a string but looks like TOML ("50") can be
	quoted by the user (`'"50"'`), which is the same escape the shell
	already teaches.
	"""
	try:
		return tomllib.loads(f'value = {text}')['value']
	except tomllib.TOMLDecodeError:
		return text


def _assign(document, parts: list[str], value: object) -> None:
	"""Set `parts` in a tomlkit document, creating missing tables.

	A plain value where the path needs a table is a config mistake
	(`sources = 8` then `sources.wellfound.max_pages`); say so rather
	than replacing the value the user wrote.
	"""
	table = document
	for part in parts[:-1]:
		if part not in table:
			table[part] = tomlkit.table()
		child = table[part]
		if not isinstance(child, dict):
			raise config.ConfigError(f'{part}: expected a table, got {type(child).__name__}')
		table = child
	table[parts[-1]] = value


def _display(value: object, *, bare: bool = False) -> str:
	"""TOML-ish output; `bare` leaves a top-level string unquoted for `get`.

	Lists always quote their strings: `get search.locations` should
	print something a user can paste back into the file, so only the
	value the command is about loses its quotes.
	"""
	if isinstance(value, str):
		return value if bare else json.dumps(value, ensure_ascii=False)
	if is_dataclass(value):
		return _display(asdict(value))
	if value is None:
		return '""'
	if isinstance(value, bool):
		return 'true' if value else 'false'
	if isinstance(value, (int, float)):
		return repr(value)
	if isinstance(value, (list, tuple)):
		return '[' + ', '.join(_display(item) for item in value) + ']'
	if isinstance(value, dict):
		inner = ', '.join(f'{key} = {_display(item)}' for key, item in value.items())
		return '{ ' + inner + ' }' if inner else '{}'
	return str(value)


def _create_from_template(path: Path) -> None:
	"""Copy the shipped jobtriage.toml template, comments and all."""
	template = workspace.assets_dir() / workspace.CONFIG_NAME
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text(template.read_text(encoding='utf-8'), encoding='utf-8')
