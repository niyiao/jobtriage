"""`jobtriage status` — set one job's status from a shell or an agent.

The write is store.set_status, whose default actor is 'cli'; this module
reads the old value first so it can print the transition the way the
plan draws it (`42: new → to_apply`). Setting the status a job already
has is reported as "already", matching the store's no-op: a printed
`new → new` would claim a change that history never recorded.

`by_slug` runs before the write, so a typo (`to_aply`) leaves the job
exactly as it was and exits 2 with the slug it did not know. A missing
id exits 1: the flag was fine, the job is not there.
"""

import argparse
import sys

from jobtriage import status as status_table
from jobtriage import store
from jobtriage.cli.output import error_text


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser('status', help="set a job's status: new, skipped, to_apply, applied")
	parser.add_argument(
		'id',
		type=int,
		metavar='ID',
		help='the job id, as `jobtriage list --json` prints it',
	)
	parser.add_argument(
		'status',
		metavar='STATUS',
		help='the status slug to set: ' + ', '.join(entry.slug for entry in status_table.STATUSES),
	)
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	try:
		# validate before the lookup: an unknown slug is a bad argument,
		# not a missing job, and the message should name the slug
		slug = status_table.by_slug(args.status).slug
	except KeyError as bad:
		print(f'error: {error_text(bad)}', file=sys.stderr)
		return 2
	try:
		job = store.get(ws.db, args.id)
	except KeyError as bad:
		print(f'error: {error_text(bad)}', file=sys.stderr)
		return 1
	old = job['status']
	store.set_status(ws.db, args.id, slug)
	if old == slug:
		print(f'{args.id}: already {slug}')
	else:
		print(f'{args.id}: {old} → {slug}')
	return 0
