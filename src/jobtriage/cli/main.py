"""The one entry point: build the parser, resolve the workspace,
dispatch.

Every command except `init` needs a workspace; `init` makes one. The
resolution order lives in jobtriage.workspace — here it's wrapped in
the one behaviour the plan adds: found nowhere, a TTY gets the offer
to create one at the default path and carry on, anything else exits
with status 2 and the command to run.
"""

import argparse
import os
import sys

from jobtriage import __version__, db, logs, workspace
from jobtriage.cli import attach as attach_cli
from jobtriage.cli import config as config_cli
from jobtriage.cli import db as db_cli
from jobtriage.cli import doctor as doctor_cli
from jobtriage.cli import export as export_cli
from jobtriage.cli import filter as filter_cli
from jobtriage.cli import init as init_cli
from jobtriage.cli import list as list_cli
from jobtriage.cli import mcp as mcp_cli
from jobtriage.cli import note as note_cli
from jobtriage.cli import open as open_cli
from jobtriage.cli import scrape as scrape_cli
from jobtriage.cli import show as show_cli
from jobtriage.cli import skills as skills_cli
from jobtriage.cli import sources as sources_cli
from jobtriage.cli import status as status_cli
from jobtriage.cli import tty
from jobtriage.cli import tui as tui_cli

PROGRAM = 'jobtriage'


class _VersionAction(argparse.Action):
	"""`--version` with the three facts a support request starts with.

	argparse's built-in version action can only print a string fixed
	when the parser is built, and the workspace path and the schema
	version are only known once the command runs. Keeping the action
	(and its exit code 0) means `--version` still behaves like every
	other CLI's.
	"""

	def __init__(
		self,
		option_strings,
		dest=argparse.SUPPRESS,
		default=argparse.SUPPRESS,
		help=None,
	):
		super().__init__(
			option_strings=option_strings,
			dest=dest,
			default=default,
			nargs=0,
			help=help,
		)

	def __call__(self, parser, namespace, values, option_string=None):
		print(version_text())
		parser.exit(0)


def version_text() -> str:
	"""The build, the folder it is using and the database's schema.

	One block rather than one number: when someone reports a bug, these
	are the three answers that decide what can be wrong, and asking for
	them separately costs a round trip. No workspace is not an error —
	`--version` must work before `init` — so that line says what to run
	instead.
	"""
	lines = [f'{PROGRAM} {__version__}']
	try:
		ws = workspace.find()
	except workspace.NoWorkspace:
		lines.append(f'workspace: none — run "{PROGRAM} init"')
		return '\n'.join(lines)
	lines.append(f'workspace: {ws.root}')
	lines.append(f'database: {_schema_line(ws)}')
	return '\n'.join(lines)


def _schema_line(ws: workspace.Workspace) -> str:
	"""The database's schema version, or the build's when there is no file."""
	if not ws.db.is_file():
		return f'schema {db.LATEST} (no database file yet)'
	try:
		version = db.schema_version(ws.db)
	except Exception as error:  # noqa: BLE001 — a corrupt file is a fact to report, not a crash
		return f'unreadable ({type(error).__name__}: {error})'
	if version == db.LATEST:
		return f'schema {version}'
	if version > db.LATEST:
		return f'schema {version}, this build understands up to {db.LATEST} — upgrade {PROGRAM}'
	return f'schema {version}, this build expects {db.LATEST} — run "{PROGRAM} init --upgrade"'


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		prog=PROGRAM,
		description='Scrape remote job boards, keep the relevant roles, '
		'remember them in SQLite, triage them from the keyboard.',
	)
	parser.add_argument(
		'--workspace',
		metavar='PATH',
		help=f'use this op folder (default: the nearest one — see `{PROGRAM} init`)',
	)
	parser.add_argument(
		'--version',
		action=_VersionAction,
		help='print the version, the workspace path and the database schema version',
	)
	parser.add_argument(
		'-v',
		'--verbose',
		action='store_true',
		help='log at DEBUG on stderr; every command also writes <op>/.jobtriage/logs/jobtriage.log',
	)
	sub = parser.add_subparsers(dest='command', metavar='COMMAND')

	scrape_cli.add_parser(sub)
	sources_cli.add_parser(sub)
	list_cli.add_parser(sub)
	show_cli.add_parser(sub)
	open_cli.add_parser(sub)
	status_cli.add_parser(sub)
	note_cli.add_parser(sub)
	attach_cli.add_parser(sub)
	export_cli.add_parser(sub)
	config_cli.add_parser(sub)
	filter_cli.add_parser(sub)
	db_cli.add_parser(sub)
	skills_cli.add_parser(sub)
	mcp_cli.add_parser(sub)
	tui_cli.add_parser(sub)
	init_cli.add_parser(sub)
	doctor_cli.add_parser(sub)

	where = sub.add_parser('where', help='print the workspace path (handy for scripts and agents)')
	where.set_defaults(run=_run_where)
	return parser


def main(argv: list[str] | None = None) -> int:
	args = build_parser().parse_args(argv)
	if args.command is None:
		# the daily action needs no typing (decision 3); no subparser
		# ran, so the dispatch target is set by hand
		args.command = 'tui'
		args.run = tui_cli.run
	if args.command == 'init':
		return _dispatch(init_cli.run, args)  # init makes a workspace; it doesn't need one
	ws = _resolve(args)
	if ws is None:
		return 2
	logs.setup(ws, verbose=args.verbose)
	_notice(ws)
	return _dispatch(args.run, args, ws)


def _notice(ws: workspace.Workspace) -> None:
	"""Say once that the folder is older than the build.

	Plan 08's upgrade notice: the package can update and the workspace
	cannot, so the first run after one tells the user which command
	refreshes the templates and skills. stderr, because stdout is the
	command's answer — `list --json` must stay one document a script
	can parse. `init` never reaches here (it does not need an
	existing workspace), which is why `init --upgrade` is not nagged
	about itself.
	"""
	message = workspace.upgrade_notice(ws)
	if message:
		print(message, file=sys.stderr)


def _dispatch(run, args, ws: workspace.Workspace | None = None) -> int:
	"""Run one command, turning an unexpected failure into the CLI's
	one-line error.

	Plan 05's agent contract is "no tracebacks in CLI paths": an agent
	reads stderr and an exit code, and a stack trace is neither advice
	nor a sentence it can act on. The named failures are already caught
	where they happen (ConfigError, RegistryError, BoardError, a missing
	id); this is the last line for everything a command did not
	anticipate — a database from a newer build, a corrupt file, a
	permission. `JOBTRIAGE_TRACEBACK=1` keeps the traceback for the
	developer who is debugging the command itself.

	KeyboardInterrupt is not an Exception and still stops the process;
	argparse's own errors stay SystemExit(2) from parse_args.
	"""
	try:
		return (run(args) if ws is None else run(args, ws)) or 0
	except Exception as error:  # a command's surprise is one line, never a traceback
		if os.environ.get('JOBTRIAGE_TRACEBACK'):
			raise
		text = str(error).strip()
		message = ' '.join(text.splitlines()) or type(error).__name__
		print(f'error: {message}', file=sys.stderr)
		return 1


def _resolve(args: argparse.Namespace) -> workspace.Workspace | None:
	"""The workspace for this run — or None, after telling the user
	what to do about it."""
	try:
		return workspace.find(path=args.workspace)
	except workspace.NoWorkspace as missing:
		if tty.interactive():
			answer = input(f'no workspace — create one at {workspace.default_root()}? [y/N] ')
			if answer.strip().lower() in ('y', 'yes'):
				ws, notes = workspace.create(workspace.default_root())
				for note in notes:
					print(note)
				return ws
		message = str(missing) or 'no workspace — run "jobtriage init"'
		print(f'error: {message}', file=sys.stderr)
		return None


def _run_where(args, ws) -> int:
	print(ws.root)
	return 0
