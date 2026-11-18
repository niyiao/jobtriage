"""The filters `list` and `export` share: one set of flags, one query.

Both commands answer "which jobs", so the flags are declared here once
rather than twice and allowed to drift — `--status` on export selecting
a different set from the same flag on list would be a bug nobody
notices until a spreadsheet disagrees with the terminal.

Nothing is validated here: the values go straight to store.query, whose
`by_slug` names an unknown status and whose ordering whitelist names an
unknown column. Keeping the I/O out of this module also means a caller
decides what a bad flag looks like — the CLI prints one line and exits,
while a test can call select() and expect the exception.
"""

import argparse
from pathlib import Path

from jobtriage import store


def add(parser: argparse.ArgumentParser) -> None:
	"""Add the five filter flags every job query accepts.

	No `choices=` on the order: the store's whitelist is the one
	source, and its ValueError already lists every column. Duplicating
	it here would be a second list to forget.
	"""
	parser.add_argument(
		'--status',
		metavar='SLUG',
		help='only jobs with this status (new, skipped, to_apply, applied)',
	)
	parser.add_argument(
		'--since',
		metavar='WINDOW',
		help='only jobs published in the last N days, like 7d (undated jobs count by first seen)',
	)
	parser.add_argument('--source', metavar='SOURCE', help='only jobs from this board')
	parser.add_argument(
		'--search',
		metavar='TEXT',
		help='only jobs whose title, company, role or URL contains TEXT',
	)
	parser.add_argument(
		'--order',
		metavar='COL',
		default='published',
		help='sort by one of ' + ', '.join(sorted(store._ORDERINGS)) + ' (default: published)',
	)


def select(db_path: str | Path, args: argparse.Namespace, limit: int | None = None) -> list[dict]:
	"""The jobs these flags select, all of them unless `limit` says otherwise.

	Raises whatever store.query raises for a bad flag — KeyError for an
	unknown status slug, ValueError for a bad window or order — so the
	command can print the one-line message and choose an exit code.
	"""
	return store.query(
		db_path,
		status=args.status,
		since=args.since,
		source=args.source,
		search=args.search,
		order=args.order,
		limit=limit,
	)
