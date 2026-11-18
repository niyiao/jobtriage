"""`jobtriage show` — one job in full, for a person or an agent.

store.get returns the row; the two child tables (status history and
attachments) have no store reader yet — plan 02's undo needs one, but
this step only needs to look — so they are read here through db.connect,
the schema module's connection, instead of teaching the command raw
SQLite. The JSON shape is the job's own fields plus `history` and
`attachments`, so an agent gets one object and never has to join.

`--json` keeps nulls (published and salary can be null); the human
output substitutes an em dash for them and 'undated' for a missing
published date, matching the rest of the tool's language.
"""

import argparse
import sys

from jobtriage import db, store
from jobtriage.cli.output import error_text, print_json

# The columns shown on their own line at the end rather than in the
# field table: notes because they can span lines, the rest because they
# are lists of rows, not job fields.
_NOTE_COLUMNS = ('notes',)


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser('show', help='show one job in full: every field, notes, history')
	parser.add_argument(
		'id',
		type=int,
		metavar='ID',
		help='the job id, as `jobtriage list --json` prints it',
	)
	parser.add_argument(
		'--json',
		action='store_true',
		help='print the job, its history and attachments as one JSON object',
	)
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	try:
		job = store.get(ws.db, args.id)
	except KeyError as bad:
		print(f'error: {error_text(bad)}', file=sys.stderr)
		return 1
	# store.get has just migrated the database, so the child tables exist
	history = _history(ws.db, args.id)
	attachments = _attachments(ws.db, args.id)
	if args.json:
		payload = dict(job)
		payload['history'] = history
		payload['attachments'] = attachments
		print_json(payload)
		return 0
	_print(job, history, attachments)
	return 0


def _history(db_path, job_id: int) -> list[dict]:
	"""One job's status changes, oldest first — the order they were made.

	`id` is the tiebreak, not `at`: two changes in the same second (a
	script moving new → to_apply → applied) must come back in the order
	they were written, and the timestamp cannot tell them apart.
	"""
	conn = db.connect(db_path)
	try:
		return [
			dict(row)
			for row in conn.execute(
				'SELECT old, new, at, actor FROM status_history WHERE job_id = ? ORDER BY id',
				(job_id,),
			)
		]
	finally:
		conn.close()


def _attachments(db_path, job_id: int) -> list[dict]:
	"""One job's files (plan 05). Empty today, but the table is the
	contract `show` advertises, so query it rather than assume."""
	conn = db.connect(db_path)
	try:
		return [
			dict(row)
			for row in conn.execute(
				'SELECT kind, path, created_at FROM attachments WHERE job_id = ? ORDER BY id',
				(job_id,),
			)
		]
	finally:
		conn.close()


def _print(job: dict, history: list[dict], attachments: list[dict]) -> None:
	"""The human view: a header, every field, then the three sections."""
	print(f'{job["title"]} — {job["company"]}')
	fields = [(name, value) for name, value in job.items() if name not in _NOTE_COLUMNS]
	width = max(len(name) for name, _ in fields)
	for name, value in fields:
		print(f'  {name:<{width}}  {_value(value)}')

	print()
	_notes(job.get('notes'))
	print()
	_history_lines(history)
	print()
	_attachment_lines(attachments)


def _value(value) -> str:
	"""One field as a line: nulls and empty lists are an em dash."""
	if value is None:
		return '—'
	if isinstance(value, (list, tuple)):
		return ', '.join(str(item) for item in value) or '—'
	return str(value)


def _notes(notes: str | None) -> None:
	print('notes')
	if not notes:
		print('  none')
		return
	for line in notes.splitlines():
		print(f'  {line}')


def _history_lines(history: list[dict]) -> None:
	print('history')
	if not history:
		print('  none')
		return
	for change in history:
		print(f'  {change["at"]}  {_value(change["old"])} → {change["new"]}  ({change["actor"]})')


def _attachment_lines(attachments: list[dict]) -> None:
	print('attachments')
	if not attachments:
		print('  none')
		return
	for attachment in attachments:
		print(f'  {attachment["kind"]}: {attachment["path"]}  ({attachment["created_at"]})')
