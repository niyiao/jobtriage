"""The store: the database's API for the pipeline, the CLI and the TUI.

One SQLite database holds the jobs and their triage state; this module
is the only place that turns its rows into the dicts the rest of the
tool passes around. Everything else goes through here — the pipeline
ingests, the CLI queries, the TUI tracks — and the schema itself is
db.py's (`connect` and `migrate`).

The cache key is unchanged: (title, company, role, year, month),
fold-tolerant on title and company, because the same posting rides
several boards worded slightly differently ("Frontend Engineer" here,
"Front-End Engineer" there), plus the role word that matched and the
year and month it was published. The month in the key is what lets a
repost next month count as new again. An undated job is keyed under
the current month: assumed new, as decided. Keeping the key identical
is what let step 2's migration rebuild the table without re-reading
one job as two.

A row remembers its first sighting (title, published, first_seen). A
later sighting only refreshes the pointer data — url, salary, source —
and last_seen, which doubles as a "probably closed" signal when a job
stops appearing on every board. Status and notes belong to the person
triaging, never to a scrape: ingest does not touch them.

Everything that writes opens its own connection and does so inside one
transaction, so a crash halfway through a scrape leaves the database
exactly as it was. Board health is the one write beside the ingest:
`record_run` appends one row per attempted board as the scrape happens
— before filtering, because a board that fails has no jobs to ingest
and its failure is exactly what the next scrape's notice needs.
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobtriage import db
from jobtriage.relevance import job_key, parse_published
from jobtriage.status import DEFAULT, STATUSES, by_slug

# The database path is the workspace's (ws.db, jobtriage.workspace) —
# every function takes it as an argument; tests pass a tmp_path one.

_KEY_WHERE = 'WHERE title_key = ? AND company_key = ? AND role = ? AND year = ? AND month = ?'

# The columns `--order` may name. Parameter binding cannot cover an
# ORDER BY clause, so this whitelist is the guard against SQL arriving
# through a command-line flag. Published sorts undated last (the `IS
# NULL` term, because SQLite would otherwise put NULLs first in an
# ascending sort and last only by luck in a descending one); every
# order gets an id tiebreak so a page of equal keys is stable instead
# of whatever the query planner feels like.
_ORDERINGS = {
	'published': 'published IS NULL, published DESC, id DESC',
	'first_seen': 'first_seen DESC, id DESC',
	'last_seen': 'last_seen DESC, id DESC',
	'title': 'title COLLATE NOCASE ASC, id ASC',
	'company': 'company COLLATE NOCASE ASC, id ASC',
	'source': 'source COLLATE NOCASE ASC, published DESC, id DESC',
	'salary': 'salary IS NULL, salary DESC, id DESC',
	'status': 'status ASC, published DESC, id DESC',
	'id': 'id DESC',
}


@dataclass(frozen=True)
class IngestResult:
	"""What one scrape added to the database.

	`new` is the list of jobs inserted this time, not a count: the ids
	minted by the insert are on the dicts, which callers and tests use
	to address exactly those jobs. `seen` and `total` are counts because
	nothing downstream needs the rows themselves — a summary line is all
	they feed. `len(new)` is the "x new" in it.
	"""

	new: list[dict]
	seen: int
	total: int


# How many recent runs a board's success rate covers (plan 04's "last
# 10"). Ten is long enough that one flaky morning does not brand a
# board, and short enough that a fixed board looks healthy within a
# couple of scrapes. The totals cover the same window as the rate, so
# every number on the line comes from one set of runs.
HEALTH_WINDOW = 10

# Consecutive failures that make the end of `scrape` point at
# `sources test NAME`: three mornings in a row is a layout change, not
# a bad day.
FAILING_STREAK = 3


@dataclass(frozen=True)
class HealthSummary:
	"""One board's recent runs, for `sources` and scrape's notice.

	`runs`, `failures`, `fetched` and `dropped_invalid` cover the last
	`HEALTH_WINDOW` runs. `streak` counts failures from the newest row
	back to the first success, within that window, so a run that
	recovered yesterday reads 0 even if the board failed for a week
	before. The `last_*` fields describe the single newest run — what a
	person checking "is it down right now?" needs, including the error.
	"""

	source: str
	runs: int
	failures: int
	streak: int
	fetched: int
	dropped_invalid: int
	last_started_at: str
	last_ok: bool
	last_error: str | None
	last_duration_ms: int
	last_fetched: int

	@property
	def success_rate(self) -> float | None:
		"""The fraction of ok runs in the window, or None with no runs."""
		if not self.runs:
			return None
		return (self.runs - self.failures) / self.runs

	def failing(self, streak: int = FAILING_STREAK) -> bool:
		"""Has the board failed every one of its last `streak` runs?"""
		return self.streak >= streak


def _now() -> str:
	"""The one timestamp form the columns use: aware UTC, seconds.

	Mixed precision (a board's date-only here, milliseconds there)
	would make string comparison and ORDER BY lie about order; seconds
	is all the pipeline can observe anyway.
	"""
	return datetime.now(UTC).isoformat(timespec='seconds')


def _open(db_path: str | Path) -> sqlite3.Connection:
	"""Open the database, making sure its schema is current first.

	Any store call can be the first thing a fresh workspace runs —
	`jobs list` in an op folder nobody has scraped into — so opening
	the store is what migrates, rather than every caller remembering
	to. migrate() is a PRAGMA read once the database is current, and
	it is also where a database from a newer build is refused instead
	of written over.
	"""
	db.migrate(db_path)
	return db.connect(db_path)


def _year_month(job: dict) -> tuple[int, int]:
	"""The job's published year and month, or the current month for an
	undated job — the cache's assumption that it's new."""
	posted = parse_published(job.get('published'))
	when = posted or datetime.now(UTC)
	return when.year, when.month


def _cache_keys(job: dict) -> tuple:
	"""The five-part cache key, in _KEY_WHERE order."""
	year, month = _year_month(job)
	return job_key(job['title']), job_key(job['company']), job['role'], year, month


def _normalize_published(published: str | None) -> str | None:
	"""One canonical form for the column: aware UTC, seconds precision.

	The boards' mixed precision (date-only, milliseconds) would make
	string ORDER BY lie about order; one form makes it true. Undated
	stays NULL.
	"""
	posted = parse_published(published)
	if posted is None:
		return None
	return posted.isoformat(timespec='seconds')


def _row_to_job(row: sqlite3.Row) -> dict:
	"""One row as the dict the rest of the tool passes around.

	`locations` is stored as JSON because SQLite has no list type; the
	boundary where that packing is undone is here, so no caller has to
	know how the column is written.
	"""
	job = dict(row)
	job['locations'] = json.loads(job['locations'] or '[]')
	return job


def _total(conn: sqlite3.Connection) -> int:
	"""How many jobs the database holds."""
	return conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]


def ingest(jobs: list[dict], db_path: str | Path) -> IngestResult:
	"""Put these jobs in the database, all in one transaction.

	A job the key has not seen before is inserted with the default
	status (`new`) and gets an id, which is attached to the job dict:
	the id is what the CLI and the TUI address it by from here on. A
	job already in the database is *touched* instead — last_seen, and
	the newest url/salary/source — because a later sighting is better
	pointer data, not a second job. salary keeps its old value when
	the new sighting has none: a board that stopped publishing pay did
	not un-pay the job.

	One transaction is the whole story now: there is no second step to
	fail, so either the whole scrape lands or none of it does.

	jobs is the list relevance.select() returned; its dedup already
	removes most duplicates, but a batch may still fold two job dicts
	onto one key (two boards, two spellings). The second is a touch:
	one row, one job, flagged seen.
	"""
	conn = _open(db_path)
	try:
		if not jobs:
			return IngestResult([], 0, _total(conn))
		now = _now()
		fresh: list[dict] = []
		seen = 0
		batch: set[tuple] = set()
		with conn:  # one transaction: commit on the way out, rollback on error
			for job in jobs:
				keys = _cache_keys(job)
				if keys in batch or _exists(conn, keys):
					_touch(conn, job, keys, now)
					seen += 1
					continue
				batch.add(keys)
				cursor = conn.execute(
					"""INSERT INTO jobs (title, company, role, year, month,
						title_key, company_key, url, published, locations,
						salary, source, first_seen, last_seen, status)
					VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
					(
						job['title'],
						job['company'],
						job['role'],
						*keys[3:],  # year, month
						keys[0],
						keys[1],
						job['url'],
						_normalize_published(job.get('published')),
						json.dumps(job.get('locations') or []),
						job.get('salary'),
						job.get('source'),
						now,
						now,
						DEFAULT,
					),
				)
				job['id'] = cursor.lastrowid
				fresh.append(job)
			total = _total(conn)
		return IngestResult(fresh, seen, total)
	finally:
		conn.close()


def _exists(conn: sqlite3.Connection, keys: tuple) -> bool:
	"""Is this cache key already a row?"""
	return conn.execute(f'SELECT 1 FROM jobs {_KEY_WHERE}', keys).fetchone() is not None


def _touch(conn: sqlite3.Connection, job: dict, keys: tuple, now: str) -> None:
	"""Refresh a row's pointer data on a later sighting.

	Exactly what the old unseen() did, and deliberately the same
	columns: url points at the freshest posting, salary only ever gets
	filled in, source records the board that served it this time, and
	last_seen moves. Title, published, locations, first_seen and the
	person's status/notes stay as first written.
	"""
	conn.execute(
		f'UPDATE jobs SET url = ?, salary = COALESCE(?, salary),'
		f' source = ?, last_seen = ? {_KEY_WHERE}',
		(job['url'], job.get('salary'), job.get('source'), now, *keys),
	)


def record_run(
	db_path: str | Path,
	source: str,
	*,
	started_at: str,
	duration_ms: int = 0,
	ok: bool = True,
	fetched: int = 0,
	dropped_invalid: int = 0,
	error: str | None = None,
) -> None:
	"""Write one board run's numbers for the health surfaces.

	The pipeline calls this once per attempted board — success or
	failure — and passes `started_at` as the run's UTC ISO start rather
	than the write time, so a slow board is placed where it began.
	`ok` is stored as 0/1, the column the plan names; `error` is the
	board's one-line exception and NULL on success. A broken or
	disabled board never reaches here: it did not run.
	"""
	conn = _open(db_path)
	try:
		with conn:
			conn.execute(
				'INSERT INTO source_runs'
				' (source, started_at, duration_ms, ok, fetched, dropped_invalid, error)'
				' VALUES (?, ?, ?, ?, ?, ?, ?)',
				(
					source,
					started_at,
					int(duration_ms),
					int(bool(ok)),
					int(fetched),
					int(dropped_invalid),
					error,
				),
			)
	finally:
		conn.close()


def board_health(db_path: str | Path) -> dict[str, HealthSummary]:
	"""Every board with recorded runs, summarised, keyed by canonical name.

	This is the read side `jobtriage sources` prints (plan 04 step 8)
	and the end of `scrape` consults. The dict follows SQLite's source
	ordering, so two runs and two listings are diffable; a board with
	no rows is simply absent — `health.get(name)` is the caller's
	"never run" case, not an empty summary pretending to be data.
	"""
	conn = _open(db_path)
	try:
		rows = conn.execute(
			'SELECT source, started_at, duration_ms, ok, fetched, dropped_invalid, error'
			' FROM source_runs ORDER BY source, id DESC'
		).fetchall()
	finally:
		conn.close()

	by_source: dict[str, list[sqlite3.Row]] = {}
	for row in rows:
		by_source.setdefault(row['source'], []).append(row)
	return {source: _health(source, runs[:HEALTH_WINDOW]) for source, runs in by_source.items()}


def _health(source: str, runs: Sequence[sqlite3.Row]) -> HealthSummary:
	"""One board's summary from its newest-first window of rows."""
	latest = runs[0]
	streak = 0
	for row in runs:
		if row['ok']:
			break
		streak += 1
	return HealthSummary(
		source=source,
		runs=len(runs),
		failures=sum(1 for row in runs if not row['ok']),
		streak=streak,
		fetched=sum(row['fetched'] for row in runs),
		dropped_invalid=sum(row['dropped_invalid'] for row in runs),
		last_started_at=latest['started_at'],
		last_ok=bool(latest['ok']),
		last_error=latest['error'],
		last_duration_ms=latest['duration_ms'],
		last_fetched=latest['fetched'],
	)


def get(db_path: str | Path, job_id: int) -> dict:
	"""One job by id, every field including notes and status_at.

	KeyError for a missing id, the way a failed lookup behaves
	elsewhere in the tool (status.by_slug included): the CLI turns it
	into "no job 42", and there is no sensible empty job to return.
	"""
	conn = _open(db_path)
	try:
		row = conn.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
		if row is None:
			raise KeyError(f'no job with id {job_id}')
		return _row_to_job(row)
	finally:
		conn.close()


def query(
	db_path: str | Path,
	status: str | None = None,
	since: int | str | None = None,
	source: str | None = None,
	search: str | None = None,
	order: str = 'published',
	limit: int | None = 50,
) -> list[dict]:
	"""The jobs matching every given filter, newest first by default.

	This is the read side the CLI (step 5) and the agent skills (plan
	05) see: filters are optional and independent, `limit=None` means
	"all of them" (export wants that), and every job is the full row.

	- `status` is a slug (`new`, `to_apply`…), validated through the
	  status table so a typo is a named error, never a silent empty
	  list.
	- `since` is a number of days as an int or a string like '7d':
	  the window is measured on published, because that is the job's
	  own age. An undated job falls back to first_seen rather than
	  disappearing — undated is "assumed new" everywhere else in the
	  tool, and a filter that quietly dropped those jobs would make
	  `list --since 1d` disagree with the scrape that just stored them.
	- `search` matches title, company, role or url, case-insensitively
	  (SQLite's LIKE folds ASCII case) and as a substring; `%` and `_`
	  are literal characters, not wildcards, because a search for
	  "C++" must not become a pattern.
	- `order` is one of _ORDERINGS' keys; anything else raises rather
	  than quietly falling back to the default.
	"""
	if order not in _ORDERINGS:
		raise ValueError(f'unknown order {order!r} — choose from {", ".join(_ORDERINGS)}')

	where: list[str] = []
	params: list = []
	if status is not None:
		where.append('status = ?')
		params.append(by_slug(status).slug)
	if source is not None:
		where.append('source = ?')
		params.append(source)
	if since is not None:
		where.append('COALESCE(published, first_seen) >= ?')
		params.append(_cutoff(since))
	if search:
		pattern = _like_pattern(search)
		where.append(
			"(title LIKE ? ESCAPE '\\' OR company LIKE ? ESCAPE '\\'"
			" OR role LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\')"
		)
		params.extend([pattern] * 4)

	sql = 'SELECT * FROM jobs'
	if where:
		sql += ' WHERE ' + ' AND '.join(where)
	sql += f' ORDER BY {_ORDERINGS[order]} LIMIT ?'
	params.append(-1 if limit is None else int(limit))

	conn = _open(db_path)
	try:
		return [_row_to_job(row) for row in conn.execute(sql, params)]
	finally:
		conn.close()


def _cutoff(since: int | str) -> str:
	"""Turn '7d' (or 7) into the ISO timestamp seven days ago.

	Seconds precision and UTC, matching the columns, so the string
	comparison SQLite does is a true chronological one.
	"""
	text = str(since).strip().casefold()
	text = text.removesuffix('d')
	try:
		days = int(text)
	except ValueError:
		raise ValueError(f"since must be a number of days, like '7d' or 7, not {since!r}") from None
	if days < 0:
		raise ValueError(f'since cannot be negative: {since!r}')
	return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec='seconds')


def _like_pattern(search: str) -> str:
	"""The LIKE pattern for a literal substring search.

	The escape character is backslash, so a term that itself holds `%`
	or `_` keeps its meaning as text. SQLite's LIKE is case-insensitive
	for ASCII, which is the case the boards' titles are in.
	"""
	escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
	return f'%{escaped}%'


def set_status(db_path: str | Path, job_id: int, status: str, actor: str = 'cli') -> None:
	"""Move one job to `status`, recording who did it and when.

	The update and its history row go in together: history without
	the job's current status, or the reverse, would make undo (plan
	06) and "when did I apply" lie. `status_at` is the same `at` the
	history row carries, so the row's own timestamp and the last
	entry always agree.

	Setting the status a job already has is a no-op and writes no
	history: the TUI only commits real changes, and a stream of
	old == new rows would bury the actual decision.

	`status` is a slug; an unknown one raises KeyError from the
	status table before anything is written. `actor` is 'tui', 'cli'
	or 'agent' — free text in the column, named in plan 02.
	"""
	conn = _open(db_path)
	try:
		with conn:
			_write_status(conn, job_id, status, actor)
	finally:
		conn.close()


def _write_status(conn: sqlite3.Connection, job_id: int, status: str, actor: str) -> None:
	"""The body of set_status, on a connection the caller controls.

	Separate so Tracker.set_status and the module-level function share
	one implementation, and so the "record the change" logic is
	readable without connection handling around it.
	"""
	by_slug(status)  # KeyError before any write: a typo must not half-land
	row = conn.execute('SELECT status FROM jobs WHERE id = ?', (job_id,)).fetchone()
	if row is None:
		raise KeyError(f'no job with id {job_id}')
	if row['status'] == status:
		return
	at = _now()
	conn.execute(
		'UPDATE jobs SET status = ?, status_at = ? WHERE id = ?',
		(status, at, job_id),
	)
	conn.execute(
		'INSERT INTO status_history (job_id, old, new, at, actor) VALUES (?, ?, ?, ?, ?)',
		(job_id, row['status'], status, at, actor),
	)


def add_note(db_path: str | Path, job_id: int, text: str) -> str:
	"""Append a note to the job, keeping whatever was there.

	Notes accumulate rather than replace: `jobs note 12 "called back"`
	is a diary entry, and the first one ("recruiter said...") is
	usually the one worth keeping. They are separated by a newline so
	the TUI and `jobs show` can print them as written. Returns the
	job's notes after the append.

	KeyError for a missing id, like get().
	"""
	conn = _open(db_path)
	try:
		row = conn.execute('SELECT notes FROM jobs WHERE id = ?', (job_id,)).fetchone()
		if row is None:
			raise KeyError(f'no job with id {job_id}')
		notes = f'{row["notes"]}\n{text}' if row['notes'] else text
		with conn:
			conn.execute('UPDATE jobs SET notes = ? WHERE id = ?', (notes, job_id))
		return notes
	finally:
		conn.close()


def history(db_path: str | Path, job_id: int) -> list[dict]:
	"""One job's status changes, oldest first — the order they were made.

	The TUI's details pane reads this the way `jobs show` does: the
	same four columns, the same `id` tiebreak (two changes in one
	second have to come back in the order they were written, and `at`
	cannot tell them apart). `old` can be NULL on a hand-edited row;
	the caller renders that as an em dash.
	"""
	conn = _open(db_path)
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


def attachments(db_path: str | Path, job_id: int) -> list[dict]:
	"""One job's files, oldest first — plan 05's cover letters.

	Empty for most jobs today, but the pane shows the table rather
	than assuming, exactly as `jobs show` does: the row is the
	contract, and a future agent writing one must appear here without
	this function changing.
	"""
	conn = _open(db_path)
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


def attach(db_path: str | Path, job_id: int, path: str, kind: str = 'cover_letter') -> bool:
	"""Record one file against a job; True when the row is new.

	`path` is the caller's string, already made meaningful: the CLI
	stores a path relative to the op folder when the file lives in or
	under it, and an absolute path otherwise — plan 02's schema comment,
	"where the file lives, in or under the workspace". This module does
	not resolve paths, because it does not know the workspace; it only
	keeps what the caller decided `show` and the TUI will print back.

	Attaching the same path twice is a no-op: a cover-letter skill that
	retries must not leave two rows for one letter, and history-style
	duplicates would print forever. The check is on (job_id, path), not
	on the file, so a second job may legitimately share the same letter.

	KeyError for a missing id, like get() and set_status(); an unknown
	kind is stored as given, because plan 02 leaves the vocabulary open
	('cover_letter' | ...) and a new kind must not need a migration.
	"""
	conn = _open(db_path)
	try:
		with conn:
			row = conn.execute('SELECT 1 FROM jobs WHERE id = ?', (job_id,)).fetchone()
			if row is None:
				raise KeyError(f'no job with id {job_id}')
			existing = conn.execute(
				'SELECT 1 FROM attachments WHERE job_id = ? AND path = ?',
				(job_id, path),
			).fetchone()
			if existing is not None:
				return False
			conn.execute(
				'INSERT INTO attachments (job_id, kind, path, created_at) VALUES (?, ?, ?, ?)',
				(job_id, kind, path, _now()),
			)
			return True
	finally:
		conn.close()


def counts(db_path: str | Path) -> dict[str, int]:
	"""How many jobs carry each status, zeros included.

	The zero entries matter: the CLI prints the whole status table and
	the TUI paints every filter, and a missing key would mean "this
	status does not exist" instead of "nothing is in it". The order is
	the status table's cycle order, because a dict keeps insertion
	order and callers display it as is.
	"""
	result = {entry.slug: 0 for entry in STATUSES}
	conn = _open(db_path)
	try:
		for row in conn.execute('SELECT status, COUNT(*) FROM jobs GROUP BY status'):
			if row[0] in result:  # a slug from a newer build: not ours to show
				result[row[0]] = row[1]
	finally:
		conn.close()
	return result


class Tracker:
	"""The database as the triage TUI reads and writes it.

	load() reads every job once with its id attached, and set_status()
	lands one decision at a time. A SQLite write is a millisecond, so
	these are plain synchronous calls — no writer thread, no queue, no
	flush on quit — and a failure is a message, not a lost decision.
	"""

	def __init__(self, db_path: str | Path):
		self._db_path = db_path

	def load(self) -> list[dict]:
		"""Every job, newest first, as the TUI consumes it.

		`published` is 'undated' for a job with no date: the TUI's
		sort buckets that string last. `status` is the slug — the
		database's value, not the label — because that is what
		set_status takes and what the status table looks up.
		"""
		jobs = query(self._db_path, limit=None)
		for job in jobs:
			job['published'] = job['published'] or 'undated'
		return jobs

	def set_status(self, job_id: int, status: str, actor: str = 'tui') -> None:
		"""Land one triage decision on the job the TUI has selected.

		`actor` defaults to 'tui' — this is the keyboard's path; the
		CLI and agents call store.set_status, which defaults to 'cli'.
		"""
		set_status(self._db_path, job_id, status, actor)

	def history(self, job_id: int) -> list[dict]:
		"""The selected job's status changes, for the details pane."""
		return history(self._db_path, job_id)

	def attachments(self, job_id: int) -> list[dict]:
		"""The selected job's files, for the details pane."""
		return attachments(self._db_path, job_id)

	def add_note(self, job_id: int, text: str) -> str:
		"""Append a note and return the job's notes after the append.

		The TUI's `m` lands here; `jobs note` still calls the module
		function directly. The return value is what the pane shows
		without another SELECT.
		"""
		return add_note(self._db_path, job_id, text)
