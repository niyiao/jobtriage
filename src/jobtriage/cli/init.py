"""`jobtriage init` — create or upgrade the op folder, and make the first
config the user's own.

Idempotent: safe to run twice, never overwrites a user file. The tree
comes from the shipped assets, the manifest records what was shipped,
and `--upgrade` refreshes after a package update: untouched files
replaced, edited files left alone with the new version beside them as
<file>.new. The full behaviour lives in jobtriage.workspace.create.

Plan 03 step 6 adds the setup questions, asked only when the workspace
is new, stdin is a TTY and `--yes` was not given. They fill four keys in
the template `create` just wrote — locations, families, employment and
the title-filter preset — through tomlkit, so every other key stays
commented and the comments around the four survive. The questions are
deliberately small: the default answer is always the right answer for a
stranger (worldwide, software, any preset, full-time), and everything
else is an edit away in the file.

A non-interactive run (`--yes`, or stdin is not a TTY — an agent, a
pipe, a test) writes the plan's documented defaults instead and asks
nothing: locations `["worldwide"]`, families `["software"]`, preset
`"any"`. Employment is left to the code default rather than written,
because those three are the defaults the plan names; an interactive
run writes the employment answer because it was asked for.

Answers are read with plain `input()`, so a bad one can be retyped;
three bad answers in a row cancel cleanly with exit 1 instead of
looping forever at a prompt no one is reading. `other` as a family
asks for a name, and every choice is validated with the same
vocabulary the config loader uses (geo for places, the shipped preset
names for presets).
"""

import argparse
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import tomlkit

from jobtriage import db, geo, logs, workspace
from jobtriage.cli import tty

# The short list the location question offers: worldwide plus every
# region geo knows, in the order geo declares them. A country is typed
# as its name or ISO code; canonicalising is geo's job, not ours.
PLACES = ('worldwide', *geo.REGIONS)

# The setup shortlist for `families`. The config loader accepts any
# slug (a board or a later plan can add one), but a guided question
# with an open vocabulary is no question at all; `other` is the escape
# hatch and lets the user type their own.
FAMILIES = ('software', 'design', 'product', 'data', 'devops', 'marketing')

# One line each, because the preset names alone do not say which is
# right. `any` is last and is the default: no title filter, which is
# the plan's choice for a stranger.
PRESETS = {
	'web-engineer': 'frontend and full-stack web roles',
	'backend-engineer': 'APIs, services, data stores and platform work',
	'data': 'data engineering, analytics, BI, ML and AI',
	'designer': 'product, UX/UI, interaction and visual design',
	'product-manager': 'product management and product ownership',
	'any': 'no title filter — keep every title the board sends',
}

# Three tries is enough to catch a typo and short enough that a prompt
# attached to the wrong terminal ends instead of hanging forever.
_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class Setup:
	"""The four answers the questions collect, already validated."""

	locations: tuple[str, ...]
	families: tuple[str, ...]
	employment: tuple[str, ...]
	preset: str


DEFAULTS = Setup(('worldwide',), ('software',), ('full-time',), 'any')


class _Cancelled(Exception):
	"""The user pressed Ctrl-C/Ctrl-D or never gave a usable answer."""


class _Invalid(ValueError):
	"""One answer the question can ask again for."""


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser('init', help='create or upgrade the workspace (the op folder)')
	parser.add_argument(
		'path',
		nargs='?',
		type=Path,
		help=f'the folder to use (default: ~/{workspace.PROGRAM})',
	)
	parser.add_argument(
		'--yes',
		action='store_true',
		help='take the defaults, ask nothing (for scripts and agents)',
	)
	parser.add_argument(
		'--upgrade',
		action='store_true',
		help='refresh shipped assets in an existing workspace: '
		'replace the untouched, keep the edited (new version beside '
		'them as <file>.new)',
	)
	parser.set_defaults(run=run)
	return parser


def run(args) -> int:
	if args.upgrade:
		path = args.path or _default_upgrade_target()
		ws, notes = workspace.create(path, upgrade=True)
		logs.setup(ws, verbose=getattr(args, 'verbose', False))
		for note in notes:
			print(note)
		# The database is part of the folder, and doctor's advice for an
		# old schema is this command: upgrading the assets but not the
		# schema would make that advice a lie. A file that is not there
		# is left for the first command that needs it, and migrate()
		# takes its own backup before changing an existing one.
		if ws.db.is_file():
			print(f'database: schema {db.migrate(ws.db, ws.backups)}')
		print(f'workspace: {ws.root}')
		return 0

	path = Path(args.path).expanduser() if args.path else workspace.default_root()
	fresh = not workspace.Workspace(path).config.is_file()

	answers: Setup | None = None
	if fresh and not args.yes and _interactive():
		try:
			answers = _ask_setup()
		except _Cancelled as error:
			print(f'error: {error}', file=sys.stderr)
			return 1

	ws, notes = workspace.create(path)
	logs.setup(ws, verbose=getattr(args, 'verbose', False))
	for note in notes:
		print(note)
	if fresh:
		# Asked answers include employment because it was a question;
		# the non-interactive defaults do not, matching plan 03.
		_write_setup(ws, answers or DEFAULTS, include_employment=answers is not None)
	print(f'workspace: {ws.root}')
	return 0


def _default_upgrade_target() -> Path:
	"""`--upgrade` with no path: the nearest existing workspace, else
	the default folder — upgrading should never create a new one, so
	there's no prompt here."""
	try:
		return workspace.find().root
	except workspace.NoWorkspace:
		return workspace.default_root()


def _interactive() -> bool:
	"""True only for a person at a terminal; `--yes` is checked by run.

	Kept as a function (rather than inlined) because it is the tests'
	one seam for the TTY question; the rule itself lives in cli.tty.
	"""
	return tty.stdin_is_tty()


def _ask_setup() -> Setup:
	"""The four questions, in the order the plan lists them."""
	print('\nA few questions so the first scrape is yours; press Enter to keep a default.')
	locations = _ask_locations()
	families = _ask_families()
	preset = _ask_preset()
	employment = _ask_employment()
	return Setup(locations, families, employment, preset)


def _ask_locations() -> tuple[str, ...]:
	print('\nWhere can you work from?')
	print(textwrap.fill(', '.join(PLACES), width=76, initial_indent='  ', subsequent_indent='  '))
	print('  ... or a country by name or ISO code (GB, Nigeria); comma-separate several.')

	def parse(answer: str) -> tuple[str, ...]:
		if not answer:
			return DEFAULTS.locations
		tokens: list[str] = []
		for piece in answer.split(','):
			text = piece.strip()
			if not text:
				continue
			token = geo.canonical(text)
			if token is None:
				hint = geo.suggestions(text)
				suffix = f' (did you mean {hint[0]!r}?)' if hint else ''
				raise _Invalid(f'unknown location {text!r}{suffix}')
			if token not in tokens:
				tokens.append(token)
		if not tokens:
			raise _Invalid('type at least one place, or press Enter for worldwide')
		return tuple(tokens)

	return _ask('Locations [worldwide]: ', parse)


def _ask_families() -> tuple[str, ...]:
	print('\nWhat kind of role?')
	print(textwrap.fill(', '.join(FAMILIES) + ', or other', width=76, initial_indent='  '))
	print('  Comma-separate several; "other" asks for a family name.')

	def parse(answer: str) -> tuple[str, ...]:
		if not answer:
			return DEFAULTS.families
		values: list[str] = []
		for piece in answer.split(','):
			slug = _slug(piece)
			if not slug:
				continue
			if slug == 'other':
				slug = _slug(input('  your family: '))
				if not slug:
					raise _Invalid('type a family name for "other"')
			elif slug not in FAMILIES:
				allowed = ', '.join(FAMILIES)
				raise _Invalid(f'unknown family {slug!r} (one of {allowed}, or "other")')
			if slug not in values:
				values.append(slug)
		if not values:
			raise _Invalid('type at least one family, or press Enter for software')
		return tuple(values)

	return _ask('Families [software]: ', parse)


def _ask_preset() -> str:
	print('\nTitle filter — what title words count as your role:')
	for index, (name, hint) in enumerate(PRESETS.items(), 1):
		print(f'  {index}. {name:<17} {hint}')

	def parse(answer: str) -> str:
		if not answer:
			return DEFAULTS.preset
		text = answer.strip().casefold()
		if text.isdigit() and 1 <= int(text) <= len(PRESETS):
			return tuple(PRESETS)[int(text) - 1]
		if text in PRESETS:
			return text
		raise _Invalid(f'unknown preset {answer!r} (a name above, or a number 1-{len(PRESETS)})')

	return _ask('Preset [any]: ', parse)


def _ask_employment() -> tuple[str, ...]:
	print('\nEmployment: full-time, contract, or both.')

	def parse(answer: str) -> tuple[str, ...]:
		if not answer:
			return DEFAULTS.employment
		if _slug(answer) == 'both':
			return ('full-time', 'contract')
		values: list[str] = []
		for piece in answer.split(','):
			slug = _slug(piece)
			if not slug:
				continue
			if slug not in ('full-time', 'contract'):
				raise _Invalid(f'unknown employment {slug!r} (full-time, contract, or both)')
			if slug not in values:
				values.append(slug)
		if not values:
			raise _Invalid('type full-time, contract, or both')
		return tuple(values)

	return _ask('Employment [full-time]: ', parse)


def _ask(prompt: str, parse) -> object:
	"""Ask until `parse` accepts, or give up after a few clean errors.

	An empty answer is the question's default and each `parse` decides
	what that means; a ValueError from validation prints on the next
	line and asks again. Ctrl-C and Ctrl-D cancel rather than write a
	config the user did not choose.
	"""
	for _attempt in range(_ATTEMPTS):
		try:
			return parse(input(prompt).strip())
		except (EOFError, KeyboardInterrupt):
			raise _Cancelled('cancelled — nothing was written') from None
		except _Invalid as error:
			print(f'  {error}')
	raise _Cancelled(f'no valid answer after {_ATTEMPTS} tries — nothing was written')


def _slug(text: str) -> str:
	"""The config loader's own spelling: lowercase, hyphen-joined."""
	return '-'.join(text.strip().casefold().replace('_', '-').split())


def _write_setup(ws: workspace.Workspace, setup: Setup, *, include_employment: bool) -> None:
	"""Put the answers under the template's own sections.

	tomlkit is used for the same reason `config set` uses it: the
	template is mostly comments, and those comments are the
	documentation the user reads later. Only the four answered keys
	stop being comments.

	The manifest keeps the hash of the *template*, not of this file:
	the answers make it user data, so `--upgrade` must treat it as
	edited and leave it alone rather than refresh it.
	"""
	document = tomlkit.parse(ws.config.read_text(encoding='utf-8'))

	search = document.get('search')
	if not isinstance(search, dict):
		document['search'] = tomlkit.table()
		search = document['search']
	search['locations'] = list(setup.locations)
	search['families'] = list(setup.families)
	if include_employment:
		search['employment'] = list(setup.employment)

	filter_table = document.get('filter')
	if not isinstance(filter_table, dict):
		document['filter'] = tomlkit.table()
		filter_table = document['filter']
	filter_table['preset'] = setup.preset

	ws.config.write_text(tomlkit.dumps(document), encoding='utf-8')
