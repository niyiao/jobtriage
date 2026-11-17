"""`jobtriage list` — the newest jobs in the database, no scraping.

This is the read side of plan 02's database: every filter is optional
and independent, `--json` is the shape agents and skills consume, and
the default is still the three-line-per-job table the old command
printed. The positional limit stays for `jobtriage list 20` (README and
muscle memory); `--limit` is the flag form, and the flag wins if both
are given.

The summary line stays on stderr so it never contaminates a pipe — and
is omitted entirely for `--json`, where a clean stream is the point.
"""

import argparse
import sys

from jobtriage import store
from jobtriage.cli import filters
from jobtriage.cli.output import error_text, print_jobs, print_json


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser('list', help="list the newest jobs in the database, don't scrape")
	parser.add_argument(
		'limit',
		nargs='?',
		type=int,
		default=50,
		help='how many jobs to show (default: 50)',
	)
	filters.add(parser)
	parser.add_argument(
		'--limit',
		dest='limit_flag',
		type=int,
		metavar='N',
		help='how many jobs to show (the flag form of the positional limit)',
	)
	parser.add_argument(
		'--json',
		action='store_true',
		help='print the jobs as JSON, one list of full job dicts (for agents and scripts)',
	)
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	limit = args.limit if args.limit_flag is None else args.limit_flag
	try:
		jobs = filters.select(ws.db, args, limit=limit)
	except (KeyError, ValueError) as bad:
		# by_slug's KeyError for a typo'd status, query's ValueError for
		# a bad window or order: both are the flag's fault, not the
		# database's, so exit 2 and say which flag was wrong.
		print(f'error: {error_text(bad)}', file=sys.stderr)
		return 2
	if args.json:
		print_json(jobs)
		return 0
	print_jobs(jobs)
	sys.stdout.flush()
	print(
		f'{sum(store.counts(ws.db).values())} in the database ({ws.db.name}, showing up to {limit})',
		file=sys.stderr,
	)
	return 0
