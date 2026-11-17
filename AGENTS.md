# Contributing to jobtriage

jobtriage is the CLI (`jobtriage`) and nothing personal. Everything
that belongs to one user lives in their op folder — `jobtriage init`
creates it (default `~/jobtriage`) from the templates in
`src/jobtriage/assets/`. Never put a name, a resume, a credential or
a personal preference in this repo: that is op-folder material.

## Running things

    uv sync                     # set up the venv (the dev group has pytest)
    uv run pytest               # the suite; run it before and after changes
    uv run ruff format          # format (tabs that render as 2 spaces, single quotes)
    uv run ruff check           # lint; noqa with a reason where it's deliberate
    uv run jobtriage --help     # the CLI, from the repo

Tests are the contract. tests/test_relevance.py, test_store.py and
test_pipeline.py are characterisation tests — they pin today's
behaviour so plans 01–04 can refactor under them; keep them green
and extend them first when changing behaviour.

## Code style

Tabs, single quotes, long docstrings explaining *why* over *what*.
Every module that needs a path takes it as an argument — paths come
from `src/jobtriage/workspace.py` and nowhere else.

## How the docs are organised

- `README.md` — the user's page: what it does, install, quickstart,
  the command list, doctor, logging, responsible use and privacy
- `docs/config.md` — every `jobtriage.toml` key, the location
  vocabulary and the title-filter presets
- `docs/scrapers.md` — the plugin contract and template, the
  per-board research: how each board was cracked, the research
  playbook and browser setup
- `docs/skills.md` — the eight agent skills: triggers, discovery
  folders, global install; and `jobtriage mcp`, the stdio MCP server
  and its tool list
- `docs/design.md` — why the tool is shaped this way: the filter
  internals, the dedup keys, the scrape transaction, the location
  model, the registry tiers, config provenance, the template/upgrade
  rules and the three version tracks
- `docs/research.md` — the investigations that decided which boards
  those are

`CONTRIBUTING.md` is the entry point for a code change (setup, tests,
style, releases); `CHANGELOG.md` and `SECURITY.md` are the other two
root pages a contributor should skim.

The personal workflow instructions (cover letters, applying, browser
automation) ship as the op folder's `AGENTS.md` template in
`src/jobtriage/assets/` — they are a user's, not the repo's.
