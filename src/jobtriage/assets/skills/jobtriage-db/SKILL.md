---
name: jobtriage-db
description: Use for questions about the job tracker's data — how many jobs or applications, which boards produce the best jobs, jobs from a company or board, jobs going stale, or anything that needs a count, a breakdown or SQL. Covers jobtriage list filters, the database schema and read-only query recipes.
---

# Querying the tracker

`jobtriage list` answers most questions. Reach for SQL only when the
question needs a join, a group or the status history. `jobtriage show`
is the single-job version of both.

The full schema is in `references/schema.md` (generated from a fresh
database, so it cannot drift); the ready-made queries are in
`references/recipes.sql`. `jobtriage db schema` prints the same shape
in a terminal.

## The rule

Read with any of these. **Write only through the CLI**:
`jobtriage status`, `jobtriage note`, `jobtriage attach`.
Do not `UPDATE`, `INSERT` or `DELETE` with SQL: that bypasses status
history, the actor, and the unique key that keeps a re-scrape from
duplicating a job. Reading in `db shell` is fine.

## list filters

Every filter is optional and independent; `--json` prints full job dicts
(agents and scripts read this). Plain output is three lines per job.

    jobtriage list --status new --json
    jobtriage list --since 7d --status applied --json
    jobtriage list --source remotive --limit 50 --json
    jobtriage list --search "python" --order last_seen --json
    jobtriage list --order salary --status to_apply --json
    jobtriage export --format json --status applied

- `--status` takes a slug: `new`, `skipped`, `to_apply`, `applied`.
- `--since 7d` measures from `published`; an undated job falls back to
  `first_seen`, so it does not disappear.
- `--source` is a board's canonical name (`remotive`, not `rm`).
- `--search` is a case-insensitive substring of title, company, role or
  URL; `%` and `_` are literal.
- `--order` takes `company`, `first_seen`, `id`, `last_seen`,
  `published`, `salary`, `source`, `status`, `title`.
- `--limit N` caps the result; the positional form `list 20` does the
  same. `export` returns every match unless limited.
- The summary line goes to stderr; a `--json` run has no summary.

`jobtriage show ID --json` is one object: the job's columns, plus
`history` (status changes, oldest first) and `attachments`.

## Common sums

These are faster as `list` filters, and the counts come from `jq`:

    jobtriage list --status applied --json | jq length
    jobtriage list --source weworkremotely --since 30d --json | jq '[.[].salary] | length'

`--since` measures the **posting's** age, not when the user applied. "How
many did I apply to this week" is a `status_history` question (recipe 3
in `references/recipes.sql`); a `list --since 7d --status applied` counts
jobs *published* in the last week that are now applied, which is a
different set.

For the total without parsing, `jobtriage list` prints `N in the database`
on stderr; it is one number, not a status breakdown. For anything
grouped by status, source or week, use SQL.

## SQL, read-only

`jobtriage db shell` runs the real `sqlite3` on the workspace database;
extra arguments go through to it.

    jobtriage db shell "SELECT source, COUNT(*) AS n FROM jobs GROUP BY source ORDER BY n DESC"
    jobtriage db shell -header -column "SELECT status, COUNT(*) FROM jobs GROUP BY status"

`jobtriage db path` prints the file for another client. Open it
read-only when the client has the option; never leave a write open.

The four questions the CLI cannot answer, with the SQL in
`references/recipes.sql`:

1. **Per-source counts and quality** — jobs per board, and how many of
   that board's jobs reached `to_apply` or `applied`.
2. **Applications over time** — `status_history` rows whose `new` is
   `applied`, grouped by week or month, plus the median wait from first
   sighting to applying.
3. **Jobs going stale** — open jobs (`new` or `to_apply`) whose
   `last_seen` is older than two weeks; the board stopped showing them,
   so the posting is probably closed.
4. **The funnel and the reasons** — status counts, and what the notes
   say about skipped jobs.

`status_history.new` is the status the job moved *to*; `old` is where it
came from. `actor` is `tui`, `cli`, `import` or `agent`. `at` is ISO UTC
with an offset, which `julianday()` and `datetime()` understand.

## Reporting to the user

Give the number first, then the one or two rows that explain it. Say
which window the answer covers (`--since 7d`, "all time") and that
`published` is the board's date, while `first_seen` is when this
tracker first saw the job. A count of `applied` means the user marked it
applied; the tool has no idea whether the employer replied.
