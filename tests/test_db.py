"""Tests for the database layer: the connection pragmas a scrape and
the TUI both rely on, and the migrations that turn the 0.2 seen-cache
into the tracked-jobs schema (plan 02).

The old schema is written out here instead of imported from store:
it is history. The migration has to keep working on a database file
written by the shipped version, whatever store.py looks like after
step 3 rewrites it.
"""

import sqlite3

import pytest

from jobtriage import db

# Exactly the table store.py creates today, frozen at the moment the
# migration was written.
TODAY_SCHEMA = """
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

COLUMNS = (
	'id, title, company, role, year, month, title_key, company_key, '
	'url, published, locations, salary, source, first_seen, last_seen, '
	'status, status_at, notes'
)


def _row(**overrides):
	"""One row in the shape the 0.2 cache wrote."""
	row = {
		'title': 'Frontend Engineer',
		'company': 'Acme',
		'role': 'frontend',
		'year': 2026,
		'month': 11,
		'url': 'https://example.com/job',
		'published': '2026-11-10T12:00:00+00:00',
		'locations': '["Worldwide"]',
		'salary': None,
		'source': 'test',
		'first_seen': '2026-11-10T13:00:00+00:00',
		'last_seen': '2026-11-10T13:00:00+00:00',
	}
	row.update(overrides)
	# The fold-tolerant keys follow the title and company unless a test
	# deliberately sets them, so changing one title does not collide with
	# the five-part primary key.
	row.setdefault('title_key', row['title'].lower())
	row.setdefault('company_key', row['company'].lower())
	return row


def _make_today_db(path, rows=()):
	"""A database as the shipped 0.2 store leaves it: today's table,
	user_version still 0, no migration runner has touched it."""
	conn = sqlite3.connect(path)
	conn.execute(TODAY_SCHEMA)
	for row in rows:
		conn.execute(
			"""INSERT INTO jobs (title, company, role, year, month,
				title_key, company_key, url, published, locations,
				salary, source, first_seen, last_seen)
			VALUES (:title, :company, :role, :year, :month,
				:title_key, :company_key, :url, :published, :locations,
				:salary, :source, :first_seen, :last_seen)""",
			row,
		)
	conn.commit()
	conn.close()


def _version(path):
	conn = sqlite3.connect(path)
	try:
		return conn.execute('PRAGMA user_version').fetchone()[0]
	finally:
		conn.close()


def _columns(path):
	conn = sqlite3.connect(path)
	try:
		return {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}
	finally:
		conn.close()


def test_fresh_database_lands_at_the_latest_version(db_path, tmp_path):
	assert db.migrate(db_path, tmp_path / 'backups') == db.LATEST
	assert _version(db_path) == db.LATEST

	conn = sqlite3.connect(db_path)
	try:
		tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
		assert {'jobs', 'status_history', 'attachments', 'source_runs'} <= tables
		pk = [row[1] for row in conn.execute('PRAGMA table_info(jobs)') if row[5]]
		assert pk == ['id']
	finally:
		conn.close()

	assert {'id', 'status', 'status_at', 'notes'} <= _columns(db_path)


def test_connect_sets_wal_foreign_keys_and_a_row_factory(db_path):
	conn = db.connect(db_path)
	try:
		assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
		assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
		assert conn.row_factory is sqlite3.Row
	finally:
		conn.close()


def test_today_database_migrates_without_losing_rows(db_path, tmp_path):
	"""A version-0 database with the table present is treated as version
	1, then rebuilt: same rows, same first_seen, new ids, status 'new'."""
	rows = [
		_row(title='Oldest', first_seen='2026-11-01T08:00:00+00:00'),
		_row(
			title='Middle',
			first_seen='2026-11-05T09:00:00+00:00',
			salary='$100k',
			url='https://example.com/middle',
		),
		_row(title='Newest', first_seen='2026-11-09T10:00:00+00:00'),
	]
	_make_today_db(db_path, rows)
	assert _version(db_path) == 0

	assert db.migrate(db_path, tmp_path / 'backups') == db.LATEST

	conn = db.connect(db_path)
	try:
		got = conn.execute(f'SELECT {COLUMNS} FROM jobs ORDER BY id').fetchall()
		assert len(got) == len(rows)
		assert [row['title'] for row in got] == ['Oldest', 'Middle', 'Newest']
		assert [row['first_seen'] for row in got] == [row['first_seen'] for row in rows]
		assert len({row['id'] for row in got}) == len(rows)
		assert all(row['status'] == 'new' for row in got)
		assert all(row['status_at'] is None and row['notes'] is None for row in got)
		assert got[1]['salary'] == '$100k'
		assert got[1]['url'] == 'https://example.com/middle'
		assert got[0]['locations'] == '["Worldwide"]'
	finally:
		conn.close()

	# The old five-part key is still a constraint, so a re-scrape cannot
	# insert the same posting twice.
	conn = db.connect(db_path)
	try:
		with pytest.raises(sqlite3.IntegrityError):
			conn.execute(
				"""INSERT INTO jobs (title, company, role, year, month,
					title_key, company_key, url, first_seen, last_seen)
				VALUES ('Oldest', 'Acme', 'frontend', 2026, 11,
					'oldest', 'acme', 'https://example.com/again',
					'2026-11-17T00:00:00+00:00', '2026-11-17T00:00:00+00:00')"""
			)
	finally:
		conn.close()


def test_running_migrate_twice_is_a_noop(db_path, tmp_path):
	_make_today_db(db_path, [_row()])
	backups = tmp_path / 'backups'
	db.migrate(db_path, backups)

	conn = sqlite3.connect(db_path)
	try:
		before = conn.execute('SELECT id, title, status FROM jobs ORDER BY id').fetchall()
	finally:
		conn.close()
	backups_before = sorted(path.name for path in backups.iterdir())

	assert db.migrate(db_path, backups) == db.LATEST
	assert _version(db_path) == db.LATEST

	conn = sqlite3.connect(db_path)
	try:
		after = conn.execute('SELECT id, title, status FROM jobs ORDER BY id').fetchall()
	finally:
		conn.close()
	assert after == before
	assert sorted(path.name for path in backups.iterdir()) == backups_before


def test_migration_to_2_backs_up_the_version_1_database(db_path, tmp_path):
	_make_today_db(
		db_path,
		[_row(title='Keep Me', first_seen='2026-11-03T07:00:00+00:00')],
	)
	backups = tmp_path / 'backups'
	db.migrate(db_path, backups)

	files = list(backups.glob('jobs-1-*.db'))
	assert len(files) == 1, 'one backup, named for the schema it holds'
	assert _version(files[0]) == 1
	assert 'status' not in _columns(files[0])

	conn = sqlite3.connect(files[0])
	try:
		row = conn.execute('SELECT title, first_seen FROM jobs').fetchone()
	finally:
		conn.close()
	assert row == ('Keep Me', '2026-11-03T07:00:00+00:00')


def test_backup_defaults_to_the_workspace_state_folder(db_path):
	"""A database lives at the workspace root, so the default backup
	folder is the workspace's .jobtriage/backups."""
	_make_today_db(db_path, [_row()])
	db.migrate(db_path)
	assert list((db_path.parent / '.jobtriage' / 'backups').glob('jobs-1-*.db'))


def test_migration_3_adds_source_runs_to_a_version_2_database(db_path, tmp_path):
	"""The `--upgrade` path for an existing database (plan 04 step 6):
	a version-2 file migrates in place to 3, keeps its jobs, backs
	itself up first, and gains the health table."""
	db.migrate(db_path, tmp_path / 'backups')
	conn = db.connect(db_path)
	try:
		with conn:
			conn.execute(
				"""INSERT INTO jobs (title, company, role, year, month,
					title_key, company_key, url, first_seen, last_seen)
				VALUES ('Keep Me', 'Acme', 'software', 2026, 11,
					'keep me', 'acme', 'https://example.com/keep',
					'2026-11-01T08:00:00+00:00', '2026-11-01T08:00:00+00:00')"""
			)
			conn.execute('DROP TABLE source_runs')
			conn.execute('PRAGMA user_version = 2')
		assert _version(db_path) == 2
		found = conn.execute(
			"SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source_runs'"
		).fetchone()
		assert found is None
	finally:
		conn.close()

	assert db.migrate(db_path, tmp_path / 'backups') == 3

	assert _version(db_path) == 3
	conn = db.connect(db_path)
	try:
		assert [row[0] for row in conn.execute('SELECT title FROM jobs')] == ['Keep Me']
		columns = {row[1] for row in conn.execute('PRAGMA table_info(source_runs)')}
		assert columns == {
			'id',
			'source',
			'started_at',
			'duration_ms',
			'ok',
			'fetched',
			'dropped_invalid',
			'error',
		}
		# and the table is writable as the pipeline writes it
		conn.execute(
			"""INSERT INTO source_runs (source, started_at, duration_ms, ok,
				fetched, dropped_invalid, error)
			VALUES ('keep', '2026-11-01T08:00:00+00:00', 120, 1, 3, 0, NULL)"""
		)
	finally:
		conn.close()

	# The migration backed up the version it changed away from.
	backups = sorted(path.name for path in (tmp_path / 'backups').glob('jobs-2-*.db'))
	assert len(backups) == 1


def test_a_newer_schema_is_refused_rather_than_overwritten(db_path):
	conn = sqlite3.connect(db_path)
	conn.execute('PRAGMA user_version = 99')
	conn.close()
	with pytest.raises(RuntimeError, match='schema version 99'):
		db.migrate(db_path)
	assert _version(db_path) == 99


def test_status_history_cascades_with_its_job(db_path, tmp_path):
	db.migrate(db_path, tmp_path / 'backups')
	conn = db.connect(db_path)
	try:
		with pytest.raises(sqlite3.IntegrityError):
			conn.execute(
				"""INSERT INTO status_history (job_id, old, new, at, actor)
				VALUES (999, 'new', 'applied', '2026-11-17T00:00:00+00:00', 'tui')"""
			)
	finally:
		conn.close()
