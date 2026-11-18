"""`jobtriage note` — append a diary entry to one job.

Notes accumulate rather than replace (store.add_note), so the second
call keeps the first — which is the point: "recruiter said..." is worth
more later than the note that overwrote it. The command confirms the id
it wrote to instead of echoing the whole notes column; `show` is where
you read it back.
"""

import argparse
import sys

from jobtriage import store
from jobtriage.cli.output import error_text


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser('note', help='append a note to one job (keep a diary of it)')
	parser.add_argument(
		'id',
		type=int,
		metavar='ID',
		help='the job id, as `jobtriage list --json` prints it',
	)
	parser.add_argument('text', metavar='TEXT', help='the note; quote it if it has spaces')
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	try:
		store.add_note(ws.db, args.id, args.text)
	except KeyError as bad:
		print(f'error: {error_text(bad)}', file=sys.stderr)
		return 1
	print(f'{args.id}: note added')
	return 0
