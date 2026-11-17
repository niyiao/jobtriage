# jobtriage

One CLI over fifteen remote job boards — and one place to keep what it
finds. jobtriage scrapes every enabled board in parallel, drops the
postings older than three weeks, keeps the titles that match your
filter, and remembers every job and its triage status in a local SQLite
database, so a later scrape only shows you what is new. A TUI sits on
top: newest first, one key to move a job through **new → to apply →
applied → skip**, and `enter` opens the posting in a Chrome window it
keeps for applying.

<!-- screenshot: TODO — the repository has no real TUI screenshot yet.
     Take one of `jobtriage` on a populated database (a wide terminal
     with the details pane open), save it under docs/, and embed it
     here. Never commit a mock-up. -->

## Install

    uv tool install jobtriage     # or: pipx install jobtriage
    jobtriage init                # create the op folder (default ~/jobtriage)
    jobtriage doctor              # check the setup; every problem names its fix

Python 3.11 or newer. macOS and Linux are the tested platforms.
Windows is supported in the code — the browser finder, the launch, the
copy fallback for skill links and the CI matrix all cover it — but the
TUI has not been run in Windows Terminal yet. If the screen renders
badly there, a screenshot in an issue is the report that helps.

`jobtriage init` asks four small questions — where you can work from,
what kind of role, a title-filter preset, employment — then writes the
op folder from the shipped templates. Pressing Enter keeps each
default; `--yes` takes all four without asking, for scripts and agents.
Everything personal lives in that folder:

    ~/jobtriage/
      jobtriage.toml            config; its presence marks the folder
      jobtriage.db              the database: jobs, statuses, notes
      profile/                  RESUME.md, DETAILS.md, resume PDF
      letters/                  cover letters
      scrapers/                 user scraper plugins
      skills/                   agent skills
      AGENTS.md                 agent instructions for this folder
      CLAUDE.md                 symlink to AGENTS.md
      .agents/skills            symlink to skills/ (also .claude/skills,
                                .opencode/skills)
      writing-style.md          prose style for letters and content
      .env                      secrets (API keys), optional
      .browser-profile/         the Chrome profile for applying/research
      .jobtriage/               tool state: version stamp, asset manifest,
                                database backups, logs, the TUI's remembered place
      .gitignore                so the folder can be git-tracked safely

The repository holds the code and nothing personal. The command finds
the folder by itself: from inside it, from the pointer `init` wrote, or
explicitly with `--workspace PATH` or `JOBTRIAGE_HOME`.

`init` is idempotent — safe to run twice, it never overwrites a file
you've edited. After a package update, `jobtriage init --upgrade`
refreshes the shipped templates: files you haven't touched are
replaced, edited files are left alone and the new version lands beside
them as `<file>.new`. If the package is newer than the folder, the
first run says so and points at that command.

## Quickstart

    jobtriage scrape               # the pipeline: every board in parallel,
                                   # relevant new jobs into the database
    jobtriage                      # the TUI over what landed

A default scrape asks each enabled board for its 50 newest jobs for
your configured locations, merges and dedupes them, drops anything
older than 21 days, keeps the titles the filter matches, and ingests
what remains in one transaction. The one-line summary
(`N relevant · x new · y already seen · total in the database`) goes to
stderr. A second run reports what it has already seen; nothing is
repeated.

`jobtriage scrape -s rs 20` runs one board with a different limit;
`--raw` prints the unfiltered listing and saves nothing;
`--explain` says, per board, what the age and title gates dropped and
which words did it.

### The TUI

Tabs follow the workflow: **new → to apply → applied → skip → all**,
opening on `tui.default_filter` (to apply) unless the last session
ended on another. `←`/`→` change tabs and never touch a status. The
tab, the search query and the selected job are saved on quit and
restored at the next start.

A status key writes the status the moment it is pressed and moves on
to the next job — triage is a stream of decisions. `u` undoes the last
one: status, tab and cursor, repeatable for the session. Writes go
straight to the database, and every change lands in the job's history
with the actor `tui`.

`/` searches title and company as you type; enter keeps the query, esc
clears it. `m` adds a one-line note, `y` copies the job's URL, `d`
shows the details pane (company, dates, locations, pay, source, notes,
history, attachments), and `?` shows every binding. `R` runs a scrape
in the background and reloads the list when it lands; `r` reloads
without scraping.

`enter` opens the job in the apply Chrome — the op folder's browser
profile, started if it isn't already up. Each job remembers its tab: a
second enter focuses that tab, and a new one opens only once the old
has been closed. Quitting the TUI leaves Chrome running. With no
Chrome installed, `enter` opens the job in the system's default
browser instead.

<!-- tui keys: generated from src/jobtriage/tui/keys.py — do not edit by hand -->

| Key | Action |
|---|---|
| `n` | set new and move on |
| `t` | set to apply and move on |
| `a` | set applied and move on |
| `s` | set skip and move on |
| `u` | undo the last status change |
| `j` / `↓` | move down one job |
| `k` / `↑` | move up one job |
| `g` | press twice for the first job |
| `G` | go to the last job |
| `^d` | move half a screen down |
| `^u` | move half a screen up |
| `pgdn` | move a page down |
| `pgup` | move a page up |
| `←` / `h` / `⇧tab` | previous tab |
| `→` / `l` / `tab` | next tab |
| `1` | jump to the new tab |
| `2` | jump to the to apply tab |
| `3` | jump to the applied tab |
| `4` | jump to the skip tab |
| `5` | jump to the all tab |
| `space` | space twice: applied (alias for a) |
| `S` | skip and move on (alias for s) |
| `enter` | open the job in Chrome |
| `d` | toggle the details pane |
| `/` | search title and company |
| `y` | copy the job URL |
| `m` | add a note to the job |
| `r` | reload from the database |
| `R` | run a scrape in the background |
| `?` | show this help |
| `q` / `^c` / `^q` | quit |

<!-- end tui keys -->

Keys are rebindable per op folder; the help overlay and the footer
follow the same table:

    [tui.keys]
    skipped = "x"

## Commands

| Command | What it does |
|---|---|
| `jobtriage` | the TUI (same as `jobtriage tui`) |
| `jobtriage scrape [LIMIT]` | run the boards; `-s NAME`, `--raw`, `--explain` |
| `jobtriage sources` | the boards: list, `info`, `new`, `test`, `enable`, `disable` |
| `jobtriage list [LIMIT]` | query the database; `--status`, `--since`, `--source`, `--search`, `--json` |
| `jobtriage show ID` | one job: every field, notes, history, attachments |
| `jobtriage open ID` | the posting in the apply browser |
| `jobtriage status ID SLUG` | set a job's status |
| `jobtriage note ID TEXT` | append a note |
| `jobtriage attach ID FILE` | record a file (a cover letter) against a job |
| `jobtriage export` | the rows as CSV, or `--format json` |
| `jobtriage config` | `show`, `edit`, `get`, `set`, `validate` |
| `jobtriage filter test TITLE` | why one title passes or not |
| `jobtriage db` | `path`, `shell`, `schema`, `import-sheet old.csv` |
| `jobtriage skills` | list the agent skills; `install --global --agent …` |
| `jobtriage mcp` | the MCP server over stdio |
| `jobtriage doctor [--offline]` | check the setup |
| `jobtriage where` | print the op folder's path |
| `jobtriage --version` | build, workspace path, database schema |

Every command takes `--help`; `jobtriage <cmd> --help` is the reference
for its flags. The config keys are in [docs/config.md](docs/config.md),
the boards and the plugin contract in
[docs/scrapers.md](docs/scrapers.md). `-v`/`--verbose` before the
command puts DEBUG detail on stderr. (`uv run jobtriage …` works from a
checkout; `python -m jobtriage` too.)

## Filtering

Two gates. **Age**: anything older than `search.max_age_days` (21 by
default) is dropped; a job with no date at all is assumed new. **Title**:
the `[filter]` table, starting from a shipped preset. A fresh folder
has no title filter (`preset = "any"`), so the board's category is the
only narrowing.

`jobtriage filter test "Senior React Native Engineer"` says why one
title passes or not. Every key and every preset is in
[docs/config.md](docs/config.md#filter); the reasoning behind the age
cutoff and the undated rule — and how the dedup keys work — is in
[docs/design.md](docs/design.md).

## The database

`jobtriage.db` in the op folder (gitignored by the shipped template) is
the single source of truth: every job the pipeline has seen, its
status, your notes, and a history row for every status change. `list`
and `export` read it without scraping; `show` prints one job in full;
`status`, `note` and `attach` are the writes.

Ad hoc questions go through sqlite3:

    jobtriage db shell 'select title, company, url from jobs order by published desc limit 10'
    jobtriage db schema       # the tables and columns, with comments

Coming from an old Google Sheet: `jobtriage db import-sheet old.csv`
(File → Download → CSV) moves its statuses over, matching by url first
and then by folded title and company; `--dry-run` first. It is a
one-time bridge, not part of the daily workflow. The dedup key and the
column list are in
[docs/design.md](docs/design.md#dedup-keys-and-the-jobs-table).

## Config

Everything you might change is `jobtriage.toml` in the op folder: where
you can work from, families, age and the per-board limit, the title
filter, disabled boards and per-board overrides, the browser, the
scraper's User-Agent, the TUI's startup tab and key bindings. Every key
ships commented out, so a fresh folder runs on the defaults until you
fill something in.

`jobtriage config show` prints every effective value with the layer
that set it, `config set` edits the file without losing its comments,
and `config validate` names a typo with a suggestion. The reference is
[docs/config.md](docs/config.md).

## Agents and skills

`init` ships eight agent skills in `<op>/skills/`: an umbrella
`jobtriage` page, the database and triage workflows, cover letters,
applications, scrapers, filter tuning and setup. It links them from
`.agents/skills`, `.claude/skills` and `.opencode/skills` (and
`CLAUDE.md` to `AGENTS.md`), so an agent opened in the folder finds
them by itself.

    jobtriage skills                                # what ships and what's installed
    jobtriage skills install --global --agent all   # copy them for the agents
    jobtriage mcp                                   # a stdio MCP server instead

`skills install --global` copies them into `~/.claude/skills`,
`~/.codex/skills` and `~/.config/opencode/skills` for use outside the
folder. `jobtriage mcp` is for clients without skill support: it
exposes typed reads and writes (`list_jobs`, `get_job`, `set_status`,
`add_note`, `attach`, `scrape`, `filter_test`) and never submits an
application. Triggers, discovery folders, the tool list and client
config: [docs/skills.md](docs/skills.md).

## Plugins

Fifteen boards ship in the package. Any Python file in
`<op>/scrapers/` is also a board — no registration, no fork, no
reinstall: `jobtriage sources new myboard` writes one from the shipped
template, `sources test myboard` runs it with limit 5 and names any
contract problem, and `sources` shows every board and its health. The
plugin contract, the worked example, the research playbook and the
notes for every board are in [docs/scrapers.md](docs/scrapers.md).

## doctor

`doctor` is the first thing to run when something is off. One line per
check — the workspace it found, the config, the database schema, the
browser and its CDP port, every plugin, the shipped skills, the profile
files — and a `problem` line carries the command that fixes it. It exits
1 when anything is broken. Its only network use is one `HEAD` request
per enabled board to the board's homepage. `--offline` runs the local
checks only — workspace, config, database, plugins, skills, profile —
and skips the three that depend on the machine: the browser, its CDP
port and the boards. That is the mode CI uses, and the right one for a
headless box or a support request that starts with "is this install
sound?".

## Logging

Every command also appends to `.jobtriage/logs/jobtriage.log`: DEBUG
detail, rotated at about a megabyte with three older copies kept.
`-v`/`--verbose` before the command puts the same detail on stderr:

    jobtriage -v scrape            # requests, board timings, every warning

## Responsible use

jobtriage is one person's reader for boards that publish jobs publicly;
it is not a crawler farm.

- A default run asks each enabled board for its 50 newest postings, one
  pass. Boards that paginate stay within their declared `MAX_PAGES`
  cap, and `POLITE_DELAY` puts a minimum gap between a board's requests
  (`src/jobtriage/plugin/http.py` enforces it for the whole run).
- Each built-in declares `TERMS` — what the board asks of a scraper:
  a link back, a credit, a request rate. `jobtriage sources info NAME`
  prints it. The URLs stored are the canonical ones several boards'
  terms require (remotefrontendjobs' `/{id}` page, weworkremotely's own
  links), and remoteok's "Remote OK" credit is carried with its link.
- `euremotejobs` ships disabled because its terms ban automated
  collection outright. `jobtriage sources enable euremotejobs` is how
  you accept them after reading `sources info euremotejobs`.
- Requests send a desktop-browser User-Agent by default, because several
  boards refuse a generic one. If you would rather identify the tool,
  set `[network] user_agent` in `jobtriage.toml` (see docs/config.md);
  a board that demands its own string still gets it.
- Run it when you will read the results. There is no scheduler and no
  queue — a scrape is one pass you started — and the only retry is a
  single reconnect after a dropped connection.

## Privacy

No telemetry, no account, no server of ours. The tool reads and writes
only the op folder, and its network traffic goes to two places: the job
boards you enabled, and any agent or API you choose to run yourself.
Delete the op folder and everything jobtriage knows is gone; the only
other file it writes is the one-line pointer in your platform config
directory that says where that folder is.

## Documentation and contributing

- [docs/config.md](docs/config.md) — every config key, the location
  vocabulary, the presets
- [docs/scrapers.md](docs/scrapers.md) — the plugin contract, discovery,
  the board notes
- [docs/skills.md](docs/skills.md) — the eight skills and the MCP server
- [docs/design.md](docs/design.md) — why: the cache keys, the one
  transaction, the location model, the registry tiers, the version tracks
- [docs/research.md](docs/research.md) — the investigations that chose
  the boards
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup, tests, how to add a
  board, the commit convention
- [CHANGELOG.md](CHANGELOG.md) — what changed, release by release
- [SECURITY.md](SECURITY.md) — the trust model and how to report a
  problem
