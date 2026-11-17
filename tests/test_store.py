"""Characterisation tests for the store: ingest's new-vs-seen split,
the touch-on-hit, the Tracker, and the query API — against a tmp_path
database, never the developer's own jobtriage.db.

The behaviour pinned here is the seen-cache's (plans 01–04 must not
change which jobs are one job, or what a later sighting refreshes)
now expressed through ingest; the tracker and query tests pin plan
02's new API, which the CLI (step 5) and the TUI (step 7) are written
against.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from jobtriage import db, status, store


def _rows(db_path):
	"""The jobs table as raw rows, for the columns query() reshapes."""
	conn = db.connect(db_path)
	try:
		return conn.execute('SELECT * FROM jobs ORDER BY id').fetchall()
	finally:
		conn.close()


def _history(db_path, job_id):
	"""One job's status changes in the order they were made."""
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


def _days_ago(days):
	"""A published date `days` before now, in a board's ISO shape."""
	return (datetime.now(UTC) - timedelta(days=days)).isoformat()


# -- ingest: new against seen -------------------------------------------


def test_ingest_on_empty_db_inserts_everything(job, db_path):
	jobs = [job(title=f'Software Engineer {i}', company=f'Co {i}') for i in range(3)]
	result = store.ingest(jobs, db_path)
	assert [j['title'] for j in result.new] == [j['title'] for j in jobs]
	assert result.seen == 0
	assert result.total == 3
	assert all(isinstance(j['id'], int) for j in result.new)
	assert len({j['id'] for j in result.new}) == 3


def test_ingest_twice_reports_the_second_batch_as_seen(job, db_path):
	jobs = [job(title='Frontend Engineer'), job(title='Backend Engineer')]
	store.ingest(jobs, db_path)
	again = store.ingest(jobs, db_path)
	assert again.new == []
	assert again.seen == 2
	assert again.total == 2


def test_new_jobs_get_the_default_status(job, db_path):
	result = store.ingest([job()], db_path)
	assert _rows(db_path)[0]['status'] == status.DEFAULT
	assert result.new[0]['id'] == _rows(db_path)[0]['id']


def test_same_key_in_one_batch_folds_to_one_row(job, db_path):
	"""Two boards, two spellings, one ingest: the second sighting is a
	touch, not a second row (and not an IntegrityError)."""
	result = store.ingest(
		[
			job(title='Frontend Engineer', company='Acme'),
			job(title='Front-End Engineer', company='ACME', source='other'),
		],
		db_path,
	)
	assert len(result.new) == 1
	assert result.seen == 1
	assert result.total == 1
	assert len(_rows(db_path)) == 1


def test_fold_tolerant_key_same_job_next_month(job, db_path):
	"""The same title/company worded differently is one job — until
	the month rolls over, when the repost counts as new again."""
	first = job(title='Frontend Engineer', company='Acme', published='2026-10-05')
	restated = job(title='Front-End Engineer', company='ACME', published='2026-10-20')
	repost = job(title='Frontend Engineer', company='Acme', published='2026-11-03')
	store.ingest([first], db_path)
	assert store.ingest([restated], db_path).seen == 1
	assert len(store.ingest([repost], db_path).new) == 1


def test_keys_are_the_cache_keys_still(job, db_path):
	"""The fold-tolerant key logic is untouched: the stored keys are
	job_key(title) / job_key(company), and the year and month come from
	published (the current month when undated)."""
	store.ingest([job(title='Front-End Engineer', company='ACME Inc.')], db_path)
	row = _rows(db_path)[0]
	assert row['title_key'] == store.job_key('Front-End Engineer')
	assert row['company_key'] == store.job_key('ACME Inc.')
	assert (row['year'], row['month']) == (2026, 11)


def test_undated_job_is_keyed_under_current_month(job, db_path):
	store.ingest([job(published=None)], db_path)
	assert store.ingest([job(published=None)], db_path).seen == 1
	now = datetime.now(UTC)
	row = _rows(db_path)[0]
	assert (row['year'], row['month']) == (now.year, now.month)


# -- ingest: touching a seen job ----------------------------------------


def test_hit_is_touched_with_newest_pointer_data(job, db_path):
	store.ingest([job(url='https://old.example', salary='$100k', source='a')], db_path)
	result = store.ingest([job(url='https://new.example', salary='$120k', source='b')], db_path)
	assert result.seen == 1
	saved = store.query(db_path)[0]
	assert saved['url'] == 'https://new.example'
	assert saved['salary'] == '$120k'
	assert saved['source'] == 'b'


def test_hit_without_salary_keeps_the_old_one(job, db_path):
	store.ingest([job(salary='$100k')], db_path)
	store.ingest([job(salary=None)], db_path)
	assert store.query(db_path)[0]['salary'] == '$100k'


def test_first_sighting_is_intact_after_a_touch(job, db_path):
	first = job(url='https://old.example', published='2026-11-10')
	store.ingest([first], db_path)
	seen_at = _rows(db_path)[0]['first_seen']
	store.ingest([job(url='https://new.example', published='2026-11-10')], db_path)
	row = _rows(db_path)[0]
	assert row['first_seen'] == seen_at  # untouched
	assert row['first_seen'] <= row['last_seen']
	assert row['url'] == 'https://new.example'  # pointer data refreshed
	assert row['published'] == '2026-11-10T00:00:00+00:00'


def test_a_touch_does_not_undo_triage(job, db_path):
	"""Status and notes are the person's: a re-scrape refreshes the
	pointer and leaves them exactly where they were."""
	job_id = store.ingest([job()], db_path).new[0]['id']
	store.set_status(db_path, job_id, 'applied', 'cli')
	store.add_note(db_path, job_id, 'interview booked')
	store.ingest([job(url='https://moved.example')], db_path)
	saved = store.get(db_path, job_id)
	assert saved['status'] == 'applied'
	assert saved['notes'] == 'interview booked'


def test_published_is_normalised_to_utc_seconds(job, db_path):
	store.ingest([job(published='2026-11-10T12:34:56.789Z')], db_path)
	assert _rows(db_path)[0]['published'] == '2026-11-10T12:34:56+00:00'


def test_locations_round_trip_as_a_list(job, db_path):
	store.ingest([job(locations=['Worldwide', 'EMEA'])], db_path)
	assert json.loads(_rows(db_path)[0]['locations']) == ['Worldwide', 'EMEA']
	assert store.query(db_path)[0]['locations'] == ['Worldwide', 'EMEA']


def test_ingesting_nothing_is_a_no_op(job, db_path):
	assert store.ingest([], db_path) == store.IngestResult([], 0, 0)
	store.ingest([job()], db_path)
	assert store.ingest([], db_path) == store.IngestResult([], 0, 1)


# -- query and get ------------------------------------------------------


def test_query_newest_first_undated_last(job, db_path):
	store.ingest(
		[
			job(title='Old', company='A', published='2026-11-01'),
			job(title='New', company='B', published='2026-11-09'),
			job(title='Undated', company='C', published=None),
		],
		db_path,
	)
	assert [j['title'] for j in store.query(db_path)] == ['New', 'Old', 'Undated']


def test_query_filters_by_status(job, db_path):
	first = store.ingest([job(title='One', company='A')], db_path).new[0]
	store.ingest([job(title='Two', company='B')], db_path)
	store.set_status(db_path, first['id'], 'to_apply', 'cli')
	assert [j['title'] for j in store.query(db_path, status='to_apply')] == ['One']
	assert [j['title'] for j in store.query(db_path, status='new')] == ['Two']


def test_query_filters_by_source(job, db_path):
	store.ingest(
		[
			job(title='A', company='A', source='one'),
			job(title='B', company='B', source='two'),
		],
		db_path,
	)
	assert [j['title'] for j in store.query(db_path, source='two')] == ['B']


def test_query_search_spans_title_company_role_and_url(job, db_path):
	store.ingest(
		[
			job(
				title='Platform Engineer',
				company='Acme',
				role='software',
				url='https://a',
			),
			job(
				title='Designer',
				company='Globex',
				role='design',
				url='https://unique.example',
			),
		],
		db_path,
	)
	assert [j['company'] for j in store.query(db_path, search='platform')] == ['Acme']
	assert [j['company'] for j in store.query(db_path, search='GLOBEX')] == ['Globex']
	assert [j['company'] for j in store.query(db_path, search='design')] == ['Globex']
	assert [j['company'] for j in store.query(db_path, search='unique.example')] == ['Globex']
	assert store.query(db_path, search='nothing-matches') == []


def test_query_search_treats_wildcards_as_text(job, db_path):
	store.ingest(
		[job(title='C++ Engineer', company='A'), job(title='Go Engineer', company='B')],
		db_path,
	)
	assert [j['title'] for j in store.query(db_path, search='%')] == []


def test_query_since_on_published_and_first_seen(job, db_path):
	"""A dated job is filtered on its own age; an undated one falls back
	to when it was first seen (undated is 'assumed new' everywhere)."""
	store.ingest(
		[
			job(title='Fresh', company='A', published=_days_ago(2)),
			job(title='Stale', company='B', published=_days_ago(30)),
			job(title='Undated', company='C', published=None),
		],
		db_path,
	)
	assert [j['title'] for j in store.query(db_path, since='7d')] == [
		'Fresh',
		'Undated',
	]
	assert len(store.query(db_path, since=7)) == 2


def test_query_order_and_limit(job, db_path):
	store.ingest(
		[
			job(title='Beta', company='A', published='2026-11-01'),
			job(title='Alpha', company='B', published='2026-11-09'),
		],
		db_path,
	)
	assert [j['title'] for j in store.query(db_path, order='title')] == ['Alpha', 'Beta']
	assert [j['title'] for j in store.query(db_path, limit=1)] == ['Alpha']
	assert len(store.query(db_path, limit=None)) == 2


def test_query_refuses_what_it_cannot_filter(job, db_path):
	store.ingest([job()], db_path)
	with pytest.raises(KeyError, match='unknown status'):
		store.query(db_path, status='nonsense')
	with pytest.raises(ValueError, match='order'):
		store.query(db_path, order='title; DROP TABLE jobs')
	with pytest.raises(ValueError, match='since'):
		store.query(db_path, since='last week')


def test_query_on_a_missing_database_is_empty(db_path):
	assert store.query(db_path) == []
	assert store.counts(db_path) == {entry.slug: 0 for entry in status.STATUSES}


def test_get_returns_every_field(job, db_path):
	job_id = store.ingest([job(locations=['EMEA'])], db_path).new[0]['id']
	saved = store.get(db_path, job_id)
	for key in [
		'id',
		'title',
		'company',
		'role',
		'year',
		'month',
		'title_key',
		'company_key',
		'url',
		'published',
		'locations',
		'salary',
		'source',
		'first_seen',
		'last_seen',
		'status',
		'status_at',
		'notes',
	]:
		assert key in saved, key
	assert saved['locations'] == ['EMEA']
	assert saved['notes'] is None


def test_get_unknown_id_raises(db_path):
	store.ingest([], db_path)
	with pytest.raises(KeyError, match='no job with id 99'):
		store.get(db_path, 99)


# -- set_status ---------------------------------------------------------


def test_set_status_writes_the_job_and_a_history_row(job, db_path):
	job_id = store.ingest([job()], db_path).new[0]['id']
	store.set_status(db_path, job_id, 'to_apply', 'cli')
	saved = store.get(db_path, job_id)
	assert saved['status'] == 'to_apply'
	assert saved['status_at'] is not None
	history = _history(db_path, job_id)
	assert len(history) == 1
	assert history[0]['old'] == 'new'
	assert history[0]['new'] == 'to_apply'
	assert history[0]['actor'] == 'cli'
	assert history[0]['at'] == saved['status_at']


def test_set_status_defaults_and_follows_a_second_change(job, db_path):
	job_id = store.ingest([job()], db_path).new[0]['id']
	store.set_status(db_path, job_id, 'skipped')  # the CLI/agent default actor
	store.set_status(db_path, job_id, 'applied', 'agent')
	history = _history(db_path, job_id)
	assert [(h['old'], h['new'], h['actor']) for h in history] == [
		('new', 'skipped', 'cli'),
		('skipped', 'applied', 'agent'),
	]


def test_set_status_to_the_same_slug_is_a_no_op(job, db_path):
	job_id = store.ingest([job()], db_path).new[0]['id']
	store.set_status(db_path, job_id, 'new', 'cli')
	assert _history(db_path, job_id) == []
	assert store.get(db_path, job_id)['status_at'] is None


def test_set_status_refuses_an_unknown_slug_without_writing(job, db_path):
	job_id = store.ingest([job()], db_path).new[0]['id']
	with pytest.raises(KeyError, match='unknown status slug'):
		store.set_status(db_path, job_id, 'decked', 'cli')
	assert store.get(db_path, job_id)['status'] == 'new'
	assert _history(db_path, job_id) == []


def test_set_status_refuses_an_unknown_id(db_path):
	store.ingest([], db_path)
	with pytest.raises(KeyError, match='no job with id 99'):
		store.set_status(db_path, 99, 'applied', 'cli')


# -- notes and counts ---------------------------------------------------


def test_add_note_appends_and_returns_the_notes(job, db_path):
	job_id = store.ingest([job()], db_path).new[0]['id']
	assert store.add_note(db_path, job_id, 'first') == 'first'
	assert store.add_note(db_path, job_id, 'second') == 'first\nsecond'
	assert store.get(db_path, job_id)['notes'] == 'first\nsecond'


def test_add_note_refuses_an_unknown_id(db_path):
	store.ingest([], db_path)
	with pytest.raises(KeyError, match='no job with id 99'):
		store.add_note(db_path, 99, 'hello')


def test_counts_include_every_status_in_cycle_order(job, db_path):
	ids = [
		store.ingest([job(title=f'Job {i}', company=f'Co {i}')], db_path).new[0]['id'] for i in range(3)
	]
	store.set_status(db_path, ids[0], 'to_apply', 'cli')
	store.set_status(db_path, ids[1], 'applied', 'cli')
	assert store.counts(db_path) == {
		'new': 1,
		'skipped': 0,
		'to_apply': 1,
		'applied': 1,
	}
	assert list(store.counts(db_path)) == [entry.slug for entry in status.STATUSES]


# -- attachments: plan 02's table, plan 05's use -------------------------


def test_attach_records_one_row_and_is_idempotent(job, db_path):
	job_id = store.ingest([job()], db_path).new[0]['id']

	assert store.attach(db_path, job_id, 'letters/acme-engineer.md') is True
	assert store.attach(db_path, job_id, 'letters/acme-engineer.md') is False
	assert store.attach(db_path, job_id, 'letters/acme-engineer.md', 'file') is False

	rows = store.attachments(db_path, job_id)
	assert [(row['kind'], row['path']) for row in rows] == [
		('cover_letter', 'letters/acme-engineer.md')
	]


def test_attach_refuses_an_unknown_job(db_path):
	store.ingest([], db_path)
	with pytest.raises(KeyError, match='no job with id 99'):
		store.attach(db_path, 99, 'letters/acme-engineer.md')


# -- the TUI's tracker --------------------------------------------------


def test_tracker_load_gives_every_job_an_id_newest_first(job, db_path):
	store.ingest(
		[
			job(title='Old', company='A', published='2026-11-01'),
			job(title='New', company='B', published='2026-11-09'),
			job(title='Undated', company='C', published=None),
		],
		db_path,
	)
	jobs = store.Tracker(db_path).load()
	assert [j['title'] for j in jobs] == ['New', 'Old', 'Undated']
	assert all('row' not in j for j in jobs)
	assert {j['id'] for j in jobs} == {1, 2, 3}  # insertion order, not sort order
	assert jobs[2]['published'] == 'undated'  # the TUI's sort bucket
	assert jobs[0]['status'] == 'new'  # the slug, what set_status takes
	assert jobs[0]['locations'] == ['Worldwide']


def test_tracker_set_status_records_a_tui_change(job, db_path):
	job_id = store.ingest([job()], db_path).new[0]['id']
	tracker = store.Tracker(db_path)
	tracker.set_status(job_id, 'to_apply')
	assert tracker.load()[0]['status'] == 'to_apply'
	assert [(h['new'], h['actor']) for h in _history(db_path, job_id)] == [('to_apply', 'tui')]


def test_tracker_history_and_attachments_read_the_child_tables(job, db_path):
	"""The details pane's two reads: the same columns `jobs show`
	uses, in the order they were written."""
	job_id = store.ingest([job()], db_path).new[0]['id']
	tracker = store.Tracker(db_path)
	tracker.set_status(job_id, 'to_apply')
	tracker.set_status(job_id, 'applied')

	assert [(row['old'], row['new'], row['actor']) for row in tracker.history(job_id)] == [
		('new', 'to_apply', 'tui'),
		('to_apply', 'applied', 'tui'),
	]
	assert tracker.attachments(job_id) == []

	conn = db.connect(db_path)
	try:
		with conn:
			conn.execute(
				'INSERT INTO attachments (job_id, kind, path, created_at) VALUES (?, ?, ?, ?)',
				(job_id, 'cover_letter', '/ops/letters/acme.pdf', '2026-11-18T10:00:00+00:00'),
			)
	finally:
		conn.close()

	assert [(row['kind'], row['path']) for row in tracker.attachments(job_id)] == [
		('cover_letter', '/ops/letters/acme.pdf')
	]
	# Unknown ids are an empty list here: the pane asks about the job
	# the list just handed it, and a deleted row is not an error.
	assert tracker.history(99) == []
	assert tracker.attachments(99) == []


def test_tracker_add_note_appends_and_returns_the_notes(job, db_path):
	job_id = store.ingest([job()], db_path).new[0]['id']
	tracker = store.Tracker(db_path)

	assert tracker.add_note(job_id, 'called back') == 'called back'
	assert tracker.add_note(job_id, 'interview booked') == 'called back\ninterview booked'
	assert tracker.load()[0]['notes'] == 'called back\ninterview booked'
