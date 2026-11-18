"""`jobtriage open` — a job's posting in the workspace's browser.

`enter` in the TUI already knows how to open one job well: the
workspace's browser profile (the same one research and applications
use), a tab per job, an existing tab adopted rather than duplicated.
This command reuses `cli.tui.opener_for` so the CLI and the keyboard
cannot drift — the same Browser-or-DefaultBrowser choice, the same
`open_or_focus` call — without starting Textual or keeping tab state:
a fresh process remembers nothing, which is exactly what an agent
calling `jobtriage open 42` wants.

The printed line is the URL that was opened, not a confirmation prose
line: it is the one fact a script may want back, and a failing browser
is already an error on stderr with exit 1. An unknown id exits 1 like
`show` and `status` — the id was well formed, the job simply is not
there.
"""

import argparse
import sys

from jobtriage import store
from jobtriage.cli.output import error_text
from jobtriage.cli.tui import opener_for


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser(
		'open',
		help="open a job's posting in the workspace browser (the TUI's opener)",
	)
	parser.add_argument(
		'id',
		type=int,
		metavar='ID',
		help='the job id, as `jobtriage list --json` prints it',
	)
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	try:
		job = store.get(ws.db, args.id)
	except KeyError as bad:
		print(f'error: {error_text(bad)}', file=sys.stderr)
		return 1

	try:
		# remembered=None: a one-shot process has no tab to focus, but the
		# Browser still adopts an open tab at the same URL (open_or_focus
		# scans by URL before opening a second one).
		opener_for(ws).open_or_focus(job['url'], None)
	except Exception as error:  # noqa: BLE001 — a browser failure is one line, not a traceback
		print(f'error: could not open {job["url"]}: {error}', file=sys.stderr)
		return 1

	print(job['url'])
	return 0
