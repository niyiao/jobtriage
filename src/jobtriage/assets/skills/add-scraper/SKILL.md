---
name: add-scraper
description: Use when the user wants to add a job board, scrape a board's URL, fix a built-in board that stopped working, or write a scraper plugin in a jobtriage workspace. Covers the research order (curl, SSR state, HAR only if needed), the plugin contract, testing with sources test, and record notes.
---

# Adding a board

A board is one Python file in `<op>/scrapers/`. Nothing is registered
and nothing in the installed package is edited: the registry finds the
file, and a workspace file with a built-in's `NAME` overrides it.

Read `references/contract.md` before writing code. If the jobtriage
source checkout is available, `docs/scrapers.md` has the long version:
the research playbook, notes for every built-in board, and
`docs/examples/demo.py` as a finished plugin.

## 1. Terms and volume first

Open the board's terms, robots file and API documentation before making
a request. If the terms ban automated collection, say so and stop; the
user can decide to ask the board for permission. Keep the request count
low: one request per run when the API allows it, a page or two when it
does not. Never bypass a login, a paywall or a rate limit.

## 2. Research in this order

**curl the listing page.** Look at the HTML before opening a browser:

    curl -sL -A 'Mozilla/5.0' 'https://board.example/jobs' | grep -oE '__NEXT_DATA__|__NUXT__|__PINIA_STATE__|__INITIAL_SEARCH_RESULTS__|wp-json|/api/[^" ]+' | sort -u

Many boards ship the first page of jobs in server-rendered state, and a
public JSON API often serves the rest. That path is the cheapest to
keep alive.

**Record the real traffic only if the data is client-side.** Use the op
browser, start a HAR, reload, scroll, paginate, click the filters:

    agent-browser --cdp 9222 --pin-tab --session <board> network har start
    agent-browser --cdp 9222 --pin-tab --session <board> open <listing url>
    # interact
    agent-browser --cdp 9222 --pin-tab --session <board> network har stop /tmp/<board>.har
    jq -r '.log.entries[].request.url' /tmp/<board>.har | sort -u

Read the Fetch/XHR requests: URL, params, headers, response shape.
**Delete the HAR when done** — it holds cookies and tokens.

**Replay without cookies.** `curl` the request you found, with no login
and no headers you cannot justify. If it answers, the browser is
research-only and the plugin needs no session.

**Learn the knobs.** Pagination (`limit`/`offset`, cursor, page number,
or everything in one response), sort (is "newest" really newest, or are
sponsored rows pinned?), category filters, and whether location is
filtered by the server or by the client. A page size that silently caps
at 25 is common; check the count that comes back.

**Map the location vocabulary.** There is no standard wording for
"worldwide" or "EMEA". Find what tags the board's jobs carry (or what
its own filter sends) and declare them:

- `WORLDWIDE` — the board's tags for anywhere, verbatim.
- `REGIONS` — canonical token (`emea`, `apac`, `latam`, …) to the
  board's tags.
- `COUNTRY_STYLE` — `'name'`, `'iso2'` or `'none'`.
- `COUNTRY_ALIASES` — ISO code to the board's exact wording; map
  countries it lacks to `''` so the filter stays exact.
- `COVERS` — the region a single-region board serves.

`jobtriage.plugin.geo.board_tags` turns `[search].locations` into the
tuple your `recent_jobs` receives.

## 3. Write the plugin

    jobtriage sources new myboard

writes `<op>/scrapers/myboard.py` from the shipped template, with `NAME`
filled in. Keep the file's `NAME`; fill the request, the parser and the
declarations. The minimum contract is:

```python
NAME = 'myboard'


def recent_jobs(*, locations=None, limit=50, **options) -> list[dict]: ...
```

Return at most `limit` jobs, newest first. A job has `title`, `company`,
`url` (absolute http/https), `published` (ISO 8601 or `None`),
`locations` (the board's own tags) and `salary` (free text or `None`).
Use `jobtriage.plugin.dates` for the three date shapes the boards use,
and `jobtriage.plugin.http` for requests; it enforces `POLITE_DELAY`.

Split `fetch()` (the one network call) from `parse(raw, ...)` (pure), the
way the built-ins do. It makes the parser testable from a recorded
response.

Add `TITLE`, `HOMEPAGE`, `TERMS`, `ALIASES`, `POLITE_DELAY`, `FAMILIES`
and `server_locations` only when they say something true. Leave
`API_VERSION` out unless you are deliberately targeting a newer
`jobtriage.plugin`.

## 4. Test until clean

    jobtriage sources test myboard

This runs the board with limit 5 through the same path a scrape uses. A
bad row is numbered and explained; fix the parser and run it again. Then
check both scrape modes:

    jobtriage scrape -s myboard 5
    jobtriage scrape -s myboard --all 5

`--all` removes the location filter, so it shows whether the board's
location declarations are too narrow. `jobtriage sources test --all`
runs every enabled board and catches a broken override elsewhere.

Record what you learned in the module's docstring. `jobtriage sources
info myboard` prints it, and the next person reads it before the code:
the endpoint, the pagination, the sort lie, the location vocabulary and
the terms.

## 5. Fixing a built-in

A workspace file with the same `NAME` overrides the built-in, with one
notice on stderr. To patch a broken built-in:

1. Find the installed module: `python -c "import jobtriage.scrapers.remotive as m; print(m.__file__)"`.
2. Copy it to `<op>/scrapers/remotive.py`.
3. Change the broken part; keep `NAME` so it overrides.
4. `jobtriage sources test remotive` then `jobtriage scrape -s remotive 5`.

Do not edit anything under `src/` or the installed package. Add a line
to the docstring saying what was patched and why, so the override can be
dropped when the package catches up.

## Rules

- Plugins are ordinary Python run with the user's permissions. Read a
  scraper before running it; do not paste code from an untrusted source.
- Respect terms and rate limits. `POLITE_DELAY` is not decoration.
- Changes go in `<op>/scrapers/`; the package stays as shipped.
- A board that needs a login or a CAPTCHA is not a scraping problem to
  defeat — stop and tell the user.
