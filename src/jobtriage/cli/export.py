"""`jobtriage export` — the table as CSV or JSON, for a spreadsheet.

One-way: unlike the old Google Sheet there is no write-back path, so an
export is a snapshot. The filters are exactly `list`'s (cli/filters.py),
and the default is every matching job, because "export" means the whole
set unless asked otherwise.

CSV is the spreadsheet view, not a byte copy: the database's internal
cache keys (title_key, company_key) and the year/month key parts stay
out, locations and published are shaped the way a person reads them, and
notes with commas or newlines are quoted by the stdlib csv module rather
than mangled. status keeps its slug, the same value JSON and the
database carry, so a script matching a CSV row to `list --json` sees the
same word.
"""

import argparse
import csv
import sys

from jobtriage.cli import filters
from jobtriage.cli.output import error_text, print_json

# Familiar sheet order, with the new stable id first and notes last.
# Written out because `SELECT *` also returns the cache keys, which are
# store business, not spreadsheet columns.
CSV_COLUMNS = (
	'id',
	'title',
	'company',
	'role',
	'published',
	'locations',
	'salary',
	'source',
	'url',
	'status',
	'first_seen',
	'last_seen',
	'notes',
)


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser('export', help='write the jobs as CSV or JSON, for a spreadsheet')
	parser.add_argument(
		'--format',
		choices=('csv', 'json'),
		default='csv',
		help='output format (default: csv)',
	)
	filters.add(parser)
	parser.add_argument(
		'--limit',
		type=int,
		metavar='N',
		help='at most N jobs (default: all of them)',
	)
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	try:
		jobs = filters.select(ws.db, args, limit=args.limit)
	except (KeyError, ValueError) as bad:
		print(f'error: {error_text(bad)}', file=sys.stderr)
		return 2
	if args.format == 'json':
		print_json(jobs)
		return 0
	writer = csv.writer(sys.stdout, lineterminator='\n')
	writer.writerow(CSV_COLUMNS)
	for job in jobs:
		writer.writerow([_cell(name, job.get(name)) for name in CSV_COLUMNS])
	return 0


def _cell(name: str, value) -> str:
	"""One spreadsheet cell: the column's presentation, not the column.

	Only the two shapes a spreadsheet reads badly are transformed.
	Everything else is the stored value, so a CSVer can still match on
	url or copy a note verbatim.
	"""
	if name == 'published':
		# boards give timestamps; a spreadsheet wants the day, and the
		# rest of the tool says 'undated' rather than leaving a hole
		return value[:10] if value else 'undated'
	if value is None:
		return ''
	if name == 'locations':
		return ', '.join(value)
	return str(value)
