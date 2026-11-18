"""`jobtriage db import-sheet` — the one-time move off the Google Sheet.

Plan 02 retires the sheet, but its rows are the only record of the
triage already done: which jobs were skipped, which were queued, which
were applied to. Google's API is not worth a dependency for one read,
so the bridge is a hand-downloaded CSV — File → Download → CSV in the
spreadsheet — and this module is the only thing that understands it.
The sheet's header row is mirrored below rather than imported, so the
sheet module's deletion never touched the import.

The match is deliberately conservative, in this order:

- `url`, exact. The url column came from the database in the first
  place, so a row that still has it is that row, even if the title was
  edited by hand in the spreadsheet.
- `(job_key(title), job_key(company))`, the fold-tolerant identity the
  cache has always used. It catches a posting whose url moved. More
  than one candidate is *ambiguous* — two roles at the same company
  share the pair — and picking one would move a status onto the wrong
  job, so ambiguous rows are reported and left alone rather than
  guessed at.

A row whose url was already handled in this run is a no-op, so an
export with duplicates (or the same job appended twice) is harmless.
Re-running the command is harmless too: set_status writes nothing when
the status already matches, and a note already on the job is not
appended a second time. The sheet never had a notes column, so a real
export has no notes to duplicate in the first place.

Only the columns the job needs are read. `published`, `locations`,
`salary` and `source` are used for a row that has no match;
`apply status` is dropped (plan 02's open question: it was seeded
`none` and never used). Columns a spreadsheet owner added are ignored,
except `notes`: the sheet did not write one, but a hand-kept CSV can,
and a note is exactly what an import must not silently lose.

The one write that needs explaining is `source`. A row with no match
was not scraped by this database, so recording a board would be a lie
and would pollute `list --source`; those rows are stamped
'sheet-import', which also lets a later reader tell which jobs came
over in the migration. A matched row keeps the source it already had —
the sheet's copy of it is older than the database's.
"""

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

from jobtriage import store
from jobtriage.cli.output import error_text
from jobtriage.relevance import job_key
from jobtriage.status import DEFAULT, UnknownSheetStatus, from_sheet

# The sheet's header row, in the order sheet.py wrote it. The importer
# matches rows to jobs by name, so a hand-reordered or re-cased export
# still works; this tuple is also the message for a foreign header.
SHEET_COLUMNS = (
	'title',
	'company',
	'published',
	'locations',
	'salary',
	'role',
	'source',
	'url',
	'status',
	'apply status',
)

# Without these a row cannot be matched or inserted, and a status is
# the whole point of the migration: an export missing one is a
# different file, not a sheet with empty cells, and is refused.
ESSENTIAL = ('title', 'company', 'role', 'url', 'status')

# What an inserted row is called in `source`. See the module docstring.
SOURCE = 'sheet-import'


@dataclass
class ImportResult:
	"""What one run did, in the words the command prints.

	`problems` and `unknown` are lines, not structured records: they
	are read once by a person fixing the CSV, and there is no second
	consumer to keep a schema for.
	"""

	matched: int = 0
	inserted: int = 0
	skipped: int = 0
	problems: list[str] = field(default_factory=list)
	unknown: list[str] = field(default_factory=list)
	apply_status_ignored: int = 0
	dry_run: bool = False


def read_rows(csv_path: str | Path) -> list[tuple[int, dict[str, str]]]:
	"""The data rows of a sheet export, each with its spreadsheet row.

	Row numbers start at 2 because the header is row 1 — the number
	the person sees in the spreadsheet, which is what they need to go
	fix a row this command reports. Header names are folded and
	stripped, cells are stripped, a short row's missing cells read as
	empty and a long row's extras are ignored.

	`utf-8-sig` is not paranoia: Google's export carries a BOM, and an
	unfolded BOM on 'title' would look like a missing column. Blank
	separator lines (no cells at all) are skipped rather than counted
	as rows.

	ValueError for a file that cannot be this export — absent, empty,
	or missing an essential column — so the caller can print one line.
	"""
	path = Path(csv_path)
	if not path.is_file():
		raise ValueError(
			f'{path} is not a file — download the old sheet first '
			'(File → Download → CSV) and pass that file'
		)
	try:
		handle = path.open(newline='', encoding='utf-8-sig')
	except OSError as bad:
		raise ValueError(f'{path} cannot be read: {bad.strerror or bad}') from None
	with handle:
		rows = csv.reader(handle)
		header = next(rows, None)
		if header is None:
			raise ValueError(
				f"{path} is empty — expected the sheet's header row: {', '.join(SHEET_COLUMNS)}"
			)
		names = [cell.strip().casefold() for cell in header]
		missing = [name for name in ESSENTIAL if name not in names]
		if missing:
			named = ', '.join(repr(name) for name in missing)
			raise ValueError(
				f'{path} is missing the {named} column '
				f'(the header row should name {", ".join(SHEET_COLUMNS)}; '
				'extra columns are fine)'
			)
		data: list[tuple[int, dict[str, str]]] = []
		for number, cells in enumerate(rows, start=2):
			if not any(cell.strip() for cell in cells):
				continue
			cells = [cell.strip() for cell in cells]
			data.append(
				(
					number,
					{
						name: cells[index] if index < len(cells) else ''
						for index, name in enumerate(names)
						if name
					},
				)
			)
	return data


def import_sheet(csv_path: str | Path, db_path: str | Path, dry_run: bool = False) -> ImportResult:
	"""Match every row to a job, set its status, insert the rest.

	The database is read once and indexed twice — by url and by the
	folded (title, company) pair — because a query per row would be
	hundreds of round trips for a one-time command. Rows inserted
	during the run join both indexes as it goes, so a later row for
	the same job lands on the row just written instead of colliding
	with the unique key.

	`dry_run` decides between reading-and-reporting and writing: it
	does not even create a missing database, so it leaves the
	filesystem exactly as it found it.
	"""
	result = ImportResult(dry_run=dry_run)
	rows = read_rows(csv_path)
	existing = [] if dry_run and not Path(db_path).exists() else store.query(db_path, limit=None)
	by_url: dict[str, list[dict]] = {}
	by_key: dict[tuple[str, str], list[dict]] = {}
	for job in existing:
		by_url.setdefault(job['url'], []).append(job)
		by_key.setdefault((job['title_key'], job['company_key']), []).append(job)

	handled_urls: set[str] = set()
	for number, row in rows:
		if row.get('apply status', '').casefold() not in ('', 'none'):
			result.apply_status_ignored += 1

		url = row['url']
		title = row.get('title', '')
		if not title or not url:
			# the sheet kept separator rows and hand-written totals; a
			# row without both never was a job (sheet.Tracker skipped
			# them too), so it is skipped rather than inserted empty
			result.skipped += 1
			result.problems.append(f'row {number}: no title or url — skipped')
			continue
		if url in handled_urls:
			result.skipped += 1
			result.problems.append(f'row {number}: already imported in this file (same url)')
			continue

		job, ambiguous = _match(row, by_url, by_key)
		if ambiguous:
			result.skipped += 1
			result.problems.append(
				f'row {number}: ambiguous — {_describe(ambiguous)} share '
				f'{row["title"]} / {row["company"]}; left alone'
			)
			continue

		status, known = _status(row, number, result)
		if job is None:
			result.inserted += 1
			inserted = (
				{'id': None, 'title': row['title'], 'company': row['company']}
				if dry_run
				else _insert(db_path, row, status)
			)
			by_url.setdefault(url, []).append(inserted)
			by_key.setdefault((job_key(row['title']), job_key(row['company'])), []).append(inserted)
		else:
			result.matched += 1
			if not dry_run:
				if known:
					store.set_status(db_path, job['id'], status, actor='import')
				_append_note(db_path, job, row.get('notes', ''))
		handled_urls.add(url)
	return result


def _match(
	row: dict[str, str],
	by_url: dict[str, list[dict]],
	by_key: dict[tuple[str, str], list[dict]],
) -> tuple[dict | None, list[dict]]:
	"""The one job this row means, or the candidates that make it ambiguous.

	Url first, exactly: it is the strongest identity the sheet and the
	database share. The folded (title, company) pair is the fallback
	for a moved url. `(None, [])` means no match — a new job;
	`(None, candidates)` means the fallback found more than one, and
	the caller must report rather than pick.
	"""
	hits = by_url.get(row['url'], [])
	if hits:
		return (hits[0], []) if len(hits) == 1 else (None, hits)
	hits = by_key.get((job_key(row['title']), job_key(row['company'])), [])
	if len(hits) == 1:
		return hits[0], []
	return None, hits


def _describe(jobs: list[dict]) -> str:
	"""'2 jobs (ids 4, 9)' for an ambiguous row's message.

	An id is None only for a row this dry run has not written, and
	saying so is less confusing than a bare 'None'.
	"""
	ids = ', '.join('not written yet' if job.get('id') is None else str(job['id']) for job in jobs)
	return f'{len(jobs)} jobs (ids {ids})'


def _status(row: dict[str, str], number: int, result: ImportResult) -> tuple[str | None, bool]:
	"""The row's status as a slug, and whether it may be written.

	An unknown cell is listed and the row is still imported — its
	title, url and notes are not in doubt, only the cell is — but the
	status is left alone (or DEFAULT on an insert), because a guess
	would put a job the person had already applied to back in the
	queue. That is why the second value exists rather than returning
	DEFAULT for an unknown value.
	"""
	try:
		return from_sheet(row.get('status')), True
	except UnknownSheetStatus:
		result.unknown.append(f'row {number}: {row.get("status")!r} is not a known status — left alone')
		return None, False


def _insert(db_path: str | Path, row: dict[str, str], status: str | None) -> dict:
	"""Insert a row that matched nothing, the way the pipeline would.

	store.ingest is the insert path on purpose: it computes the
	five-part cache key, parses the date into the year/month key (an
	'undated' cell becomes this month, the pipeline's assumption) and
	normalises published. A second implementation here would drift
	from all three. Its status and note come separately because
	ingest deliberately never touches what belongs to the person
	triaging.
	"""
	job = {
		'title': row['title'],
		'company': row['company'],
		'role': row.get('role', ''),
		'url': row['url'],
		'published': _published(row.get('published')),
		'locations': _locations(row.get('locations')),
		'salary': row.get('salary') or None,
		'source': SOURCE,
	}
	result = store.ingest([job], db_path)
	if not result.new:
		# Cannot happen for a row _match() called unmatched — ingest
		# checks the same key — but a silent loss of the status is
		# worse than a named failure.
		raise ValueError(
			f'could not insert {job["title"]!r}: a job with its key appeared between matching and writing'
		)
	inserted = result.new[0]
	store.set_status(db_path, inserted['id'], status or DEFAULT, actor='import')
	_append_note(db_path, inserted, row.get('notes', ''))
	return inserted


def _published(cell: str | None) -> str | None:
	"""The published cell as ingest's input: a date, or nothing.

	The sheet wrote a date or the word 'undated'. 'undated' must reach
	ingest as no date rather than as an unparseable string — the same
	result either way, but this way the current-month key is the
	documented path, not a parse failure.
	"""
	if not cell or cell.casefold() == 'undated':
		return None
	return cell


def _locations(cell: str | None) -> list[str]:
	"""The locations cell back into a list.

	Lossy by construction: the sheet wrote ', '.join(locations), so a
	board location that itself held a comma comes back as two. There
	is no better reading of the cell, and the value is display data,
	not part of a job's identity.
	"""
	if not cell:
		return []
	return [part.strip() for part in cell.split(',') if part.strip()]


def _append_note(db_path: str | Path, job: dict, note: str) -> None:
	"""Append a CSV note unless the job already carries that line.

	Notes accumulate (store.add_note), so a second import must not
	grow the diary: line equality is the unit add_note joins on, and
	the sheet export itself has no notes column to supply in the
	first place.
	"""
	if not note or note in (job.get('notes') or '').splitlines():
		return
	store.add_note(db_path, job['id'], note)


def add_parser(actions: argparse._SubParsersAction) -> argparse.ArgumentParser:
	"""Register `db import-sheet` on the db subcommand's subparsers."""
	parser = actions.add_parser(
		'import-sheet',
		help='import the old Google Sheet CSV (one-time, then delete)',
	)
	parser.add_argument(
		'file',
		metavar='FILE',
		help='the sheet downloaded as CSV (File → Download → CSV)',
	)
	parser.add_argument(
		'--dry-run',
		action='store_true',
		help='print what would happen and write nothing',
	)
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	"""Import the file, or report what it would do; 2 for a bad file.

	A bad export is a usage error, not a missing job, so it exits 2
	the way a bad flag does, with one line and no traceback.
	"""
	try:
		result = import_sheet(args.file, ws.db, dry_run=args.dry_run)
	except (OSError, ValueError, csv.Error) as bad:
		print(f'error: {error_text(bad)}', file=sys.stderr)
		return 2
	_print(result)
	return 0


def _print(result: ImportResult) -> None:
	"""The summary first — the only line a clean import prints — then
	the rows someone has to look at, each with its spreadsheet row."""
	if result.dry_run:
		print('dry run — nothing written')
	print(f'matched {result.matched} · inserted {result.inserted} · skipped {result.skipped}')
	if result.problems:
		print('skipped:')
		for line in result.problems:
			print(f'  {line}')
	if result.unknown:
		print('unknown status:')
		for line in result.unknown:
			print(f'  {line}')
	if result.apply_status_ignored:
		print(
			"note: ignored the 'apply status' column on "
			f'{result.apply_status_ignored} row(s) — it was never used'
		)
