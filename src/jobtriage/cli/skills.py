"""`jobtriage skills` — the shipped agent skills: list them, install them
in an agent's user-level folder.

The skills themselves live in `<op>/skills/`, one directory per skill
with a `SKILL.md`; `jobtriage init` copies them from the package and
`--upgrade` refreshes the untouched ones. This command is the two things
an agent or a person cannot do with the folder alone:

* `list` says which skills ship, which are installed here, and which
  agents already have a copy in their user-level skills folder.
* `install --global` copies the workspace's skills into the user-level
  folders, for people who run their agent outside the op folder. It is
  explicit and never part of `init`: a global folder belongs to the
  agent installation, not to this workspace.

The three folder conventions are the tools' own, not platformdirs
guesses. Claude Code reads `~/.claude/skills`; Codex reads
`~/.codex/skills`; opencode reads the XDG config directory
(`$XDG_CONFIG_HOME/opencode/skills`, default `~/.config/opencode/skills`)
on every platform. platformdirs would answer
`~/Library/Application Support` on macOS, which opencode never looks at,
so this module spells the conventions out and points at them in the
help text.
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

from jobtriage import workspace
from jobtriage.cli.output import print_json

# The agents the plan names. `all` is the default because a person
# installing for "their agent" usually does not know which folder name
# the tool uses, and one extra copy costs a few files.
AGENTS = ('claude', 'opencode', 'codex')


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser(
		'skills',
		help='the agent skills: list them and install them for an agent',
	)
	parser.set_defaults(run=run_list)
	actions = parser.add_subparsers(dest='skills_command', metavar='ACTION')

	listing = actions.add_parser(
		'list',
		help='show the shipped, workspace and globally installed skills',
	)
	listing.add_argument(
		'--json',
		action='store_true',
		help='print the listing as one JSON document',
	)
	listing.set_defaults(run=run_list)

	install = actions.add_parser(
		'install',
		help='copy the workspace skills into an agent user-level folder (--global)',
	)
	install.add_argument(
		'--global',
		dest='global_',
		action='store_true',
		help='install into the user-level folder, not the workspace',
	)
	install.add_argument(
		'--agent',
		choices=(*AGENTS, 'all'),
		default='all',
		help='which agent folder (default: all): claude ~/.claude/skills, '
		'opencode $XDG_CONFIG_HOME/opencode/skills, codex ~/.codex/skills',
	)
	install.set_defaults(run=run_install)
	return parser


def run_list(args, ws) -> int:
	"""Every skill that ships, that is installed here, and that each
	agent already has globally."""
	document = listing(ws)
	if getattr(args, 'json', False):
		print_json(document)
		return 0

	_print_group('shipped', document['shipped'])
	_print_group(f'installed in {ws.skills}', document['installed'])
	for agent in AGENTS:
		folder = global_dir(agent)
		items = document['global'][agent]
		state = f'{len(items)} skill(s)' if items else 'not installed'
		_print_group(f'{agent}  {folder}  ({state})', items)
	return 0


def listing(ws, *, home: Path | None = None, xdg: Path | None = None) -> dict:
	"""The `list` document: three groups of skill records.

	`path` is the skill directory, not the `SKILL.md`, because that is
	what an installer copies and what a person opens. The order is
	alphabetical, which is stable and groups the umbrella with the
	rest.
	"""
	global_items = {agent: _records(global_dir(agent, home=home, xdg=xdg)) for agent in AGENTS}
	return {
		'shipped': _records(workspace.assets_dir() / 'skills'),
		'installed': _records(ws.skills),
		'global': global_items,
	}


def run_install(args, ws) -> int:
	if not args.global_:
		print('error: install copies into a user-level agent folder; add --global', file=sys.stderr)
		return 2
	agents = list(AGENTS) if args.agent == 'all' else [args.agent]
	results = install(ws, agents)
	if not results:
		print(f'error: no skills to install in {ws.skills}', file=sys.stderr)
		return 1
	for agent, target, count in results:
		print(f'{agent}: {count} skill(s) -> {target}')
	return 0


def install(
	ws,
	agents: list[str],
	*,
	home: Path | None = None,
	xdg: Path | None = None,
) -> list[tuple[str, Path, int]]:
	"""Copy each workspace skill into each agent's user-level folder.

	A merge rather than a replace: files of the same name are
	overwritten, files the destination has and the source does not
	are left alone. That keeps a user's extra skill in the same folder
	and still makes the shipped copy current. Returns one row per
	agent, or nothing when the workspace has no skills to install.
	"""
	skills = _skill_dirs(ws.skills)
	if not skills:
		return []
	results = []
	for agent in agents:
		target = global_dir(agent, home=home, xdg=xdg)
		target.mkdir(parents=True, exist_ok=True)
		for source in skills:
			shutil.copytree(source, target / source.name, dirs_exist_ok=True)
		results.append((agent, target, len(skills)))
	return results


def global_dir(agent: str, *, home: Path | None = None, xdg: Path | None = None) -> Path:
	"""One agent's user-level skills folder, by that agent's convention.

	`home` and `xdg` exist for tests; the command uses the real ones.
	opencode is XDG on every platform (including macOS), which is why
	this is not `platformdirs.user_config_path`.
	"""
	home = Path.home() if home is None else home
	if agent == 'claude':
		return home / '.claude' / 'skills'
	if agent == 'codex':
		return home / '.codex' / 'skills'
	if agent == 'opencode':
		if xdg is None:
			xdg = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
		return Path(xdg) / 'opencode' / 'skills'
	raise KeyError(f'unknown agent {agent!r}')


def _skill_dirs(root: Path) -> list[Path]:
	"""Every directory directly under `root` that has a `SKILL.md`."""
	if not root.is_dir():
		return []
	return sorted(
		(path for path in root.iterdir() if path.is_dir() and (path / 'SKILL.md').is_file()),
		key=lambda path: path.name,
	)


def _records(root: Path) -> list[dict]:
	records = []
	for folder in _skill_dirs(root):
		name, description = _front_matter(folder / 'SKILL.md')
		records.append(
			{
				'name': name or folder.name,
				'description': description,
				'path': str(folder),
			}
		)
	return records


def _front_matter(path: Path) -> tuple[str, str]:
	"""The `name` and `description` from a SKILL.md, or two empty strings.

	A listing is not the place to reject a malformed skill: a file that
	does not parse still shows up under its directory name, and the
	format is checked by the repository's tests. Values are unquoted
	only at the edges; the shipped descriptions are one line each.
	"""
	text = path.read_text(encoding='utf-8')
	if not text.startswith('---'):
		return '', ''
	end = text.find('\n---', 3)
	if end == -1:
		return '', ''
	fields = {}
	for line in text[3:end].splitlines():
		key, separator, value = line.partition(':')
		if separator and key.strip() in ('name', 'description'):
			fields[key.strip()] = value.strip().strip('"\'')
	return fields.get('name', ''), fields.get('description', '')


def _print_group(title: str, records: list[dict]) -> None:
	"""One heading and one indented line per skill: name, description."""
	print(title)
	if not records:
		print('  none')
		return
	width = min(max((len(record['name']) for record in records), default=0), 22)
	for record in records:
		print(f'  {record["name"]:<{width}}  {record["description"]}')
