# Design notes

Why the tool works the way it does, for whoever changes it next. The
user documentation is [README](../README.md); every config key is in
[config.md](config.md), the plugin contract in
[scrapers.md](scrapers.md), the skills and MCP server in
[skills.md](skills.md), and the research that chose the boards in
[research.md](research.md).

## The filter

`src/jobtriage/relevance.py`, two gates.

The title rules are the user's `[filter]` table, merged from a shipped
preset and the op folder's file; a fresh folder has no title filter
(`preset = "any"`), so the board's category is the only narrowing. The
lists below are the `web-engineer` preset. Every key, the preset
semantics and the region vocabulary are in
[config.md](config.md#filter); `jobtriage filter test "Senior React
Native Engineer"` says why one title passes or not.

**Age.** Older than 21 days is dropped. A job with no date at all is
assumed new instead — it flows on, and the database keys it under the
current month. Boards emit ISO 8601 at mixed precision (date-only
here, milliseconds with a Z there); date-only values count as UTC
midnight, which puts the cutoff a few hours early for them. Dead
posts are dead posts; that suits us.

**Title.** A role worth applying to carries a role word *and* a
builder word (engineer/developer) — anywhere in the title, not
necessarily adjacent, so "Senior AI Product Engineer" is ours. Role
words, matched case-insensitively on word boundaries:

- platform words: web, website, software, product, ai, and the
  full-stack / frontend families in every spelling
  (fullstack, full-stack, full stack, …)
- resume languages named as the role: typescript, javascript,
  node / nodejs / node.js
- stacks named as the role: golang, go, python, react, vue, svelte

Turned away even when they pass both tests, because they sound like
us without being us:

- the sales-engineering family — sales, solutions, support,
  implementation engineer
- manager ("Product Manager, Developer Tools" carries both words and
  is still a PM job)
- DevRel — advocate, evangelist, relations
- security and network engineers, and interviewer roles
- the mobile family — ios, android, mobile, flutter, react native —
  which slips in through the words it shares with web work, "React"
  most often, or a parenthetical "AI Integration"

Seniority and domain are deliberately not filtered. "Junior
Software Engineer" and "Software Engineer, Networking" pass; those
judgments stay with the reader.

A new word is one line in the user's `[filter]` lists (or in a preset
under `src/jobtriage/assets/presets/`). `jobtriage scrape --explain`
reports, per board, how many jobs the age and title gates dropped and
the words that did it.

## The scrape, in one transaction

A default run scrapes the 50 newest jobs each board has for the
configured locations (worldwide by default), merges and dedupes them,
drops anything older than 21 days, keeps the titles that match the
filter, then ingests what remains in one transaction: a job the
database hasn't seen is inserted with status `new` and gets an id, and
a job it has is touched — `last_seen`, plus the newest url, salary and
source as pointer data. There is no second step to fail, so a scrape
either lands whole or not at all, and no later run repeats a job. The
one-line summary (`N relevant · x new · y already seen · total in the
database`) goes to stderr.

Rows keep their first sighting (title, published, first_seen), and
`last_seen` doubles as a "probably closed" signal when a job stops
appearing on every board. An export is a snapshot; losing one costs
nothing.

## Dedup keys and the jobs table

Jobs are deduplicated by (title, company, role, year, month):

- title and company are casefolded with punctuation stripped, so the
  same posting worded differently across boards — "Frontend
  Engineer" against "Front End Engineer", hyphen against en dash —
  is one job, not two. Token-level company naming ("Pitchup.com"
  against "Pitchup") still stays distinct; the in-run merge has the
  same limit.
- role is the role word that matched.
- year and month come from the published date, and the month in
  the key is what lets the same title posted again next month count
  as new. An undated job is keyed under the current month: assumed
  new, as decided.

Columns: `id` (the stable one the CLI, the TUI and agents address a
job by), title, company, role, year, month, title_key and
company_key (the folded keys), url, published (NULL for undated),
locations (JSON array), salary, source (the board that served the
first sighting), first_seen, last_seen, status, status_at, notes.
Statuses are slugs from `src/jobtriage/status.py` — `new`, `skipped`,
`to_apply`, `applied` — and each one has a label for display.
`status_history` records every change with its time and actor
(`tui`, `cli`, `import`, `agent`); `attachments` is where a cover
letter or other file recorded with `jobtriage attach` lives.
`jobtriage db schema` prints all of it with a comment per column.

## One location vocabulary, fifteen spellings

A person has one location preference ("worldwide, EMEA, Nigeria");
each board spells the places differently: `'UK'` on remotive,
`'United Kingdom'` on wellfound, `'NG'` on dynamitejobs, slugs like
`remote-jobs-emea` on euremotejobs. `src/jobtriage/geo.py` owns the
one vocabulary in the middle:

- `worldwide` — open to anyone, anywhere;
- regions (`emea`, `europe`, `eu`, `uk-ireland`, `nordics`, `benelux`,
  `western-europe`, `eastern-europe`, `southern-europe`,
  `northern-europe`, `central-europe`, `middle-east`, `africa`,
  `sub-saharan-africa`, `americas`, `north-america`, `latam`,
  `caribbean`, `apac`, `asia`, `oceania`);
- countries as ISO 3166-1 alpha-2 codes, with display names and the
  aliases people and boards actually type ("UK", "USA", "Türkiye",
  "Viet Nam").

Three sides of the translation, all driven by the board's own
declarations:

- `expand(prefs)` resolves a preference list to canonical tokens and
  flattens regions into their member countries. It is what the central
  filter and the "does this board cover anything I want?" check read.
- `board_tags(module, prefs)` feeds a board: a scraper declares its
  wording as data (`WORLDWIDE`, `REGIONS`, `COUNTRY_STYLE`,
  `COUNTRY_ALIASES`) and gets back the exact tuple its `recent_jobs`
  already accepts. The model is containment: a job matches when its
  stated area *covers* one of the user's locations — a job must be open
  to where I am, or wider. A region token asks for the board's tags of
  every declared region at least as wide as itself (`europe` pulls EMEA
  and Europe; `emea` pulls only EMEA) and never for member countries; a
  country token asks for the tags of every declared region containing
  it, then its own wording. That is why a `REGIONS` key must state a
  tag's true area: a "Europe" tag filed under `emea` would let a
  Europe-only job match a Nigeria-based user. A board that declares
  nothing returns `()`: the pipeline reads that as `locations=None`,
  meaning the board fetches everything and the central filter does the
  narrowing.
- `normalize(module, tags)` is the inverse, best effort: raw board
  tags become canonical codes/regions so the store can keep canonical
  locations beside the original tags. A tag we cannot place is kept
  as written rather than dropped — losing it would lose the only clue
  about where the job is.

The country table is vendored rather than a dependency: about 250 rows
fit in one file and never break, while a package whose vocabulary can
change under us would be a new failure mode in the middle of a scrape.
Matching is case-insensitive and forgiving about punctuation, because
"UK", "uk" and "U.K." are one place and no board has promised
consistency.

## The registry: three tiers, one contract

A plugin is a module with `NAME` and `recent_jobs`. `src/jobtriage/registry.py`
loads them from three places, in order:

1. **built-ins** — `jobtriage.scrapers.*`, found with
   `pkgutil.iter_modules`, never a hand-written list, so a new module
   in the package is a new board;
2. **installed packages** — the `jobtriage.scrapers` entry-point group,
   so a board pack can be `pip install`ed;
3. **workspace plugins** — `<op>/scrapers/*.py`, one file per board,
   loaded by path. Files starting with `_` are skipped; the shipped
   template is `_example.py`.

Nothing is ever loaded from the current directory: a plugin is
arbitrary code with the user's permissions, so the installed
environment and the op folder are the only two places it may come from.
A later source overrides an earlier one with the same `NAME`, and a
notice says so — that is the point of the workspace tier, where a user
can patch a board the day it breaks.

A plugin that cannot be used is *broken*, never fatal: an import error,
a missing or malformed `NAME`, a missing `recent_jobs`, an
`API_VERSION` newer than this build, or an alias that clashes with
another board. `enabled()` and `run()` skip broken boards, the rest of
the run goes on, and `sources` / `doctor` name the reason.

`run()` is the contract boundary: it times the call, isolates a
board's exception, validates every returned row, drops the bad ones
with a numbered reason, truncates to `limit` and tags `source`.
Nothing invalid ever reaches the database.

## Config provenance

The config resolves in layers, lowest first: code default < preset <
`jobtriage.toml` < `JOBTRIAGE_*` environment < CLI flag. `load()` is
the signature every caller uses; `load_with_provenance()` is the same
resolution and also records which layer set each dotted key, which is
what `jobtriage config show` prints (`# default`,
`# preset:web-engineer`, `# file`, `# env:JOBTRIAGE_SEARCH_LIMIT`,
`# flag`). One resolution pass, two views: the answer to "why is the
limit 20?" is the answer the tool acted on, not a second guess at it.

## Templates, the manifest, and upgrades

The op folder is built from `src/jobtriage/assets/`. `init` records the
SHA-256 of every file it ships in `.jobtriage/manifest.json`, and
`.jobtriage/version` holds the version that shipped them. `init` never
overwrites a file that is already there.

`init --upgrade` replaces a file only when its hash still matches the
manifest — untouched since it shipped. An edited file is left alone
and the new version lands beside it as `<file>.new`. That is the whole
upgrade rule, and it is why a template can change in this repository
without touching anyone's edits.

The version stamp is also how a run notices that the package moved and
the folder did not: `workspace.upgrade_notice()` compares the stamp
with `__version__`, and the CLI prints one line pointing at
`init --upgrade`. A missing stamp is silence — there is nothing to
compare, so the advice would be a guess.

## Three version tracks

Each moves on its own, because they answer to different audiences:

| Track | Where it lives | What a bump means |
|---|---|---|
| CLI semver | `pyproject.toml` `[project].version`, mirrored in `src/jobtriage/__init__.py` (`__version__`) | The package's release. `--version`, the upgrade notice and the changelog read it. |
| Plugin API | `src/jobtriage/plugin/__init__.py` (`API_VERSION`, currently `1`) | A change that breaks a plugin written against the old contract. The registry refuses a plugin that declares a newer number. |
| Database schema | `src/jobtriage/db.py` (`LATEST`, currently `3`) | A new migration. The number is stored in the database header as `PRAGMA user_version`; `db.schema_version()` reads it and `db.migrate()` moves it one migration at a time, after a file backup. |

A release that only changes the CLI bumps the first number. A new
plugin helper is a minor release and leaves `API_VERSION` alone; a
contract break is a release *and* an `API_VERSION` bump. A migration is
a release *and* a `LATEST` bump, counted from the database's stored
`user_version`, never from the code's idea of it. Never edit a
migration that has shipped; add the next number, because someone's
database has already run the old one.

## The TUI's one key table

`src/jobtriage/tui/keys.py` is the only list of bindings. Textual's
bindings, the footer's short form, the `?` overlay and the key map in
this repository's README are all generated from it; `[tui.keys]` in the
op folder resolves through the same table, so a rebound key changes all
four at once and help cannot go stale. `tests/tui/test_readme.py`
regenerates the README block and fails on drift.

## Layout

    src/jobtriage/__init__.py    version only
    src/jobtriage/__main__.py    python -m jobtriage
    src/jobtriage/cli/           one module per subcommand (main builds the
                                 parser and resolves the workspace; scrape,
                                 sources, list, show, open, status, note,
                                 attach, export, config, filter, db, skills,
                                 mcp, tui, init, doctor; output holds
                                 printing, tty the terminal checks)
    src/jobtriage/workspace.py   finding, creating, upgrading the op folder
    src/jobtriage/config.py      jobtriage.toml: every section, one loader
    src/jobtriage/geo.py         the location vocabulary and translations
    src/jobtriage/pipeline.py    scrape_all(), the parallel merge
    src/jobtriage/registry.py    board discovery, overrides, validation, run
    src/jobtriage/plugin/        the public plugin API: http, parse, dates,
                                 geo, Job
    src/jobtriage/relevance.py   the filter: age cutoff, role-title
                                 matcher, fold-tolerant dedup
    src/jobtriage/db.py          the schema, migrations, backups
    src/jobtriage/status.py      the status table: slug, label, colour
    src/jobtriage/store.py       the database's API: ingest, queries,
                                 Tracker for the TUI
    src/jobtriage/attachments.py the files recorded against a job
    src/jobtriage/tui/           the triage TUI (textual): keys.py the key
                                 table, widgets.py the screen, app.py the
                                 behaviour; help.py the ? overlay
    src/jobtriage/chrome.py      the apply Chrome: launch on the
                                 workspace profile, one tab per job,
                                 over CDP — the Browser class
    src/jobtriage/browser/       finding a browser and bringing it forward
    src/jobtriage/logs.py        the rotating log and -v
    src/jobtriage/mcp.py         the stdio MCP server and its tools
    src/jobtriage/scrapers/      one module per board, behind one contract
    src/jobtriage/assets/        the templates `jobtriage init` copies
    tests/
