# Scrapers

One module per job board, one CLI. The CLI lists the most recent remote
developer jobs from a board — or from every board at once — keeping only
jobs open worldwide / EMEA by default.

A board is a plugin. Fifteen ship in the package; any Python file in
`<op>/scrapers/` is a board too, with no registration, no fork and no
reinstall. `jobtriage sources` is the inventory, `jobtriage sources new`
starts a file from the template, and `jobtriage sources test` is the
feedback loop. This file documents that system first, then the research
playbook and the notes for every board that was cracked.

## Writing a plugin

A plugin is one Python module. Required:

```python
NAME = 'himalayas'  # unique id, [a-z0-9-]+, stored in jobs.source


def recent_jobs(*, locations=None, limit=50, **options) -> list[dict]: ...
```

`NAME` is what `-s`, `jobs.source` and `[sources.<name>]` use. It must
be lowercase letters, digits and hyphens, and unique across every board
the registry finds. `recent_jobs` returns at most `limit` jobs, newest
first; `locations` is the tuple this board's own location declarations
produced (or `None` for `--all`), and `**options` receives the family
parameters plus anything from `[sources.<name>]`, so a board's own knobs
are configurable without a release.

Everything else is optional, with a default:

| Declaration | Default | What it does |
|---|---|---|
| `ALIASES = ()` | none | Extra names `-s` accepts. Also unique; not stored in `jobs.source`. |
| `TITLE = 'Himalayas'` | the id, title-cased with hyphens as spaces | The board's name in listings. |
| `HOMEPAGE = ''` | none | The site a person can visit. |
| `API_VERSION = 1` | 1 | The plugin contract this file targets. Leave it out unless you deliberately write against a newer `jobtriage.plugin`; a newer number than this build speaks marks the plugin broken. |
| `TERMS = ''` | none | What the board's terms ask of a scraper: a link back, a credit, a request rate. `jobtriage sources info` prints it. |
| `POLITE_DELAY = 0` | 0 | Seconds between requests, enforced by `jobtriage.plugin.http` for the whole `recent_jobs` call. A plugin never sleeps itself. |
| `WORLDWIDE`, `REGIONS`, `COUNTRY_STYLE`, `COUNTRY_ALIASES`, `COVERS` | none | The board's location vocabulary, in the board's own wording. `geo.board_tags` turns the user's `[search].locations` into the tuple handed to `recent_jobs`. A `REGIONS` key is the tag's *true area*, the smallest canonical region that covers it — `'europe': ('Europe',)`, `'emea': ('EMEA',)` — because a wider key makes the tag claim an area the board does not serve. A `COUNTRY_ALIASES` value may be a string or a tuple of strings when the board has two spellings for one country. |
| `FAMILIES = {}` | every family | Job family -> the board's category params. The pipeline passes the entry for the requested family, so the filter is server-side. `{}` means the board has no category parameter and serves everything. |
| `server_locations(prefs)` | none | Declarative: the request params a server-filtered board builds from a preference list. `sources info` reports it; the pipeline passes the same tags as `locations`, and `recent_jobs` derives its own params from them. |
| module docstring | none | The research notes. `jobtriage sources info` prints it. |

`fetch()`/`parse()` are not part of the registry contract, but they are
the built-ins' convention and worth copying: `fetch` is the one function
that touches the network, and `parse(raw, *, locations=None, limit=50)`
is pure, so a recorded response tests the parsing offline. Every
built-in has such a test.

### The returned jobs

A job is `jobtriage.plugin.Job`, a `TypedDict` with these six keys:

| Key | Type | Rule |
|---|---|---|
| `title` | string | non-empty |
| `company` | string | non-empty |
| `url` | string | absolute `http(s)` |
| `published` | ISO 8601 string or `None` | `dates.iso`, `dates.relative` and `dates.yearless` cover the formats the boards use; the pipeline sorts these strings, so "2 days ago" is not acceptable |
| `locations` | list of strings | the board's own tags, not canonical geo tokens; a tuple is accepted and normalised to a list |
| `salary` | string or `None` | free text — boards bundle currency, interval and equity in it — so a number must be formatted here |

Extra keys are kept; the pipeline adds `role` later. Every row goes
through `registry.validate` before anything is written: a row with a
missing key, an empty title or company, a relative URL, an unparseable
`published`, a non-string location or a numeric salary is dropped and
counted, never stored. `jobtriage sources test` names the row and the
field, in the order the board returned them.

### Start from the template

    jobtriage sources new myboard

writes `<op>/scrapers/myboard.py` from the shipped `_example.py`, with
`NAME` filled in and every optional hook commented. The command refuses
a bad `NAME`, `all` (the CLI's meta-board), an existing file, another
board's alias, and a `NAME` a second workspace file already defines.
If the name is an existing board's, it says the new file will override it.

`_example.py` is a working JSON-API board for a made-up host: replace
`API_URL`, the request params and `parse()`, and it is a real board.
[docs/examples/demo.py](examples/demo.py) is the same contract written
as a finished board — the test suite copies it into a temporary op
folder and runs `sources`, `sources test` and `scrape` against it — so
read the two together.

### `sources test` is the feedback loop

    jobtriage sources test myboard

runs the board with `limit` 5 through the same `registry.run` a scrape
uses, so the jobs, the timing and every contract rejection are real.
It records nothing: a test is not a scrape, and board health does not
move because someone checked a plugin.

    $ jobtriage sources test demo
    demo (workspace) · 5 fetched · 5 valid · 0 dropped · 0.31s
      Platform Engineer 1 — Demo Co · 2026-11-18 · Worldwide · https://demo-jobs.test/jobs/1
      Platform Engineer 2 — Demo Co · 2026-11-18 · Worldwide · https://demo-jobs.test/jobs/2
      ...

A bad row is numbered and explained:

    demo (workspace) · 5 fetched · 4 valid · 1 dropped · 0.28s
      ...
      job 3: published '2 days ago' is not ISO 8601 — convert it in parse() with jobtriage.plugin.dates.iso() or dates.relative()

A board that raises is one line, not a traceback:

    error: demo failed after 0.12s: HttpError: HTTP 404 for https://api.demo-jobs.test/v1/jobs

A board that cannot load names the reason:

    error: demo is broken: missing recent_jobs(*, locations, limit, **options)

Exit status is 0 clean, 1 a failed or broken board, 2 an unknown name
(`sources new` uses 2 for a bad `NAME` too). `--all` tests every enabled
board, one block each, and names broken ones first. `sources test NAME`
on a disabled board runs it anyway with a note — naming the board is the
choice, exactly as `scrape -s NAME` does. `--json` prints the same run
as one document (`boards` with `ok`, `error`, the counts and the job
URLs; the `broken` map; an `ok` matching the exit code) — the nightly
canary's input (`scripts/canary_report.py` turns a failing board into
one GitHub issue).

### Discovery and loading

The registry (`src/jobtriage/registry.py`) loads three tiers, later
overriding earlier:

1. **Built-ins** — `jobtriage.scrapers.*`, found with
   `pkgutil.iter_modules`; a new module in the package is a new board.
2. **Installed packages** — the entry-point group `jobtriage.scrapers`,
   so a board pack can be `pip install`ed.
3. **Workspace plugins** — `<op>/scrapers/*.py`, in filename order,
   skipping files whose name starts with `_` (that is what keeps
   `_example.py` a template). They load by path under the module name
   `jobtriage_workspace_plugins.<stem>`.

A board pack installed with pip declares the module under that group in
its `pyproject.toml`:

```toml
[project.entry-points."jobtriage.scrapers"]
myboard = "myboard_pack.board"
```

A later source with the same `NAME` **overrides** an earlier one, with
one notice on stderr:

    scraper: using workspace scraper "remotive" instead of the built-in

That is the point of the workspace tier: a user can patch a broken
built-in the day the board changes, without waiting for a release. The
override keeps the workspace file's aliases and drops the built-in's.

### A broken plugin is one line, never a crash

A plugin that cannot be used is **broken**, and the rest of the run
goes on. `sources` still lists it — as a row with `no` under ENABLED,
then `broken: <name>: <reason>` — and `scrape` prints the same line on
stderr and skips it. The reasons:

- it fails to import (a syntax error, a failed import, an exception at
  module level) — the key is the file stem when no `NAME` was reached
- `NAME` is missing, not a string, or not `[a-z0-9-]+`
- `recent_jobs` is missing or not callable
- `API_VERSION` is not an integer, or targets a newer contract than
  this build speaks
- an alias is already used by another board, or `NAME` is another
  board's alias

Aliases are checked across every tier: a workspace plugin cannot take
an alias a built-in already owns, and it is broken for the attempt
rather than silently shadowing it.

### Aliases, disabled, and `[sources.<name>]`

`-s` accepts a `NAME` or any alias. The two config surfaces that name
a board canonicalise the same way: `[sources].disabled = ["rm"]` and
`[sources.rm]` both mean `remotive`. `jobtriage sources enable` and
`disable` write the canonical name, so `sources disable rm` stores
`remotive` and a later board rename does not leave a second spelling
behind. A canonical table wins when the file has both spellings. A
wrong name stays in the list so `sources` can warn about it and
`sources enable NAME` can remove the stale entry.

### The inventory

`jobtriage sources` prints one row per board — name, aliases, origin
(`built-in`, `package` or `workspace`), enabled, last run and success
rate over the last ten runs — then one indented line per broken board
and per last error. `--json` is one document, `{"boards": [...],
"broken": {...}}`, with `title`, `homepage`, `terms` and the health
fields on each board, for a script or an agent. Listing never fails on
a broken board: showing it with its reason is the point.
`jobtriage sources info NAME` is one board's docstring (the research
notes), terms, location vocabulary, family params and the keyword
arguments `recent_jobs` accepts, read from its signature — the answer
to "what may I put in `[sources.NAME]`?".

### Security

Plugins are arbitrary Python code, run in-process with the user's
permissions. There is no sandbox. The registry loads them from two
places only: the installed environment (the entry-point group) and the
op folder's `scrapers/`. It never loads from the current directory,
and no command changes that. Treat someone else's `scrapers/` folder
like a shell script: read it before running it.

## Running it

    jobtriage scrape             # the pipeline: every board in parallel,
                                 # relevant new jobs into the database
    jobtriage scrape -s rs 20     # remotesource, same pipeline
    jobtriage scrape -s dj       # dynamitejobs
    jobtriage scrape -s dj --all  # no location filter
    jobtriage scrape --raw       # the unfiltered listing, nothing saved
    jobtriage list 20            # the newest jobs in the database, no scraping

A default run filters (21-day cutoff plus a role-title match,
src/jobtriage/relevance.py) and ingests the matches into the database
in one transaction; README.md documents that half. This file
documents the scraping half. `jobtriage` runs
installed (`uv tool install .`) or from the repo (`uv run
jobtriage`); the data lives in the op folder
(`jobtriage init`, README).

`-s/--source` takes `all` or any board `NAME` or alias the registry
found: the fifteen built-ins — `wn`/`workingnomads`,
`rs`/`remotesource`, `dj`/`dynamitejobs`, `jr`/`justremote`,
`rm`/`remotive`, `jg`/`jobgether`, `wwr`/`weworkremotely`,
`rok`/`remoteok`, `ir`/`inclusivelyremote`, `wf`/`wellfound`,
`rc`/`remocate`, `etj`/`eurotechjobs`, `erj`/`euremotejobs`,
`rfj`/`remotefrontendjobs`, `bw`/`bruntwork` — and any plugin in
`<op>/scrapers/`. `jobtriage sources` is the list. Default source is
`all`.

`all` is not a board — it is the CLI's meta-source: every enabled
board scraped in parallel (a thread each, via `scrape_all()` in
src/jobtriage/pipeline.py), each through the location tags its own
declarations give for the user's config (`geo.board_tags`), then
merged newest-first, deduped by company+title keeping the newest (the
same posting rides several boards). The merged pool is not truncated —
the relevance filter is the narrowing step now, and it wants everything
the boards sent. A board that raises is skipped with a warning on
stderr (remotive rate-limits hard); the run only fails if every board
does.

Every request goes through `jobtriage.plugin.http`: a desktop-browser
User-Agent by default (`[network] user_agent` in the config replaces
it), the board's `POLITE_DELAY` as the gap between requests, and a
timeout. `jobtriage sources info NAME` prints the board's `TERMS` —
what it asks of a scraper — and the canonical URLs it wants kept.
euremotejobs ships disabled because its terms ban automated collection;
`sources enable euremotejobs` is the explicit acceptance.

## Layout

    src/jobtriage/cli/scrape.py         `jobtriage scrape`: -s/--source,
                                   [limit], --all, --raw
    src/jobtriage/cli/sources.py        `jobtriage sources`: list, info,
                                   new, test, enable, disable
    src/jobtriage/pipeline.py           scrape_all(): every board in parallel
    src/jobtriage/registry.py           discovery, override and broken rules,
                                   result validation, `run`
    src/jobtriage/plugin/               the public plugin API: http, parse,
                                   dates, geo, Job, LocationPrefs
    src/jobtriage/cli/output.py         shared print_jobs()
    src/jobtriage/relevance.py          the filter: age cutoff, role-title
                                   matcher, fold-tolerant dedup
    src/jobtriage/store.py              the SQLite store (the database, in
                                   the op folder)
    src/jobtriage/scrapers/<board>.py   one module per built-in board
    src/jobtriage/assets/scrapers/      the template `sources new` copies;
                                   `_example.py` is the working board

## How the built-ins are written

Every built-in is a plugin with the declarations above; nothing about
them is special. They declare their vocabularies as data:

- `WORLDWIDE`, `REGIONS`, `COUNTRY_STYLE`, `COUNTRY_ALIASES` — the
  board's tags for areas it is open to, in the board's own wording.
  `geo.board_tags(module, prefs)` turns the user's preference list into
  the tuple the scraper accepts. `REGIONS` keys must state each tag's
  true area — the smallest canonical region covering it — because the
  containment match asks for a tag only when the tag's area covers the
  whole requested region or country; an invented wider key would let a
  narrow job through. An empty `COUNTRY_ALIASES` wording suppresses a
  country the board does not carry, so a partial board stays exact, and
  a tuple value emits every spelling the board uses. `FAMILIES` maps job
  families to that board's category params, and `COVERS` says which
  region a single-region board serves (only eurotechjobs today).
- `recent_jobs(*, locations=None, limit=50, **family_params)` — newest
  first. `locations=None` disables the filter (that's `--all`); family
  params default from `FAMILIES['software']`.
- `server_locations(prefs)` — only on boards that filter location
  server-side (euremotejobs, jobgether, remotefrontendjobs): the
  request params for a preference list, written down so a reviewer and
  `sources info` can see how a tag becomes a query string. The
  pipeline passes the same tags as `locations`; the board's
  `recent_jobs` builds the actual request.

When filtering, fetch the whole board in one request if the API allows
it, so the list is the newest *matching* jobs, not the newest overall.
Every board so far allows a big fetch (see notes below). Then run:

    jobtriage sources test <name>
    jobtriage scrape -s <name> 3
    jobtriage scrape -s <name> --all 2

## Research playbook

How each board here was cracked, in order. Do these steps in order too.

1. **curl the listing page before opening any browser.** Look for a
   server-side data source in the HTML: `__NEXT_DATA__`, `__NUXT__`,
   `window.__PINIA_STATE__`, `wp-json`, `/api/...` in script tags. Many
   boards ship the first page of jobs in SSR state (dynamitejobs ships
   20 in `__PINIA_STATE__`), and a public JSON API often does the rest.

2. **If the data loads client-side, record the real traffic.** Use the
   repo browser profile (next section), start a HAR, then reload,
   scroll, paginate, click filters:

       agent-browser --cdp 9222 --pin-tab --session <board> network har start
       agent-browser --cdp 9222 --pin-tab --session <board> open <listing url>
       # ...interact...
       agent-browser --cdp 9222 --pin-tab --session <board> network har stop /tmp/<board>.har

   Read the HAR for Fetch/XHR requests: URL, params, headers, response
   shape. Grep is fine:

       jq -r '.log.entries[].request.url' /tmp/<board>.har | sort -u

   **Delete the HAR when done.** HARs capture auth headers and OAuth
   tokens — a remotesource sign-in via Google OAuth was caught in one
   once.

3. **Replay the data request with curl, no cookies.** It usually works
   unauthenticated — the login wall is often UI-only (remotesource) or
   the backend is a public Firebase database (dynamitejobs). If it needs
   no auth, the scraper needs no browser ever again.

4. **Learn the knobs:** pagination (limit/offset/cursor), sort (is it
   really newest-first? sponsored pins?), category filter, and whether
   location filtering is server- or client-side. If the API takes a
   huge `limit`, fetch-all-and-filter-client-side is simpler and
   matches the contract.

5. **Map the board's location vocabulary.** There is no standard
   wording for "open worldwide / EMEA" — look at what tags jobs carry
   or what the site's own location filter sends, and pick the
   equivalents. File every tag under the smallest region its area
   actually covers: "Europe" is `europe`, "EMEA" is `emea`, and a
   tag that only covers the EU is `eu`. A wider key would make the tag
   claim area the board does not serve and leak narrow jobs through:

       workingnomads   "Anywhere", "EMEA"
       remotesource    eligibilityScopes: "WORLDWIDE", "EMEA"
       dynamitejobs    region slugs: "worldwide", "europe", "european-union",
                       "eastern-europe", "western-asia" (Middle East),
                       "africa", plus "NG"
       justremote      location_restrictions country names; [] = "Anywhere"
       remotive        locations tags: "Worldwide", "Europe" plus its
                       sub-regions, then country names as the board words
                       them ("UK", "United Arab Emirates")

6. **Write the plugin** from `jobtriage sources new NAME`, to the
   contract above, then test both modes. Nothing needs registering:
   the file in `<op>/scrapers/` is the board. What `sources test` and
   the two scrape modes cannot tell you, the board's notes below can.

## Board notes

- **workingnomads** — AngularJS site; the page talks to an Elasticsearch endpoint: `POST https://www.workingnomads.com/jobsapi/_search` (category `Development`, position_type `ft`, sort `pub_date` desc). Location filter is a plain tag match.
- **remotesource** — Next.js; `GET https://www.remotesource.com/api/jobs?jobCategory=Engineering+%26+Development&remoteFirstOnly=true&sortBy=relevance&limit=10000`. Fully public. `sortBy=relevance` is already newest-first with no sponsored pins. Location lives in `eligibilityScopes` per job; `eligibilityGlobal` ⟺ `["WORLDWIDE"]`. The "Your Location" UI filter is client-side only — no server param for it.
- **dynamitejobs** — Nuxt/Firebase. `POST https://firestore.googleapis.com/v1/projects/djplatform/databases/(default)/documents:runQuery?key=<public web key>` with the site's own structuredQuery (collection `jobs`, status `published`, categoriesSlugs array-contains `development`). Send the site's exact `orderBy` or Firestore 400s (missing composite index). The site's order boosts featured jobs, so re-sort by `publishedAt` client-side. Firestore quirks: `integerValue` arrives as a string, timestamps as `timestampValue`. Job URL is `/company/<username>/remote-job/<slug>`.
- **justremote** — React SPA; the listing page embeds the jobs in `window.__PRELOADED_STATE__`, but the same data comes straight from the API the client calls (base URL hardcoded in the JS bundle): `GET https://justremote-api.herokuapp.com/api/v1/jobs` — public, no auth, no pagination, no filters; returns all active jobs (~a month, newest first) and the client filters category/location itself. Listing dates are year-less ("10 Nov"), so the year is inferred. No salary data. Locations are `location_restrictions`: a list of country names; empty list = open anywhere.
- **remotive** — Django + Algolia InstantSearch. The documented public API (`/api/remote-jobs`) and the per-category RSS feeds (`/remote-jobs/feed/<slug>`) are a decoy: both serve the same tiny delayed feed and ignore their own category/search/limit params. The real data is the SSR listing page, which ships the Algolia response in `window.__INITIAL_SEARCH_RESULTS__` — page one of the category, because free users get no pagination (the rest is the paid tier). Sponsored pins break the date order, so re-sort by `discovered_on`; skip `is_blurred` hits (paywalled) and use `remotive_com_url` — `url` is false for some jobs and an external ATS link for most others. The site rate-limits hard (connection resets for a while after a burst of requests), so keep it to one request per run. `TERMS` records the link-back the site asks for.

- **jobgether** — the one board that built its API for scrapers (docs at /developers, /openapi.json, /llms.txt). `GET https://jobgether.com/api/v1/jobs?jobReferences=<slugs>&locations=emea&sort=date&limit=25&page=N`. `jobReferences` is the only param that ORs — 22 job-function slugs scope to dev roles server-side (no DevOps slug exists; ops hide under SRE/infrastructure/platform/cloud). `locations=emea` includes "Anywhere" jobs, so that one param is the default filter — but the displayed `location` tags abbreviate true eligibility (a job eligible in Germany can display only "European Union"), so custom tuples match displayed tags client-side. `limit` silently caps at 25, so paginate and dedupe by `id`. `postedAt` is batch-ingestion time — jobs share millisecond-identical timestamps. Watch the taxonomy pollution: batches of Spanish telemarketing gigs mis-tagged as iOS/Swift Developer; the module's title regex drops them. Retired `jobReferences` slugs 400 by name — the module drops the named slug and retries.
- **weworkremotely** — the per-category RSS feeds are the official interface and a complete census: `GET https://weworkremotely.com/categories/<slug>.rss` with the old long slugs (remote-full-stack-programming-jobs, remote-back-end-programming-jobs, remote-front-end-programming-jobs, remote-devops-sysadmin-jobs, remote-product-jobs) — the short `full-stack`-style slugs now 301 to nowhere. Five requests is the whole board (~189 items merged). `<title>` is "Company: Title"; `<region>` carries the restriction — "Anywhere in the World" is the worldwide marker — but it lies far more than the investigation measured: real survival through the default filter is ~51%, not ~74%. Cross-check "Anywhere" claims against the `<country>` flag list and the fine print ("Remote - USA", "U.S. Based"), and when a job names countries let the list replace the region. The default urllib UA 403s even the feeds; any browser UA passes. No salary element. Skip the "Do Not Apply" test posts. Links stay on weworkremotely.com per /api-terms, which `TERMS` records.
- **remoteok** — `GET https://remoteok.com/api` is the whole board in one request: a 100-element array, `[0]` the `{last_updated, legal}` meta, the rest the newest ~99. Cloudflare 1010 bans the default urllib UA; any custom UA passes. About 5% of records are double-encoded UTF-8 — a latin-1→utf-8 round-trip repairs them. A blank `location` (half the feed) means worldwide and is normalized to `Worldwide`. The `dev` tag is decorative — employers spray it on accountants — so dev-ness is a title battery (`dev_only=False` disables it). `salary_min`/`max` are unitless ints, 0 = missing; values ≥10M are flagged non-USD, sub-$1k print raw. The `legal` field demands a link-back and a "Remote OK" credit; `TERMS` is that demand, and `url` is the remoteok.com link it wants followed.
- **inclusivelyremote** — WordPress with the REST API wide open: `GET https://inclusivelyremote.com/wp-json/wp/v2/job_listing?per_page=100&orderby=date&order=desc&job_listing_category=<ids>&_fields=id,date_gmt,link,title,metas,class_list`. The trap the investigation missed: WP taxonomy filters do not include child terms — `engineering` alone silently drops backend/iOS posts — so the module ships an explicit 33-term dev-category ID list (blockchain/web3 excluded after live inspection found an accountant, four marketers, zero developers). Always pass `_fields` (full records run ~100KB each) and a custom UA (the default urllib UA 403s). Metas are HTML-escaped — unescape everywhere. Filtering matches `class_list` location slugs (`worldwide`, `europe_middle-east_africa`) client-side, so default, custom, and `--all` share one code path.
- **wellfound** — the role pages are server-rendered with the whole Apollo cache in `__NEXT_DATA__`: `GET https://wellfound.com/role/r/software-engineer?page=N` (browser UA mandatory). Company is a join through the cache: `ROOT_QUERY.talent.seoLandingPageJobSearchResults(...)` → `startups` → `highlightedJobListings` `$ref`s → job entities, name from `StartupResult`. Page order is not date order (promoted staleness rides page one) — re-sort by `liveStartAt`, dedupe by `id`. Empty `acceptedRemoteLocationNames` means US/unknown, never worldwide; the board has no worldwide tag at all, so the filter is an EMEA net (5 regions + 58 countries as the board words them) that worldwide-open jobs pass by listing many places. `compensation` bundles salary and equity in one string. EMEA density in the feed is ~9%, so the 5-page politeness cap returns ~14 jobs, not 50 — raise `MAX_PAGES` for the full 50 at ~16 requests a run. Never touch `/graphql` or `/company/*`, and skip the `/role/l/<role>/europe` pages: they filter by office location, not eligibility.
- **remocate** — fully server-rendered Next.js: `GET https://www.remocate.app/?workType=remote&category=development&page=N`, 20 cards a page, a 404 past the last page is the stop signal. Use a real parser — RSC chunks interleave with the markup, so cutting at the first `<script>` loses cards. The badges are the data model: no country badge means World (verified against the JSON-LD `applicantLocationRequirements: "World"`), the salary span carries `title="Salary from the job post"`, and the category badge wraps its label in a nested span while the country badge does not — the parser keys on that distinction and raises loudly if the Tailwind classes shift. `time[datetime]` is remocate's daily ~05:30 UTC first-seen sync, not the posting date. Skip `fresh=week`: the board adds ~2 jobs a day, and a week of freshness starves a 50-job run — the plain listing needs three pages instead.
- **eurotechjobs** — Laravel, server-rendered, and the default urllib UA passes. `GET https://www.eurotechjobs.com/job_search/category/developer` — all 195 dev jobs on one page, and a strict superset of the answer set: `workplace/remote` misses a genuine "Remote, Netherlands" dev job and leaks city-located ones. Paid logo cards and free rows are two layouts with the same fields — anchor on the `h3` job link. Dates are relative only ("Posted N days ago"), so `published` is date-precision computed back from today; paid pins break the order, so re-sort stably. A job is remote iff its location text has an " or "-separated alternative starting with "Remote" — the text wins over the badge and over the board's own workplace filter. "Remote, Multiple Countries" is the worldwide marker; "Remote, EMEA" is explicit. No salary anywhere.
- **euremotejobs** — WordPress, but the real surface is the AJAX feed the homepage's own JS calls: `POST https://euremotejobs.com/wp-admin/admin-ajax.php` with `action=erj_ajax_search&nonce=<from the homepage's inline var ERJ = {...}>&page=N&website=&search_region[]=remote-jobs-worldwide&search_region[]=remote-jobs-emea` — the region pair is the default filter, server-side. A stale nonce returns HTTP 403 with a `success:false` JSON body, so `urlopen` raises before any handler reads it: catch the error, read its body, refetch the homepage, retry once. Cloudflare 1010 bans the default urllib UA and any UA with parentheses — the bare `Mozilla/5.0` passes. Sixty cards a page; `has_more` ends pagination. `remote-jobs-europe` is a third bucket, not an alias — 58 of its first 60 cards are country-restricted — so it stays out of the declarations. Region buckets and card text are two taxonomies that disagree (a UK-only card sits in the EMEA bucket); the default trusts the buckets, the `locations` field reports the card. Dates are date-only. The ToS bans automated collection outright: the board ships disabled in the template for that reason, `sources info euremotejobs` shows the terms, and `sources enable euremotejobs` is how a user accepts them. Keep a run to a homepage fetch and a page or two.
- **remotefrontendjobs** — publishes its own free API (advertised at /developers): `GET https://www.remotefrontendjobs.com/api/jobs?board=frontend&regions=europe,africa,middle-east,worldwide&limit=100&offset=N` (`offset+limit` ≤ 250; `regions` sent only in default mode). The feed is not date-ordered — featured listings ride on top — so re-sort by `isoDate`. `geoRegions` can be JSON `null`, not just the comma-padded string. Sanity-check salaries: a yearly figure under $10k is bogus ('1k' → 1000, '30' → 360) and yields None. Cross-post mirrors (Grafana via 4dayweek 3×, Tether via Himalayas 4×) collapse by (title, company), keeping the newest — on heavy days that can leave the list a few short of `limit`. Usage terms require the canonical `/{id}` page as the URL, never the employer `link`; career-page-sourced jobs are held back 24 hours. One API serves the 3-board network via `board=` (frontend/backend/java, URLs on the sister domains).
- **bruntwork** — a single-employer board (every job is BruntWork), Next.js server-rendered: the `/search?category=<name>` cards ride as React flight rows in the `self.__next_f.push` script tags; there is no public jobs API, and robots only disallows the client-facing `/api/` routes. The "Website and Software Development" category is one request; dates exist only on the detail page (`/jobs/<id>`, "Published on", date-only) and are fetched per returned job — a default run costs about a dozen requests. The only real location tag is "Australia, New Zealand and Asia" (APAC-restricted, surfaces in `--all`); the other 644 of 663 jobs state no restriction, so untagged becomes a synthesized `Worldwide` — the justremote `[] = Anywhere` move. `jobId`s are HubSpot record ids minted in creation order; the module still sorts by fetched dates and sends missing ones last. No salary data exists on the board.

## Investigating

Boards researched but not built. What was learned stays here so the
next pass starts where the last one stopped.

- **greenhouse** — tabled. Fetching every tenant costs ~7k requests
  per run, and the search-limiter variant below was an idea, not a
  build. Everything else was solved:

  - **Per-board data.**
    `GET https://boards-api.greenhouse.io/v1/boards/<tenant>/jobs` —
    public JSON, no auth. Per job: title, company_name, one free-text
    `location.name`, absolute_url, `first_published` (the true posting
    date; `updated_at` bumps on edits). 404 = churned customer. No
    rate limiting observed: 100 requests at 10 concurrent finish in
    ~6 seconds; a sweep of every known board took 3:12 at 16
    concurrent, and a cached warm run 0.6s.
  - **Tenant discovery, complete.** The Wayback CDX index
    (`web.archive.org/cdx/search/cdx?url=job-boards.greenhouse.io/*&fl=original&collapse=urlkey`)
    streams 611k+ archived URLs → ~10k candidate slugs (harvest
    `?for=<tenant>` from the `/embed/job_board` URLs too; `embed` is a
    reserved path, not a tenant; digit-only slugs can be real —
    103644278 is Lume Deodorant). Liveness check: 6,842 alive (67.6%),
    the rest churned. A Brave `site:job-boards.greenhouse.io` query is
    redundant against this — one returned 19 tenants, every one
    already in the Wayback list.
  - **The plan for next time: search as the limiter.** Don't fetch
    every tenant — run a handful of Brave
    `site:job-boards.greenhouse.io <EMEA + title>` queries first (one
    request each; `BRAVE_SEARCH_API_KEY` is in .env), take the tenants
    they name, then hit the boards API only for those. ~10-50
    requests per run instead of ~7k. Trade: search-index bias —
    tenants whose matching jobs aren't indexed are invisible, and
    "newest first" only spans what the index has seen.
  - **Filter vocabulary, already tested** (28-case battery plus live
    data): locations are free text, not tags — a remote marker plus an
    EMEA/worldwide word list decides; bare "Remote" is worldwide,
    "Remote - US" is not, onsite/hybrid never is. US state
    abbreviations and timezones matter (CA/MI/VA/UT/EST caught "Remote
    or Alameda, CA"); OR, IN, PT (Portugal), ET are collision-prone
    and stay out. Titles: engineer/developer/devops/SRE minus
    Sales/Solutions/Support Engineer, the physical-engineering family
    (Structural, Industrialization, Fire Protection…), and `federal`
    in the title or `clearance` in the location (US-persons-only even
    when the location just says "Remote"). Normalise `first_published`
    to UTC so plain ISO string sort is true order.

## Browser research

All agent-browser ops use the op folder's Chrome profile,
`.browser-profile/` inside the workspace (gitignored by the template
— it holds cookies). It exists so sites see a normal, seasoned
browser instead of an automation build; some boards block sign-in in
the automation one. `jobtriage where` prints the folder.

Launch the real Google Chrome on it with a debug port, then attach:

    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
      --user-data-dir="$(jobtriage where)/.browser-profile" \
      --remote-debugging-port=9222 >/dev/null 2>&1 &

    agent-browser --cdp 9222 --pin-tab --session <board> tab new <url>

Attach matters; don't use `agent-browser --profile .browser-profile`.
Two reasons, both tested:

- agent-browser's bundled Chrome cannot decrypt cookies the real Chrome
  wrote to this profile (different macOS Keychain keys), so sessions
  silently vanish.
- real Chrome is what sites accept for logins. Chrome 136+ only allows
  `--remote-debugging-port` on a non-default user-data-dir, which is
  exactly what this separate profile is.

The user-data-dir holds three sub-profiles (`Profile 1/2/3`); Chrome
opens the last-used one (`Profile 1`). Switch sub-profiles in the
window if you need a different session for one-click OAuth.

The triage TUI (src/jobtriage/chrome.py) launches this same session itself
— same profile, same port — the first time enter finds the port
down, and leaves it running when the TUI exits, so an apply session
and a scraping session share one browser.

When done:

    agent-browser --cdp 9222 --session <board> close
    osascript -e 'tell application "Google Chrome" to quit'

Never commit `.browser-profile/` or HAR files. The user's real Chrome
profile (`~/Library/Application Support/Google/Chrome`) is never
touched — launch Chrome only with the repo's `--user-data-dir`.
