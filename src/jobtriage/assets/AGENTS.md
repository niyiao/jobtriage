# The op folder

This folder is one person's job search. It holds the config, the
database, the resume and details, cover letters, board plugins, agent
skills and the browser profile. `jobtriage` finds it from here with no
flags and no environment variables. It is personal data: do not copy
its contents into the jobtriage repository.

## Ground rules

- Use the `jobtriage` CLI. Read with `--json`; write with
  `jobtriage status`, `note` and `attach`. Do not write to
  `jobtriage.db` with SQL; reading with `jobtriage db shell` is fine.
- Never submit an application, send an email or create an account
  unless the user says so in this session.
- Use this folder's browser, `.browser-profile`, headed and attached
  over the CDP port from `jobtriage config get browser.port` (default
  9222). Never use a fresh automation profile.
- Delete HAR files when the research is done. They hold cookies and
  tokens.
- Facts about the user come only from `profile/`. Ask for anything that
  is not there. Do not invent experience, dates, numbers or eligibility.

## Skills

The skills live in `skills/<name>/SKILL.md`; `.agents/skills`,
`.claude/skills` and `.opencode/skills` link to that folder. Read
`skills/jobtriage/SKILL.md` first; it maps the commands and the status
vocabulary.

| Skill | Use it for |
| --- | --- |
| `jobtriage` | the umbrella: commands, statuses, workspace layout, where to go next |
| `jobtriage-db` | counts and reports: list filters, the schema, read-only SQL recipes |
| `triage` | going through the new queue and choosing what to apply to |
| `cover-letter` | writing, saving and attaching a cover letter for one job |
| `fill-application` | filling an application in the browser, stopping before submit |
| `add-scraper` | researching a board and writing or fixing a scraper plugin |
| `tune-filters` | changing locations, families, age or the title filter |
| `setup` | onboarding a fresh profile from a resume |

`jobtriage init --upgrade` refreshes untouched skills after a package
update and leaves edited ones alone, with the new version beside them
as `<file>.new`.

## Profile and writing

`profile/RESUME.md` and `profile/DETAILS.md` are what an agent reads for
the user's details. `profile/resume.pdf` is only for upload fields, not
for reading. `writing-style.md` in this folder holds the prose rules for
letters and other writing: match it, but no humour in formal documents.

Cover letters go in `letters/`, one file per application, named
`<company>-<role>.md`, and are attached with `jobtriage attach ID
letters/FILE.md`.

## Browser

The apply and research browser is this folder's Chrome profile,
`.browser-profile/`. Launch the real Chrome on it with a remote debugging
port, then attach agent-browser:

    jobtriage where          # the folder, for --user-data-dir
    jobtriage config get browser.port

The triage TUI and `jobtriage open ID` start the same profile and port
themselves. A separate automation profile has no sessions and sites
treat it differently — do not use one. Never commit `.browser-profile/`,
HAR files or `.env`.

## Pointers

- `jobtriage --help`; `skills/jobtriage/references/cli.md` for every
  command and flag.
- `jobtriage config show` for the effective settings and the layer each
  came from.
- The jobtriage repo's `docs/scrapers.md` for the plugin contract,
  board notes and browser launch commands, and `docs/config.md` for
  every config key.
