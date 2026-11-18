# Contributing

jobtriage is the CLI and nothing personal. Everything that belongs to
one user — a name, a resume, a credential, a preference — lives in
their op folder, never in this repository. [AGENTS.md](AGENTS.md) is
the short version of that rule; read it before your first change.

## Set up

    uv sync                     # the venv; the dev group has pytest and ruff
    uv run pytest               # the suite
    uv run ruff format          # format
    uv run ruff check           # lint
    uv run jobtriage --help     # the CLI, from the checkout

Python 3.11 is the floor (`tomllib`); CI runs 3.11 and 3.13 on macOS,
Linux and Windows. `uv run jobtriage …` and `python -m jobtriage` both
work without installing the package.

## Tests are the contract

Run the suite before and after a change. `tests/test_relevance.py`,
`tests/test_store.py` and `tests/test_pipeline.py` are characterisation
tests — they pin today's behaviour, so refactors happen under them.
When behaviour changes, extend the test first and let it fail for the
right reason.

Some tests are drift tests: the README's TUI key map
(`tests/tui/test_readme.py`), the umbrella skill's CLI reference and
the db skill's schema reference (`tests/test_skill_refs.py`), and the
wheel's contents (`tests/test_packaging.py`). Regenerate the artefact
from the code or the test's printed output — never hand-edit the
generated block.

## Style

Tabs, single quotes, long docstrings that explain *why* over *what*.
Ruff enforces the shape (`indent-style = "tab"`, `quote-style =
"single"`); the docstrings are the part no formatter can check.

Every module that needs a path takes it as an argument. Paths come from
`src/jobtriage/workspace.py` and nowhere else; tests pass a `tmp_path`
workspace. CLI failures are one lowercase line on stderr with a useful
exit code, not a traceback (`JOBTRIAGE_TRACEBACK=1` is the debugging
escape hatch).

## Adding a board

A board is a plugin either way, so read [docs/scrapers.md](docs/scrapers.md)
first: it has the contract, the optional declarations, the template,
offline fixtures and the research playbook.

- **A plugin** goes in the user's op folder:
  `jobtriage sources new myboard` writes `<op>/scrapers/myboard.py`
  from the shipped template. No change to this repository, no release.
  `jobtriage sources test myboard` is the feedback loop.
- **A built-in** is a module in `src/jobtriage/scrapers/` with `NAME`,
  `ALIASES`, `recent_jobs` and the location declarations. Add it to the
  inventory tests and give it a recorded-response parse test like every
  other board (the fixtures live under `tests/fixtures/`); `fetch` is
  the only function that touches the network, so `parse` stays pure.
  Its module docstring is the board's research note — `sources info`
  prints it.

`jobtriage sources test NAME` runs the real request; the suite must
never need the network, so a new fixture is part of the change.

## Where the docs live

- `README.md` — the user's page: install, quickstart, commands, doctor,
  logging, responsible use, privacy
- `docs/config.md` — every `jobtriage.toml` key, the location
  vocabulary, the title-filter presets
- `docs/scrapers.md` — the plugin contract, discovery, the board notes,
  the research playbook
- `docs/skills.md` — the eight agent skills, discovery folders, global
  install, and `jobtriage mcp`
- `docs/design.md` — why the tool is shaped this way: the filter, the
  dedup keys, the scrape transaction, the location model, the registry
  tiers, config provenance, the version tracks
- `docs/research.md` — the investigations that decided which boards
  ship

User documentation stays in `README.md` and the `docs/` pages; a design
note belongs in `docs/design.md`, not the README.

## Commit messages

The log is descriptive lowercase one-liners, not Conventional Commits:

    packaging, doctor, browser finding (plan 08 steps 4-7): metadata + 3.11 floor, wheel test ...
    the database replaces Google Sheets (plan 02); adopt ruff

Say what changed and why in the subject; put the detail the subject
cannot carry after the colon. One logical change per commit. Do not
mention a personal detail, a real name, a board credential or a local
path in a message, a test, a fixture or a doc.

## Releases and version tracks

Three numbers move on their own:

- **CLI semver** — `pyproject.toml` `[project].version` and
  `src/jobtriage/__init__.py` (`__version__`), bumped together. The
  tag is `vX.Y.Z` and must match.
- **Plugin API** — `src/jobtriage/plugin/__init__.py` (`API_VERSION`).
  Bump only for a change that breaks an existing plugin.
- **Database schema** — `src/jobtriage/db.py` (`LATEST`). Bump it when
  you add a migration, never edit a migration that has shipped; the
  database stores its own number in `PRAGMA user_version`.

[docs/design.md](docs/design.md#three-version-tracks) has the exact
locations and what each bump means. Add the change to
[CHANGELOG.md](CHANGELOG.md) under `Unreleased`. Pushing a `v*` tag
runs `.github/workflows/release.yml`, which builds and publishes to
PyPI through trusted publishing — it needs the PyPI publisher
configured first (the workflow header says how).
