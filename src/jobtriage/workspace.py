"""The workspace (the op folder): everything personal, in one place.

The repo ships code and nothing else; the workspace holds one
person's data — config, database, browser profile, resume, letters,
skills, plugins. This module is the only place that knows where any
of it is: every other module takes a Workspace (or a path it got
from one) as an argument, and tests pass a tmp_path one.

Finding it, in order, stopping at the first hit:

1. `--workspace PATH` on the command line.
2. `JOBTRIAGE_HOME` in the environment.
3. Walk up from the current directory looking for jobtriage.toml,
   the way git finds `.git` — an agent working inside the op folder
   needs no flags and no env.
4. The pointer file `jobtriage init` writes under the platform config
   dir, so the command works from any directory.
5. Nothing found: NoWorkspace. The CLI offers `jobtriage init` in a
   TTY and exits with a pointer to it otherwise.

The marker file is jobtriage.toml — the config, plan 03 — and its
presence is what makes a folder the op folder. `create()` builds
the tree from the shipped assets, never overwriting a file that is
already there, and records in .jobtriage/manifest.json the hash of
every file it shipped: that manifest is what lets a later
`jobtriage init --upgrade` replace the files the user never touched
and leave the ones they edited alone (beside those it writes the
new version as <file>.new, with a note). The same pass links the agent
discovery folders (`.agents/skills`, `.claude/skills`,
`.opencode/skills`) and `CLAUDE.md` to the one copy of each, so every
tool finds the skills and the instructions the folder already has.

The same pass writes `.jobtriage/version` — the package version whose
templates are on disk. `upgrade_notice()` compares it with the running
build so the first command after a package update can say which one
command refreshes the folder.
"""

import hashlib
import importlib.resources
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from jobtriage import __version__

PROGRAM = 'jobtriage'
ENV_VAR = 'JOBTRIAGE_HOME'
CONFIG_NAME = 'jobtriage.toml'
DB_NAME = 'jobtriage.db'

# Shipped template files whose name in the op folder differs from
# their name in assets/ (a leading dot would confuse the package
# dir's own tooling).
RENAMED = {'gitignore': '.gitignore'}

# Assets the package reads for itself and does not copy into the op
# folder. The title-filter presets are shared code data (`preset =
# "web-engineer"`), not a file the user is meant to edit in place.
PACKAGE_ONLY = ('presets',)

# Agent tools look for project skills in their own dot-folder. Each gets
# a `skills` symlink to ../skills, so one copy in the op folder serves
# every tool (plan 05). A real directory instead of a link is the
# Windows fallback: symlinks there need a privilege, and `--upgrade`
# refreshes those copies.
AGENT_FOLDERS = ('.agents', '.claude', '.opencode')


class NoWorkspace(Exception):
	"""No op folder resolved. str() is the message to show the user."""


@dataclass(frozen=True)
class Workspace:
	"""Every path the tool touches, as properties of one root.

	Nobody outside this module joins a path to find workspace data;
	modules that need a path take it as an argument (usually one of
	these properties).
	"""

	root: Path

	@property
	def config(self) -> Path:
		"""The config file; its presence marks the folder."""
		return self.root / CONFIG_NAME

	@property
	def db(self) -> Path:
		"""The database: jobs, statuses, notes, history."""
		return self.root / DB_NAME

	@property
	def profile(self) -> Path:
		"""The person: RESUME.md, DETAILS.md, resume PDF."""
		return self.root / 'profile'

	@property
	def letters(self) -> Path:
		"""Cover letters, one file per application."""
		return self.root / 'letters'

	@property
	def scrapers(self) -> Path:
		"""User scraper plugins (plan 04)."""
		return self.root / 'scrapers'

	@property
	def skills(self) -> Path:
		"""Agent skills (plan 05)."""
		return self.root / 'skills'

	@property
	def browser_profile(self) -> Path:
		"""The Chrome profile for applying and research."""
		return self.root / '.browser-profile'

	@property
	def state(self) -> Path:
		"""Tool state: version stamp, asset manifest, logs, TUI state."""
		return self.root / '.jobtriage'

	@property
	def backups(self) -> Path:
		"""Database snapshots taken before a migration (plan 02).

		They live under state/, not beside the database, because they
		are ours to prune and must never be mistaken for the live file
		by `sqlite3 jobtriage.db` or a glob in a script.
		"""
		return self.state / 'backups'

	@property
	def logs(self) -> Path:
		"""The log folder, created by the first command that logs.

		Under state/ because logs are the tool's, not the user's: a
		backup or a bug report may want them, and deleting the folder
		must cost nothing but history.
		"""
		return self.state / 'logs'

	@property
	def log_file(self) -> Path:
		"""The rotating log every command appends to (see jobtriage.logs)."""
		return self.logs / 'jobtriage.log'

	@property
	def env(self) -> Path:
		"""Secrets (API keys); optional."""
		return self.root / '.env'

	@property
	def manifest(self) -> Path:
		"""Every shipped asset with the hash of the version shipped."""
		return self.state / 'manifest.json'

	@property
	def version_file(self) -> Path:
		"""The tool version that last touched this folder."""
		return self.state / 'version'

	@property
	def tui_state(self) -> Path:
		"""Where the TUI remembers its place: tab, search, selected job.

		Under state/, not beside the database: it is the TUI's session
		bookmark, safe to delete, and must never be mistaken for data.
		"""
		return self.state / 'tui.json'


def default_root() -> Path:
	"""The default op folder: ~/jobtriage — visible and agent-friendly,
	easy to cd into (plans/README decision 2)."""
	return Path.home() / PROGRAM


def pointer_path() -> Path:
	"""The pointer file `init` writes, so `jobtriage` works from any
	directory without flags or env."""
	return platformdirs.user_config_path(PROGRAM) / 'workspace'


def find(
	path: str | Path | None = None,
	env: dict[str, str] | None = None,
	start: Path | None = None,
) -> Workspace:
	"""Resolve the workspace by the order in the module docstring.

	`env` defaults to os.environ and `start` to the current directory;
	both exist so tests don't have to monkeypatch the world. Raises
	NoWorkspace when nothing resolves.
	"""
	if path:
		return _explicit(Path(path))
	home = (env if env is not None else os.environ).get(ENV_VAR)
	if home:
		return _explicit(Path(home))
	for folder in _walk_up(Path(start or Path.cwd())):
		if (folder / CONFIG_NAME).is_file():
			return Workspace(folder)
	pointer = pointer_path()
	if pointer.is_file():
		target = Path(pointer.read_text().strip())
		if (target / CONFIG_NAME).is_file():
			return Workspace(target)
		# a pointer at a folder that's gone: fall through to the error
	raise NoWorkspace('no workspace — run "jobtriage init"')


def _explicit(path: Path) -> Workspace:
	"""A folder given by flag or env must already be a workspace: the
	marker file is the only thing that says so."""
	path = path.expanduser().resolve()
	if not (path / CONFIG_NAME).is_file():
		raise NoWorkspace(f'{path} is not a workspace (no {CONFIG_NAME}) — run "jobtriage init {path}"')
	return Workspace(path)


def _walk_up(folder: Path):
	"""folder, its parent, and so on to the filesystem root — the way
	git finds .git."""
	folder = folder.resolve()
	while True:
		yield folder
		if folder.parent == folder:
			return
		folder = folder.parent


def assets_dir() -> Path:
	"""The shipped templates, whether running from a checkout or an
	unpacked wheel."""
	return Path(importlib.resources.files(PROGRAM)) / 'assets'


def presets_dir() -> Path:
	"""The shipped title-filter presets (plan 03), read from the
	package by `config.load` rather than copied into the op folder."""
	return assets_dir() / 'presets'


def create(path: Path, upgrade: bool = False) -> tuple[Workspace, list[str]]:
	"""Build or refresh the op folder at `path` from the shipped
	assets. Returns the workspace and the human notes to print.

	Never overwrites a user file: a file that isn't there is written,
	a file that is there is left alone — on upgrade, replaced only
	when its hash still matches the manifest (untouched since it
	shipped); an edited file keeps the new version beside it as
	<file>.new. Safe to run twice.
	"""
	root = path.expanduser().resolve()
	root.mkdir(parents=True, exist_ok=True)
	if upgrade and not (root / CONFIG_NAME).is_file():
		raise NoWorkspace(f'{root} is not a workspace (no {CONFIG_NAME}) — nothing to upgrade')

	ws = Workspace(root)
	for folder in (  # the tree, before any asset lands in it
		ws.profile,
		ws.letters,
		ws.scrapers,
		ws.skills,
		ws.browser_profile,
		ws.state,
	):
		folder.mkdir(parents=True, exist_ok=True)

	old_manifest = _read_manifest(ws)
	manifest: dict[str, str] = {}
	notes = []
	for rel, data in sorted(_shipped_assets().items()):
		target = root / RENAMED.get(rel, rel)
		digest = hashlib.sha256(data).hexdigest()
		manifest[rel] = digest
		if not target.exists():
			target.parent.mkdir(parents=True, exist_ok=True)
			target.write_bytes(data)
			notes.append(f'created {target.relative_to(root)}')
		elif upgrade and rel in old_manifest:
			if _sha256(target) == old_manifest[rel]:
				if _sha256(target) != digest:  # untouched: ours to refresh
					target.write_bytes(data)
					notes.append(f'upgraded {target.relative_to(root)}')
			else:  # the user has edited it: theirs, not ours
				beside = target.with_name(target.name + '.new')
				beside.write_bytes(data)
				notes.append(
					f'{target.relative_to(root)} left alone (edited) — new version saved as {beside.name}'
				)
		elif rel not in old_manifest and upgrade:
			pass  # a file that was never ours: leave it, say nothing
		# not upgrade, file already there: idempotent, say nothing

	_write_if_changed(ws.manifest, json.dumps(manifest, indent='\t', sort_keys=True))
	_write_if_changed(ws.version_file, __version__)
	notes.extend(_connect_agents(ws, upgrade=upgrade))
	_write_pointer(root)
	return ws, notes


def upgrade(path: Path) -> tuple[Workspace, list[str]]:
	"""`create` in upgrade mode: refresh shipped assets after a
	package update."""
	return create(path, upgrade=True)


def upgrade_notice(ws: Workspace) -> str | None:
	"""One line when this build is newer than the folder's stamp.

	`create()` writes the version that shipped the templates to
	`.jobtriage/version` (and `init --upgrade` rewrites it). After a
	package upgrade the two disagree, and the templates and skills on
	disk are older than the code reading them: `init --upgrade` is
	the fix, so the CLI says so once per run rather than waiting for
	a template to disappoint someone.

	Silence means "nothing to report": no stamp (a folder from before
	the stamp existed, or a hand-made one), an unreadable one, the
	same version, or a stamp from a *newer* build — a downgrade is
	not this command's business. The comparison is numeric only; the
	stamp is always a released version, so a pre-release suffix
	parses as the release it leads to.
	"""
	stamp = ''
	if ws.version_file.is_file():
		try:
			stamp = ws.version_file.read_text(encoding='utf-8').strip()
		except OSError:
			return None
	if not stamp or stamp == __version__:
		return None
	if not _version_parts(stamp) or _version_parts(stamp) >= _version_parts(__version__):
		return None
	return (
		f'notice: workspace was created by {PROGRAM} {stamp}; '
		f'run "{PROGRAM} init --upgrade" to refresh templates and skills'
	)


def _version_parts(text: str) -> tuple[int, ...]:
	"""A version's leading numbers (`0.3.0` -> (0, 3, 0)), for ordering.

	Deliberately not a dependency: packaging would be a new requirement
	for one comparison, and the stamp only ever holds the tool's own
	released versions.
	"""
	parts = []
	for piece in text.split('.'):
		digits = ''
		for char in piece:
			if not char.isdigit():
				break
			digits += char
		if not digits:
			break
		parts.append(int(digits))
	return tuple(parts)


def _connect_agents(ws: Workspace, upgrade: bool) -> list[str]:
	"""Make the agent discovery folders point at the one skills copy.

	Three tools already read `skills/` through their own dot-folder:
	`.agents`, `.claude` and `.opencode`. A relative symlink is the
	honest shape — one source, no drift when `init --upgrade` refreshes
	a skill. Windows cannot create symlinks without a privilege, so the
	fallback is a copy of `skills/`; on upgrade the copy is refreshed
	in place, which is the only way `--upgrade` can keep it current.
	A folder the user made themselves is never replaced by a link.
	"""
	notes: list[str] = []
	for name in AGENT_FOLDERS:
		parent = ws.root / name
		link = parent / 'skills'
		shown = link.relative_to(ws.root).as_posix()
		parent.mkdir(parents=True, exist_ok=True)
		if link.is_symlink():
			if link.resolve() == ws.skills.resolve():
				continue
			notes.append(f'{shown} left alone (points elsewhere)')
			continue
		if link.exists():
			if upgrade:
				_copy_skills(ws.skills, link)
				notes.append(f'refreshed {shown}')
			continue  # a copy already in place, or the user's own folder
		try:
			link.symlink_to(Path('..') / 'skills', target_is_directory=True)
			notes.append(f'linked {shown} -> ../skills')
		except OSError:
			_copy_skills(ws.skills, link)
			notes.append(f'copied skills to {shown} (symlink not permitted)')

	claude = ws.root / 'CLAUDE.md'
	agents = ws.root / 'AGENTS.md'
	if claude.is_symlink() or claude.exists():
		return notes
	if agents.is_file():
		try:
			claude.symlink_to('AGENTS.md')
			notes.append('linked CLAUDE.md -> AGENTS.md')
		except OSError:
			shutil.copyfile(agents, claude)
			notes.append('copied AGENTS.md to CLAUDE.md (symlink not permitted)')
	return notes


def _copy_skills(source: Path, target: Path) -> None:
	"""A tree copy for platforms where a symlink is not allowed."""
	shutil.copytree(source, target, dirs_exist_ok=True)


def _shipped_assets() -> dict[str, bytes]:
	"""Every file under assets/, keyed by path relative to assets/ —
	the tree it becomes in the op folder."""
	assets = {}
	folder = assets_dir()
	for file in sorted(folder.rglob('*')):
		if not file.is_file():
			continue
		rel = file.relative_to(folder)
		if rel.parts and rel.parts[0] in PACKAGE_ONLY:
			continue  # the package's own data, not a workspace template
		if '__pycache__' in rel.parts:
			continue  # bytecode from a checkout, never a template
		assets[rel.as_posix()] = file.read_bytes()
	return assets


def _read_manifest(ws: Workspace) -> dict[str, str]:
	"""The manifest of the last shipped version — {relative path:
	sha256 of what shipped}, {} when there is none or it's unreadable
	(a corrupt manifest means 'the user owns every file', which is
	the safe reading)."""
	if not ws.manifest.is_file():
		return {}
	try:
		loaded = json.loads(ws.manifest.read_text())
	except ValueError:
		return {}
	return loaded if isinstance(loaded, dict) else {}


def _sha256(path: Path) -> str:
	return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_if_changed(path: Path, text: str) -> None:
	"""Write only when the content differs, so a second `init` changes
	nothing at all — not even mtimes."""
	if not path.is_file() or path.read_text() != text:
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(text)


def _write_pointer(root: Path) -> None:
	"""Point the platform config dir at this workspace, so the command
	works from any directory."""
	pointer = pointer_path()
	pointer.parent.mkdir(parents=True, exist_ok=True)
	_write_if_changed(pointer, str(root))
