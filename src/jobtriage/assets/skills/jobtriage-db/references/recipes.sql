-- Read-only recipes for the jobtriage database.
--
-- Run one with:
--     jobtriage db shell < references/recipes.sql   (all of them)
--     jobtriage db shell "SELECT ..."               (one at a time)
--     jobtriage db shell -header -column "SELECT ..."
--
-- Column names come from references/schema.md. Never write here:
-- status, note and attach go through the CLI so history stays true.

-- 1. The funnel: how many jobs carry each status.
SELECT status, COUNT(*) AS jobs
FROM jobs
GROUP BY status
ORDER BY CASE status
	WHEN 'new' THEN 1
	WHEN 'skipped' THEN 2
	WHEN 'to_apply' THEN 3
	WHEN 'applied' THEN 4
	ELSE 5
END;

-- 2. Which boards bring jobs, and how many of theirs reached the shortlist
--    or an application. A high applied rate is quality; a high skipped
--    rate may mean a board's category is too broad.
SELECT source,
	COUNT(*) AS jobs,
	SUM(status = 'to_apply') AS to_apply,
	SUM(status = 'applied') AS applied,
	ROUND(100.0 * SUM(status = 'applied') / COUNT(*), 1) AS applied_pct
FROM jobs
WHERE source IS NOT NULL
GROUP BY source
ORDER BY applied DESC, jobs DESC;

-- 3. Applications over time, by ISO week. `new` is the status the job
--    moved to; actor says who made the change.
SELECT strftime('%Y-W%W', at) AS week,
	COUNT(*) AS applications
FROM status_history
WHERE new = 'applied'
GROUP BY week
ORDER BY week DESC;

-- 4. The same by month, with the applications per board in that month.
SELECT strftime('%Y-%m', h.at) AS month,
	j.source,
	COUNT(*) AS applications
FROM status_history AS h
JOIN jobs AS j ON j.id = h.job_id
WHERE h.new = 'applied'
GROUP BY month, j.source
ORDER BY month DESC, applications DESC;

-- 5. How long it took from first seeing a job to applying to it.
SELECT ROUND(AVG(julianday(h.at) - julianday(j.first_seen)), 1) AS avg_days,
	COUNT(*) AS applications
FROM status_history AS h
JOIN jobs AS j ON j.id = h.job_id
WHERE h.new = 'applied';

-- 6. Open jobs going stale: the board stopped returning them two weeks
--    ago, so the posting is probably closed. Keep the window a parameter.
SELECT id, title, company, source, last_seen,
	CAST(julianday('now') - julianday(last_seen) AS INTEGER) AS days_since_seen
FROM jobs
WHERE status IN ('new', 'to_apply')
	AND julianday(last_seen) < julianday('now', '-14 days')
ORDER BY last_seen ASC;

-- 7. New jobs by day, for a week of scraping: is the feed keeping up?
SELECT substr(first_seen, 1, 10) AS day, COUNT(*) AS jobs
FROM jobs
GROUP BY day
ORDER BY day DESC
LIMIT 14;

-- 8. Board health, latest ten runs: the same numbers `jobtriage sources`
--    prints, in one table.
SELECT source,
	COUNT(*) AS runs,
	SUM(ok = 0) AS failures,
	SUM(fetched) AS fetched,
	SUM(dropped_invalid) AS dropped,
	MAX(started_at) AS last_run
FROM source_runs
GROUP BY source
ORDER BY failures DESC, last_run DESC;

-- 9. Skipped jobs and why: notes are free text, so this is a reading aid,
--    not statistics. Newest first.
SELECT j.id, j.title, j.company, j.notes, j.status_at
FROM jobs AS j
WHERE j.status = 'skipped' AND j.notes IS NOT NULL AND j.notes != ''
ORDER BY j.status_at DESC
LIMIT 30;

-- 10. Reposted jobs: the same title and company seen in more than one
--     month. The cache key includes the month, so these are separate rows
--     on purpose; useful when a role keeps coming back.
SELECT title_key, company_key, COUNT(*) AS sightings,
	MIN(published) AS first_published,
	MAX(published) AS last_published
FROM jobs
GROUP BY title_key, company_key
HAVING COUNT(*) > 1
ORDER BY sightings DESC, last_published DESC;
