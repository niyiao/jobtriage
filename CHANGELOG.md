# Changelog

All notable changes to this project are documented here. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/). The current
release in `pyproject.toml` is 0.3.0. The entries before it are
reconstructed from the commit history, which is short and linear:
0.1.0 is the first commit, 0.2.0 the database rewrite.

## [0.3.0] - 2026-11-19

### Added

- The workspace — the "op folder" — holding the config, database,
  profile, letters, plugins, skills and browser profile. `jobtriage
  init` builds it from the shipped templates and never overwrites a
  file; `init --upgrade` replaces the untouched templates, leaves
  edited ones alone and saves the new version beside them as
  `<file>.new`.
- The CLI restructured into one module per command (scrape, sources,
  list, show, open, status, note, attach, export, config, filter, db,
  skills, mcp, tui, init, doctor). `--workspace PATH`, `JOBTRIAGE_HOME`
  and a walk up from the current directory all find the folder.
- The SQLite store became the one API the CLI, the TUI and the MCP
  server share: `ingest` with pointer-data updates, the listing queries,
  counts, attachments and the TUI's `Tracker`.
- The TUI's quality-of-life pass: one key per status, undo (`u`), tabs
  on the arrow keys, a details pane, search, notes, URL copy, a
  remembered tab and cursor, a background scrape (`R`) and rebindable
  keys. The key map is generated from one table into the footer, the
  `?` overlay and this repository's README.
- The config system: `jobtriage.toml` with `[search]`, `[filter]`,
  `[letters]`, `[sources]`, `[browser]`, `[network]` and `[tui]`;
  `JOBTRIAGE_*` variables and CLI flags over the file; provenance in
  `config show`; `config edit/get/set/validate`.
- One location vocabulary (`geo.py`): `worldwide`, twenty-one regions and
  ISO countries with aliases, translated per board from the board's own
  declarations. Title-filter presets ship in the package and are shared
  by the CLI and `init`'s guided questions.
- The plugin system: `jobtriage.plugin` (`http`, `parse`, `dates`,
  `geo`, `Job`, `API_VERSION`), a three-tier registry (built-ins,
  installed packages, `<op>/scrapers/`), `sources` / `info` / `new` /
  `test` / `enable` / `disable`, per-board health history in
  `source_runs`, and the fifteen built-ins rewritten behind the
  contract.
- Eight agent skills, `jobtriage skills` and `skills install --global`,
  the op folder's `AGENTS.md`/`CLAUDE.md` discovery links, and
  `jobtriage mcp`, a stdio MCP server with seven tools.
- `jobtriage doctor` — workspace, config, schema, browser, port,
  plugins, skills, profile and boards, with `--offline` for the local
  checks — and cross-platform browser finding, launched through CDP.
- Logging: a rotating file under `<op>/.jobtriage/logs/` for every
  command, and `-v`/`--verbose` for the same detail on stderr.
- Packaging and release plumbing: the 0.3.0 metadata, the Python 3.11
  floor, a wheel-content test, `--version` printing the workspace and
  schema, GitHub Actions CI (lint, the macOS/Linux/Windows matrix, a
  wheel-install smoke test), the nightly board canary and the
  trusted-publishing release workflow.

### Changed

- `euremotejobs` ships disabled: its terms ban automated collection.
- Scraper warnings, board failures and the run summary follow the
  logging rules; every built-in declares `TERMS` and sends an honest,
  configurable User-Agent.

## [0.2.0] - 2026-11-18

### Added

- The SQLite database replaced the Google Sheet as the source of truth:
  stable job ids, statuses, notes, status history, attachments, board
  health, migrations with a file backup before each schema change, and
  `jobtriage db path`, `shell`, `schema` and `import-sheet`.

### Changed

- Adopted ruff for formatting and linting: tabs and single quotes.

## [0.1.0] - 2026-11-17

### Added

- The first working CLI: scrape the remote job boards, keep the titles
  that match the filter, and record what was seen and applied.
