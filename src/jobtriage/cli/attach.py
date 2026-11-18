"""`jobtriage attach` — record a file against a job.

Plan 02's schema left an `attachments` table for exactly this ("optional,
for plan 05"): a cover letter, a portfolio PDF, anything an application
produced. The row is the contract — `show` and the TUI's details pane
already read the table — so attaching never writes SQL of its own.

The path rules live in `jobtriage.attachments` because the MCP server's
`attach` tool asks the same questions; this command is the printing
half. A relative PATH resolves from the op folder, the stored path is
workspace-relative inside it, and the kind defaults from the path
(`cover_letter` under `letters/`, `file` otherwise). `--kind` overrides
the inference for future kinds (plan 02 leaves the vocabulary open), so
no migration is needed when one arrives.

Exit codes match the other one-job commands: 1 for a missing job, a
missing file, or an unwritable database row; argparse's misuse stays 2.
"""

import argparse
import sys

from jobtriage import store
from jobtriage.attachments import default_kind, resolve_path, stored_path
from jobtriage.cli.output import error_text


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser(
		'attach',
		help='record a file (a cover letter, a PDF) against a job',
	)
	parser.add_argument(
		'id',
		type=int,
		metavar='ID',
		help='the job id, as `jobtriage list --json` prints it',
	)
	parser.add_argument(
		'path',
		metavar='PATH',
		help='the file; a relative path resolves from the op folder (letters/... works)',
	)
	parser.add_argument(
		'--kind',
		metavar='KIND',
		help='what it is (default: cover_letter under letters/, else file)',
	)
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	try:
		target = resolve_path(args.path, ws.root)
	except OSError as error:
		print(f'error: {error}', file=sys.stderr)
		return 1

	stored = stored_path(target, ws.root)
	kind = args.kind or default_kind(target, ws.root)
	try:
		created = store.attach(ws.db, args.id, stored, kind)
	except KeyError as bad:
		print(f'error: {error_text(bad)}', file=sys.stderr)
		return 1

	if created:
		print(f'{args.id}: attached {stored} ({kind})')
	else:
		print(f'{args.id}: already attached {stored}')
	return 0
