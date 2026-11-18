"""`jobtriage db` — the database file, a SQLite shell, its schema, and
the one-time sheet import.

`path` is for scripts and agents that need the file without resolving
the workspace themselves. `shell` runs the real `sqlite3` — inheriting
stdin, stdout, stderr and returning its exit code — rather than growing
a mini-SQL console: plan 05's skills answer questions with SQL, and the
tool that already does that best is one argv away. `schema` is the map
to read before writing any: PRAGMA table_info for the types,
PRAGMA index_list for the unique key and indexes, plus one comment per
column that PRAGMA cannot carry. The comments live here because db.py's
SQL is the truth for structure, not prose; keep them in step when a
migration adds a column. `import-sheet` lives in its own module (and
goes away after a release or two, with the sheet itself): it is the
bridge from the old spreadsheet, not part of the database's shape.
"""

import argparse
import subprocess
import sys

from jobtriage import db
from jobtriage.cli import import_sheet

# Creation order: jobs first because its children point at it, then the
# two that name a job, then the run history, which stands alone. A
# migration that adds a table adds it here too.
_TABLES = ('jobs', 'status_history', 'attachments', 'source_runs')

_COMMENTS = {
	'jobs': {
		'id': 'stable id; every command and the TUI address a job by this',
		'title': "the board's title, as first seen",
		'company': 'as first seen',
		'role': 'the relevance role word that matched (software, ...)',
		'year': 'published year; the current year when undated',
		'month': 'published month; the current month when undated',
		'title_key': 'folded title; part of the cache key',
		'company_key': 'folded company; part of the cache key',
		'url': 'the newest posting URL seen',
		'published': 'ISO UTC; NULL when the board gave no date',
		'locations': "JSON array of the board's location strings",
		'salary': 'as the board wrote it; a later sighting never clears it',
		'source': 'the board that served it most recently',
		'first_seen': 'when the pipeline first saw it',
		'last_seen': 'last scrape that saw it; stale means probably closed',
		'status': 'slug from status.py: new, skipped, to_apply, applied',
		'status_at': 'when status last changed; NULL until it does',
		'notes': 'free text; notes accumulate, newline separated',
	},
	'status_history': {
		'id': 'orders two changes in the same second',
		'job_id': 'the job the change belongs to; cascades on delete',
		'old': 'the status before; NULL for the first recorded change',
		'new': 'the status after',
		'at': 'when the change was made (ISO UTC)',
		'actor': 'who made it: tui, cli, import or agent',
	},
	'attachments': {
		'id': "orders a job's files",
		'job_id': 'the job the file belongs to; cascades on delete',
		'kind': "what it is, like 'cover_letter'",
		'path': 'where the file lives, in or under the workspace',
		'created_at': 'when it was attached (ISO UTC)',
	},
	'source_runs': {
		'id': 'orders runs that share a second',
		'source': "a board's canonical NAME",
		'started_at': 'when the run began (ISO UTC)',
		'duration_ms': 'how long the registry wrapper took',
		'ok': '1 when the board returned, 0 when it raised',
		'fetched': 'rows the board returned, before validation',
		'dropped_invalid': 'rows the contract rejected',
		'error': "the board's one-line exception; NULL on success",
	},
}


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser(
		'db',
		help='the database: path, sqlite3 shell, schema, sheet import',
	)
	actions = parser.add_subparsers(dest='db_command', metavar='ACTION', required=True)

	path = actions.add_parser('path', help='print the database file path')
	path.set_defaults(run=run_path)

	shell = actions.add_parser('shell', help='run sqlite3 on the database; extra args go to sqlite3')
	shell.add_argument(
		'args',
		nargs=argparse.REMAINDER,
		metavar='ARG',
		help='passed through to sqlite3, like "select * from jobs limit 5;"',
	)
	shell.set_defaults(run=run_shell)

	schema = actions.add_parser('schema', help='print the tables and columns with comments')
	schema.set_defaults(run=run_schema)

	# Last because it is the one temporary action: it imports the old
	# sheet once and goes away after a release or two, with the CSV.
	import_sheet.add_parser(actions)
	return parser


def run_path(args, ws) -> int:
	print(ws.db)
	return 0


def run_shell(args, ws) -> int:
	"""Hand the terminal to sqlite3 and give its exit code back.

	No output is captured: the point is a real interactive prompt when
	stdin is a TTY, and stdout/errors that behave exactly as sqlite3's
	do in a pipe. Missing sqlite3 is reported as a command not found
	(127), the shell's code, rather than a traceback.
	"""
	try:
		# check=False, explicit: sqlite3's own exit code is the result,
		# not something to raise on.
		done = subprocess.run(['sqlite3', str(ws.db), *args.args], check=False)
	except FileNotFoundError:
		print(
			'error: sqlite3 is not installed — install it, or use '
			'`jobtriage db path` with another SQLite client',
			file=sys.stderr,
		)
		return 127
	return done.returncode


def run_schema(args, ws) -> int:
	"""Migrate first, so a fresh workspace gets a schema rather than an
	empty one, then print what is there."""
	version = db.migrate(ws.db, ws.backups)
	conn = db.connect(ws.db)
	try:
		print(f'schema version {version} — {ws.db}')
		for table in _TABLES:
			_print_table(conn, table)
	finally:
		conn.close()
	return 0


def _print_table(conn, table: str) -> None:
	"""One table: aligned columns, then its indexes and unique key.

	The pragmas are read rather than parsed out of db.py's SQL: the
	database's own answer cannot drift from the database, and column
	order inside CREATE TABLE is not a contract anyone should parse.
	The table names are this module's constants, so interpolating them
	into the pragma is not user input.
	"""
	columns = conn.execute(f'PRAGMA table_info({table})').fetchall()
	foreign = {
		row['from']: f'{row["table"]}.{row["to"] or "id"}'
		for row in conn.execute(f'PRAGMA foreign_key_list({table})')
	}
	rows = []
	for column in columns:
		rules = []
		if column['pk']:
			rules.append('PRIMARY KEY')
		if column['notnull']:
			rules.append('NOT NULL')
		if column['dflt_value'] is not None:
			rules.append(f'DEFAULT {column["dflt_value"]}')
		if column['name'] in foreign:
			rules.append(f'→ {foreign[column["name"]]}')
		rows.append(
			(
				column['name'],
				column['type'] or '',
				' '.join(rules),
				_COMMENTS.get(table, {}).get(column['name'], ''),
			)
		)

	name_width = max(len(row[0]) for row in rows)
	type_width = max(len(row[1]) for row in rows)
	rule_width = max(len(row[2]) for row in rows)
	print()
	print(table)
	for name, kind, rules, comment in rows:
		print(
			f'  {name:<{name_width}}  {kind:<{type_width}}  {rules:<{rule_width}}  {comment}'.rstrip()
		)
	for line in _index_lines(conn, table):
		print(f'  {line}')


def _index_lines(conn, table: str) -> list[str]:
	"""The declared indexes and unique constraints, one line each.

	The unique constraint matters most: it is the old cache key, and
	an agent writing SQL should know why a duplicate insert fails.
	Sorted by name so two runs print the same schema.
	"""
	indexes, unique = [], []
	for index in conn.execute(f'PRAGMA index_list({table})'):
		columns = ', '.join(
			column['name'] or '' for column in conn.execute(f'PRAGMA index_info({index["name"]})')
		)
		if index['origin'] == 'u':
			unique.append(columns)
		elif index['origin'] == 'c':
			indexes.append(f'{index["name"]} ({columns})')
	lines = []
	if indexes:
		lines.append('indexes: ' + ', '.join(sorted(indexes)))
	if unique:
		lines.append('unique: ' + '; '.join(sorted(unique)))
	return lines
