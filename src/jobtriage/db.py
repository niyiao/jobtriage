"""The database: one connection helper and the migration runner.

The database is the single source of truth (plan 02): the jobs, their
status, every status change, and the files an application produced.
Every module that touches it takes a path as an argument — the
workspace's `ws.db` — exactly as the old seen-cache did.

`connect` sets the two pragmas everything depends on:

- WAL, so a scrape can write while the TUI reads and an agent runs
  `sqlite3` against the same file. The default rollback journal gives
  one writer exclusive access, which turns a long scrape into a TUI
  that waits (or a "database is locked" error).
- foreign_keys, which SQLite leaves off: status_history and
  attachments name a job, and a child row whose parent vanished is a
  bug, not data.

The busy timeout is the third half of running scrape and TUI at once:
WAL still lets only one writer at a time, so a writer that finds the
lock taken waits instead of failing immediately.

Migrations are numbered and keyed on `PRAGMA user_version`, which is
part of the database header and moves inside the transaction, so a
failed migration leaves the version where it was. Each migration runs
in one transaction and, before it changes an existing schema, is
preceded by a file backup — a rollback undoes the migration, but only
the backup survives a bug in the migration itself.

Migration 1 is the schema the 0.2 seen-cache shipped, frozen with
`CREATE TABLE IF NOT EXISTS` so a database written before the runner
existed is adopted rather than recreated. Migration 2 is the rebuild
that gives every job an id and a status; SQLite cannot change a
primary key in place, so it is create-copy-drop-rename. Migration 3
adds `source_runs`, the board-health history plan 04 records on every
scrape. Never edit a migration that has shipped — add the next number
instead, because someone's database has already run the old one.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from jobtriage.workspace import Workspace

# The schema version this build understands. Tests and `jobs db schema`
# report it; migrate() returns it once the database is there.
LATEST = 3

# Migration 1: exactly what store.py wrote before the tracker existed
# (title/company/role/year/month and the fold-tolerant keys as the
# primary key). Frozen history — see the module docstring.
SCHEMA_VERSION_1 = """
CREATE TABLE IF NOT EXISTS jobs (
	title TEXT NOT NULL,
	company TEXT NOT NULL,
	role TEXT NOT NULL,
	year INTEGER NOT NULL,
	month INTEGER NOT NULL,
	title_key TEXT NOT NULL,
	company_key TEXT NOT NULL,
	url TEXT NOT NULL,
	published TEXT,
	locations TEXT NOT NULL DEFAULT '[]',
	salary TEXT,
	source TEXT,
	first_seen TEXT NOT NULL,
	last_seen TEXT NOT NULL,
	PRIMARY KEY (title_key, company_key, role, year, month)
)
"""

# Migration 2: the rebuild. Column order follows plan 02's schema: the
# cache's columns, the three new ones at the end, and the old five-part
# key demoted to a UNIQUE constraint so a re-scrape still cannot insert
# the same posting twice. The copy names its columns so the default on
# `status` fills every pre-existing job with 'new' — they were all
# untriaged.
_REBUILD_JOBS = (
	"""
	CREATE TABLE jobs_new (
		id INTEGER PRIMARY KEY,
		title TEXT NOT NULL,
		company TEXT NOT NULL,
		role TEXT NOT NULL,
		year INTEGER NOT NULL,
		month INTEGER NOT NULL,
		title_key TEXT NOT NULL,
		company_key TEXT NOT NULL,
		url TEXT NOT NULL,
		published TEXT,
		locations TEXT NOT NULL DEFAULT '[]',
		salary TEXT,
		source TEXT,
		first_seen TEXT NOT NULL,
		last_seen TEXT NOT NULL,
		status TEXT NOT NULL DEFAULT 'new',
		status_at TEXT,
		notes TEXT,
		UNIQUE (title_key, company_key, role, year, month)
	)
	""",
	"""
	INSERT INTO jobs_new (title, company, role, year, month,
			title_key, company_key, url, published, locations,
			salary, source, first_seen, last_seen)
	SELECT title, company, role, year, month,
			title_key, company_key, url, published, locations,
			salary, source, first_seen, last_seen
	FROM jobs
	ORDER BY first_seen, rowid
	""",
	'DROP TABLE jobs',
	'ALTER TABLE jobs_new RENAME TO jobs',
	'CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status)',
	'CREATE INDEX IF NOT EXISTS idx_jobs_published ON jobs (published)',
	'CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs (first_seen)',
	'CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs (source)',
	# History is what makes undo and "when did I apply" possible, and it
	# costs one row per change. `id` is not in the plan's column list but
	# gives undo two changes in the same second a definite order; the
	# cascade means deleting a job cannot orphan its past.
	"""
	CREATE TABLE status_history (
		id INTEGER PRIMARY KEY,
		job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
		old TEXT,
		new TEXT NOT NULL,
		at TEXT NOT NULL,
		actor TEXT NOT NULL
	)
	""",
	"""
	CREATE INDEX IF NOT EXISTS idx_status_history_job
		ON status_history (job_id, at)
	""",
	# Attachments are plan 05's cover letters and the like: the file
	# lives in the workspace, the row says which job it belongs to.
	"""
	CREATE TABLE attachments (
		id INTEGER PRIMARY KEY,
		job_id INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
		kind TEXT NOT NULL,
		path TEXT NOT NULL,
		created_at TEXT NOT NULL
	)
	""",
)


def _migration_1(db: sqlite3.Connection) -> None:
	"""Today's table, created only if it is not already there — so both
	a fresh database and one from the 0.2 cache land on version 1."""
	db.execute(SCHEMA_VERSION_1)


def _migration_2(db: sqlite3.Connection) -> None:
	"""Rebuild the cache as the tracker's jobs table, keeping every row.

	The old primary key becomes a UNIQUE constraint, and the rows are
	copied in first_seen order so the new ids are deterministic for a
	given database — "stable" across runs, not just unique.
	"""
	for statement in _REBUILD_JOBS:
		db.execute(statement)


# Migration 3: one row per board run (plan 04's board health). No
# foreign keys: a run is history about a name, not a row in another
# table, and a plugin may be removed while its history stays useful for
# diagnosis. `id` orders runs that share a second — the pipeline records
# a handful of boards in the same as_completed pass — and `ok` is 0/1
# because the plan's column list calls for a flag, not a word.
_SOURCE_RUNS = (
	"""
	CREATE TABLE source_runs (
		id INTEGER PRIMARY KEY,
		source TEXT NOT NULL,
		started_at TEXT NOT NULL,
		duration_ms INTEGER NOT NULL,
		ok INTEGER NOT NULL,
		fetched INTEGER NOT NULL,
		dropped_invalid INTEGER NOT NULL,
		error TEXT
	)
	""",
	"""
	CREATE INDEX IF NOT EXISTS idx_source_runs_source
		ON source_runs (source, started_at)
	""",
)


def _migration_3(db: sqlite3.Connection) -> None:
	"""Add the board-health history table."""
	for statement in _SOURCE_RUNS:
		db.execute(statement)


_MIGRATIONS = {1: _migration_1, 2: _migration_2, 3: _migration_3}


def connect(db_path: str | Path) -> sqlite3.Connection:
	"""Open the database the way every caller wants it.

	WAL, foreign keys and a busy timeout — the reasons are in the
	module docstring — plus sqlite3.Row, so callers read `row['id']`
	instead of remembering column positions. The file is created on
	first connect; the schema is migrate()'s job, not this one's.
	"""
	db = sqlite3.connect(db_path)
	db.row_factory = sqlite3.Row
	db.execute('PRAGMA journal_mode = WAL')
	db.execute('PRAGMA foreign_keys = ON')
	db.execute('PRAGMA busy_timeout = 5000')
	return db


def user_version(db: sqlite3.Connection) -> int:
	"""The schema version in the database header."""
	return db.execute('PRAGMA user_version').fetchone()[0]


def schema_version(db_path: str | Path) -> int:
	"""The schema version of a database file `--version`/doctor read.

	Unlike migrate(), this never creates or changes anything: the file
	must already exist, and the caller wants the number the tool would
	be opening, not a migration. A file from a newer build answers with
	a higher number, which is exactly what the caller must be able to
	say.
	"""
	database = connect(db_path)
	try:
		return user_version(database)
	finally:
		database.close()


def migrate(db_path: str | Path, backups_dir: str | Path | None = None) -> int:
	"""Bring the database at `db_path` to LATEST and return that version.

	Running it twice is a no-op: the version is the database's, not the
	process's, and the loop simply has nothing left to apply.

	`backups_dir` defaults to the state folder beside the database,
	because a database lives at a workspace root (`Workspace.db`);
	callers that hold a Workspace pass `ws.backups` explicitly. A
	database from a newer build raises rather than guessing: its
	migrations may have changed columns this build does not know, and
	writing over them would be worse than refusing to start.
	"""
	path = Path(db_path)
	backups = Path(backups_dir) if backups_dir is not None else Workspace(path.parent).backups
	db = connect(path)
	try:
		version = user_version(db)
		if version > LATEST:
			raise RuntimeError(
				f'{path} is at schema version {version}, but this version of '
				f'jobtriage understands up to {LATEST} — upgrade jobtriage'
			)
		if version == 0 and _has_table(db, 'jobs'):
			# A database written before the runner existed: the table is
			# migration 1's, so stamp it instead of creating it again.
			_set_user_version(db, 1)
			db.commit()
			version = 1
		while version < LATEST:
			version = _apply(db, version, backups)
		return version
	finally:
		db.close()


def _apply(db: sqlite3.Connection, version: int, backups: Path) -> int:
	"""Run one migration in a transaction and move the version with it.

	The backup is taken outside the transaction, before it starts: it
	must hold the pre-migration file, and a transaction around a
	backup of itself is not a thing SQLite can give us.
	"""
	target = version + 1
	if target > 1:  # migration 1 creates a schema; there is nothing to lose
		_backup(db, _backup_path(backups, version))
	db.execute('BEGIN IMMEDIATE')
	try:
		_MIGRATIONS[target](db)
		_set_user_version(db, target)
		db.commit()
	except BaseException:
		db.rollback()
		raise
	return target


def _backup(db: sqlite3.Connection, target: Path) -> None:
	"""Write a consistent copy of the live database to `target`.

	The backup API, not a file copy: in WAL mode the newest rows may
	still be in the -wal file, and copying jobtriage.db alone would
	restore a database that is missing exactly the recent history a
	backup is for.
	"""
	target.parent.mkdir(parents=True, exist_ok=True)
	copy = sqlite3.connect(target)
	try:
		db.backup(copy)
	finally:
		copy.close()


def _backup_path(backups: Path, version: int) -> Path:
	"""`.jobtriage/backups/jobs-<version>-<timestamp>.db`.

	The version is the schema the backup *is*, not the one being
	applied, so a file's name says what restoring it gives you. UTC and
	filesystem-safe (no colons), so the names sort in the order they
	were taken on every platform.
	"""
	stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
	return backups / f'jobs-{version}-{stamp}.db'


def _has_table(db: sqlite3.Connection, name: str) -> bool:
	row = db.execute(
		'SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?',
		('table', name),
	).fetchone()
	return row is not None


def _set_user_version(db: sqlite3.Connection, version: int) -> None:
	"""PRAGMA values cannot be bound; the value is ours, an int."""
	db.execute(f'PRAGMA user_version = {int(version)}')
