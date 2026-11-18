---
name: tune-filters
description: Use when the user says they are getting too many wrong jobs, missing jobs they expected, or wants to change the role, location, age or title filter in a jobtriage workspace. Diagnoses with filter test and scrape --explain, proposes config changes in plain words, sets them, and confirms.
---

# Tuning the filter

A scrape has three gates before a job is stored: the board's own
category, the age cutoff, and the title filter. Location is applied by
each board through the config's vocabulary. Change one gate at a time
and confirm the change with the same tools that diagnosed it.

`docs/config.md` in the jobtriage repo is the full reference for every
key.

## 1. Find the gate

    jobtriage config show --json
    jobtriage config get search.locations
    jobtriage config get filter.preset

`config show` prints each value with the layer that set it (`default`,
`preset:NAME`, `file`, `env:NAME`, `flag`), so "why is it 20?" has an
answer. The keys that decide relevance:

| Key | What it changes |
| --- | --- |
| `search.locations` | where the user can work: `worldwide`, region tokens, ISO codes |
| `search.families` | the boards' category parameters |
| `search.max_age_days` | how old a posting can be (undated counts as new) |
| `search.limit` | how many each board is asked for; volume, not relevance |
| `filter.preset` | the starting lists: `web-engineer`, `backend-engineer`, `data`, `designer`, `product-manager`, `any` |
| `filter.require_any` | the first positive gate; when non-empty the title must match |
| `filter.include_any` | the second positive gate; both must pass |
| `filter.exclude_any` | the turnaway gate; a match drops the title |
| `filter.exclude_companies` | company-name fragments, not regexes |

Then ask the title filter about one title:

    jobtriage filter test "Senior React Native Engineer"

The first line is stable: `kept: ...` or `dropped: ...`, the gate and
the word it matched. Exit status is the verdict (0 kept, 1 dropped).
`--preset NAME` tries another preset without touching the file.

For a run-wide answer, explain one board:

    jobtriage scrape -s remotive --explain

It prints one line per board on stderr: `fetched`, then `age`, `title`,
`duplicate`, `kept`, and the three most common title reasons with the
gate and the word. Use `-s` while tuning so the run stays cheap;
drop it for the final check.

Read the split before changing anything:

- large `age` — the board is sending old postings; lower is not the fix
  unless the user wants a shorter window.
- large `title` with one reason repeated — that reason names the gate
  and word to change.
- large `duplicate` — other boards already carry those jobs; no filter
  change needed.
- low `fetched` — the board or its category, not the filter.

## 2. Choose the smallest change

- **Too many of a kind** (management, a stack, a language): add one
  fragment to `filter.exclude_any_extra`. A fragment is a regex,
  case-insensitive and word-boundary matched: `manager`, `ios`,
  `react native`.
- **A whole company**: `filter.exclude_companies` is a plain name, not
  a regex; `"Acme"` drops `Acme Inc.` and `ACME, Ltd.`.
- **Missing a kind** (titles that should pass): add to
  `filter.include_any_extra` first; both positive gates must pass, so
  include is the safer add. Change the preset only when the whole
  discipline is wrong.
- **Wrong discipline**: change `filter.preset`, then add the file's
  own lists on top. `exclude_any` in the file *extends* the preset;
  `require_any`, `include_any` and `exclude_companies` replace it.
- **Location**: fix `search.locations` rather than the title filter.
  `worldwide` matches boards' "anywhere" tags; a country is its ISO
  code after loading (`GB`, not "United Kingdom"). A board that cannot
  serve the list is skipped with a notice.
- **A noisy board, not a noisy title**: `jobtriage sources disable NAME`
  (and `enable` to undo). This is not a config-set change.
- **Age**: `search.max_age_days`. `0` means now; date-only postings
  count as UTC midnight and fall outside a very tight cutoff.

Do not stack three changes and hope. One change, one explanation, one
confirmation.

## 3. Explain, then set

Before writing, say what will change and what it will affect, in plain
words:

    "Adding `manager` to exclude_any would drop 12 of the 31 titles the
     last remotive run removed for the title gate, including the two
     engineering-manager roles you skipped. It would also drop
     'Manager, Frontend Platform'. Keep it?"

Wait for agreement. Then:

    jobtriage config set filter.exclude_any_extra '["manager"]'

`config set` parses the value as TOML first, keeps the file's comments,
validates the whole config and rolls the write back if the value cannot
be honoured. An invalid regex fails here, not at scrape time. Quote the
array for the shell.

For a preset:

    jobtriage config set filter.preset designer

For a location list:

    jobtriage config set search.locations '["emea", "GB"]'

## 4. Confirm

Re-run the same question:

    jobtriage filter test "Engineering Manager"
    jobtriage filter test "<a title that should now pass>"
    jobtriage scrape -s remotive --explain

Compare the `title` count and the top reasons with the run before. If
the change did the wrong thing (dropped a title the user wanted),
revert it with another `config set` or remove the fragment from the
list; do not leave a bad filter in place because it was already
written.

## Rules

- One change at a time; explain before writing; confirm with the same
  test that found the problem.
- Write through `jobtriage config set`, not by editing the file, so
  validation runs and comments survive. `config edit` is for the user.
- `filter test` answers the title alone. Age needs a date and
  `exclude_companies` needs a company; neither is a title question.
- A filter is a preference, not a verdict. If the user cannot say what
  "too many" means, ask for two example titles and start there.
