---
name: jobtriage
description: Use for any request about the job search in a jobtriage op folder — scraping job boards, reading or updating the job tracker, statuses, notes, the database, the TUI, or deciding which jobtriage skill handles a task. Read this first; it maps the commands, the status vocabulary, the workspace layout and the other skills.
---

# jobtriage

This folder is the op folder: one person's job search. The `jobtriage`
command reads and writes everything here, and finds the folder from
inside it without flags or environment variables.

The other skills in `skills/` cover one workflow each. Read this page
first, then load the skill for the task:

| Skill | Use it for |
| --- | --- |
| `jobtriage-db` | counts, reports and questions answered with list filters or read-only SQL |
| `triage` | going through the `new` queue with the user and choosing what to apply to |
| `cover-letter` | writing, saving and attaching a cover letter for one job |
| `fill-application` | filling an application in the op browser, stopping before submit |
| `add-scraper` | researching a board and writing or fixing a scraper plugin |
| `tune-filters` | changing what a scrape keeps: locations, families, title filter, age |
| `setup` | onboarding a fresh workspace from a resume |

## The ground rules

- Use the `jobtriage` CLI. Read with `--json`; write with `status`,
  `note` and `attach`. Do not write to `jobtriage.db` with SQL; reading
  with `jobtriage db shell` is fine.
- Never submit an application, send an email, or create an account
  unless the user says so in this session. Filling a form is not
  permission to submit it.
- Browse with this folder's browser, `.browser-profile`, headed and
  attached over the CDP port from `jobtriage config get browser.port`
  (default 9222). Never use a fresh automation profile.
- Delete HAR files when the research is done; they hold cookies and
  tokens.
- Facts about the user come only from `profile/`. Ask for anything that
  is not there. Do not invent experience, dates, numbers or
  eligibility.

## Commands

Run `jobtriage COMMAND --help` for exact flags, or read
`skills/jobtriage/references/cli.md` for the full generated reference.

| Command | What it does | When |
| --- | --- | --- |
| `jobtriage scrape` | scrape every enabled board, filter, ingest | fetching new jobs; `-s NAME` for one board, `N` for the limit, `--explain` to see why jobs were dropped, `--raw` to see the unfiltered result without saving |
| `jobtriage list` | the newest jobs already in the database | reading the queue; `--status`, `--since 7d`, `--source`, `--search`, `--order`, `--limit`, `--json` |
| `jobtriage show ID` | one job: every field, notes, status history, attachments | before writing to or about a job; `--json` for agents |
| `jobtriage status ID STATUS` | set `new`, `skipped`, `to_apply` or `applied` | after the user decides |
| `jobtriage note ID TEXT` | append a note | recording a call, a recruiter reply, a reason |
| `jobtriage attach ID PATH` | record a file against a job | after writing a cover letter; files under `letters/` default to kind `cover_letter` |
| `jobtriage export` | write the rows as CSV or JSON | a snapshot for a spreadsheet or a report; `--format json` and the same filters as `list` |
| `jobtriage config` | show, get, set, edit, validate `jobtriage.toml` | changing search or filter settings; prefer `config set` over hand editing |
| `jobtriage sources` | list, inspect, scaffold, test, enable, disable boards | see `add-scraper`; `--json` lists every board |
| `jobtriage filter test TITLE` | say why the title filter keeps or drops one title | diagnosing the filter; see `tune-filters` |
| `jobtriage db` | `path`, `shell`, `schema`, `import-sheet` | SQL reading and the one-time old-sheet import |
| `jobtriage skills` | list the shipped skills; install them globally for an agent | onboarding an agent outside this folder |
| `jobtriage open ID` | open the posting in the workspace browser | reading a posting that needs a login or JavaScript |
| `jobtriage tui` | the interactive triage screen | when the user asks to triage by keyboard |
| `jobtriage init` | create or upgrade this folder | only when the folder is missing; `--upgrade` after a package update |
| `jobtriage doctor` | check the folder, config, database, browser, plugins, skills, profile and boards; say how to fix what is wrong | when something does not work, or before asking for help; `--offline` skips the machine-dependent checks (browser, port, boards) and runs the local ones |
| `jobtriage where` | print the op folder path | scripts and browser commands that need the path |

The daily loop is `scrape` → `list --status new` → triage (`triage`
skill) → `to_apply` → write a letter (`cover-letter`) → fill the
application (`fill-application`) → `applied`.

Reading commands accept `--json` and print one document. Errors are one
line on stderr beginning `error:`; exit 1 is a runtime failure, 2 is a
bad argument. There are no prompts when stdin is not a terminal.

## Status vocabulary

The stored slugs, in the order the TUI cycles them:

| Slug | TUI label | Meaning |
| --- | --- | --- |
| `new` | new | just ingested, nobody has looked at it yet |
| `skipped` | skip | looked at and rejected |
| `to_apply` | to apply | chosen; the user intends to apply |
| `applied` | applied | the application was submitted |

`new` is where every scraped job starts. A status change is recorded in
`status_history` with the actor; `show` prints the history. Setting the
status a job already has does nothing and records nothing.

## The workspace

```
jobtriage.toml        config; its presence marks the folder
jobtriage.db          jobs, statuses, notes, history, attachments
profile/              RESUME.md, DETAILS.md, resume.pdf
letters/              one cover letter per application
scrapers/             board plugins; one .py per board
skills/               these skills, linked from .agents/, .claude/, .opencode/
AGENTS.md             this page for agents; CLAUDE.md links to it
writing-style.md      prose style for letters and other writing
.browser-profile/     the Chrome profile for applying and research
.jobtriage/           tool state: version, asset manifest, backups, TUI state
.env                  secrets (API keys), optional
```

`profile/` is the only source of personal facts. `profile/resume.pdf` is
for upload fields; read `profile/RESUME.md` and `profile/DETAILS.md`
when writing or filling anything.

If a command fails with `no workspace`, stop and ask before running
`jobtriage init`: creating a folder is a decision about where the
person's data lives.
