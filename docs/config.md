# Configuration

Everything that belongs to one person lives in the op folder's
`jobtriage.toml` — where they can work from, what kind of role they
want, how old a posting can be, which boards to use, the scraper's
User-Agent, how the TUI opens. The file's presence is also what marks
the folder as a workspace (`jobtriage where`, `--workspace PATH`,
`JOBTRIAGE_HOME`).

`jobtriage init` writes it from `src/jobtriage/assets/jobtriage.toml`
with every key commented out: a fresh folder runs on the defaults until
you fill something in. Edit it by hand, or through `jobtriage config`
(below). The repo itself holds no personal values — the code defaults
and the shipped presets are the stranger's answers.

## Precedence

Lowest first:

1. **code default** — the dataclass defaults in `config.py`
2. **preset** — the `[filter]` lists loaded by `filter.preset`
3. **`jobtriage.toml`** — your file
4. **`JOBTRIAGE_*` environment** — one variable per key (below)
5. **CLI flag** — only the flags that map to a key
   (`jobtriage scrape 20` → `search.limit`; `filter test --preset` →
   `filter.preset`)

`jobtriage config show` prints every effective value with the layer that
set it (`default`, `preset:web-engineer`, `file`, `env:NAME`, `flag`).
`preset` is the only *base* layer: a file's `exclude_any` extends the
preset's turnaway list, while the other lists in the file replace the
preset's. See [the preset rules](#presets).

## `[search]`

```toml
[search]
locations = ["worldwide", "emea"]        # where you can work from
families = ["software"]                  # what kind of role
employment = ["full-time"]               # full-time, contract, part-time
max_age_days = 21                        # how old a posting can be
limit = 50                               # per board, per run
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `locations` | array of strings | `["worldwide"]` | The places you can work from: `worldwide`, a region, an ISO country code, a country name or an alias. Each board translates the list into its own tags. |
| `families` | array of slugs | `["software"]` | Job families. Boards declare the category/slug/id parameters for a family; a board that serves none of the requested families is skipped with a notice. `software` is the only family every shipped board declares today, so a list without it skips those boards. |
| `employment` | array of strings | `["full-time"]` | `full-time`, `contract`, `part-time`. Validated at load; best effort, as the plan says — no shipped board maps it to its own vocabulary yet, and workingnomads defaults to full-time in its family parameters. |
| `max_age_days` | integer ≥ 0 | `21` | The age cutoff. Older dated jobs are dropped; an undated job is assumed new. `0` tightens the cutoff to this moment; date-only postings count as UTC midnight and fall outside it. |
| `limit` | integer ≥ 1 | `50` | How many jobs each board is asked for, per run. `jobtriage scrape 20` overrides it for one run. |

### Locations

`locations` is one vocabulary shared by everyone, because the fifteen
boards spell the same places fifteen ways. A value is resolved when the
config loads: the canonical token (`emea`, `GB`) is kept, aliases are
folded, and a typo stops the run with the file, the key, the position
and a suggestion:

    jobtriage.toml: search.locations[2]: unknown region or country 'emae' (did you mean 'emea'?)

The special value `worldwide` means "open to anyone, anywhere". It is
not a country and adds no countries; it asks each board for its own
"Worldwide"/"Anywhere" tags. A region or country preference also asks
for them: a job open anywhere is open to that region or country.

The twenty-one regions:

| Region | Covers |
|---|---|
| `emea` | Europe, the Middle East and Africa |
| `europe` | Geographic Europe, including Russia, Türkiye and the Caucasus |
| `eu` | European Union member states (alias `european union`) |
| `uk-ireland` | Great Britain and Ireland (aliases `uk & ireland`, `uk and ireland`, `uki`) |
| `nordics` | Denmark, Finland, Iceland, Norway, Sweden |
| `benelux` | Belgium, Luxembourg, the Netherlands |
| `western-europe` | Britain, Ireland, France, Germany, the Low Countries, Switzerland, Austria, Monaco, Liechtenstein (alias `western europe`) |
| `eastern-europe` | Belarus, Bulgaria, Czechia, Hungary, Moldova, Poland, Romania, Russia, Slovakia, Ukraine (alias `eastern europe`) |
| `southern-europe` | Iberia, Italy, Greece, the Balkans, Malta, Cyprus and the European microstates (alias `southern europe`) |
| `northern-europe` | The Nordics, the Baltics, Britain, Ireland and the North Atlantic islands (alias `northern europe`) |
| `central-europe` | Austria, Czechia, Germany, Hungary, Poland, Slovakia, Slovenia, Switzerland (alias `central europe`) |
| `middle-east` | The Middle East, including Cyprus, Türkiye and Egypt (aliases `middle east`, `mena`) |
| `africa` | The African continent |
| `sub-saharan-africa` | Africa below the Sahara — Nigeria, Ghana, Kenya, South Africa and the rest outside North Africa (alias `sub saharan africa`) |
| `americas` | North, Central and South America and the Caribbean |
| `north-america` | Canada, Mexico, the United States |
| `latam` | Mexico, Central and South America and the Caribbean (aliases `latin america`, `south america`) |
| `caribbean` | The Caribbean islands |
| `apac` | Asia outside the Middle East, plus Oceania (aliases `asia pacific`, `asia-pacific`) |
| `asia` | Asia outside the Middle East |
| `oceania` | Australia, New Zealand, the Pacific islands (alias `anz`) |

Regions overlap on purpose: Cyprus and Türkiye are Europe *and* the
Middle East; Mexico is North America *and* LatAm; Britain is western
*and* northern Europe. A place can belong to several ways of grouping
the world.

A country can be written as its **ISO 3166-1 alpha-2 code** (`GB`,
`DE`, `NG`, `US`), its display name (`United Kingdom`, `Germany`,
`Nigeria`, `United States`) or a common alias (`UK`, `USA`, `Holland`,
`Türkiye`/`Turkey`, `Czechia`/`Czech Republic`, `Vietnam`/`Viet Nam`).
Matching is case-insensitive and forgiving about punctuation
(`u.k.` = `UK`). The table lives in `src/jobtriage/geo.py`; the resolved
list always uses codes, so `config get search.locations` prints
`["GB"]` for `"United Kingdom"`.

`locations` is where you can actually work. A job matches when its
stated area **covers** one of your locations — the job must be open to
where you are, or wider.

A country preference expands *upward*: `["NG"]` asks each board for its
Worldwide wording (where it has one), the tags of every region the
board declares that contains Nigeria (`EMEA`, `Africa`, `Sub-Saharan
Africa`) and its Nigeria tag. It does not ask for a Poland tag, a
Europe tag or a Western-Europe tag: those areas do not cover Nigeria,
so a Poland-only job and a Europe-only job are not matches.

A region preference reads the same way, one rung up: `["europe"]` asks
for the board's Europe *and EMEA* tags — EMEA covers Europe — but not
for the wording of any member country, because a job open only in
Germany does not cover someone who can work from all of Europe.
`["emea"]` asks for EMEA tags only; a Europe-only job does not cover
EMEA. The nesting repeats below: `["sub-saharan-africa"]` matches
Africa-wide and EMEA-wide jobs as well as sub-Saharan ones, and
`["western-europe"]` matches Europe- and EMEA-wide tags. Where a board
has no tag as wide as the ask, only the worldwide wording remains.

A board's region keys state each tag's true area, never a wider one, so
a "Europe" tag is `europe` and can never stand in for EMEA. A board can
also have no tag as wide as the ask: remotive's widest area tag is
`Europe` and remocate has only that one, so an EMEA preference gets no
region tag there and only the worldwide wording remains; the central
filter still judges the jobs that fetch returns.

A board only covers what it covers: eurotechjobs is Europe-only, so a
`["north-america"]` user skips it with one line on stderr instead of
paying a request for nothing. A `worldwide` user matches every board's
coverage.

## `[filter]`

The title filter: regex fragments, tried in this order on every scraped
title.

```toml
[filter]
preset = "web-engineer"          # optional starting point, shipped with the package
require_any = ["engineers?", "developers?"]
include_any = ["front[ -]?end", "full[ -]?stack", "react", "typescript"]
exclude_any = ["manager", "ios", "android", "react native"]
exclude_companies = ["Acme"]
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `preset` | string | none | Load one of the shipped presets as the base lists. Empty or absent means no preset. Unknown names fail at load with a suggestion. |
| `require_any` | array of regexes | `[]` | A positive gate: when non-empty, the title must match at least one fragment. |
| `include_any` | array of regexes | `[]` | The second positive gate: when non-empty, the title must match at least one fragment too. Both gates must pass. |
| `exclude_any` | array of regexes | `[]` | The turnaway gate: a match drops the title, whatever the positive gates said. |
| `exclude_companies` | array of strings | `[]` | Company-name fragments, not regexes. Fold-tolerant containment: `"Acme"` drops `Acme Inc.` and `ACME, Ltd.`. |
| `require_any_extra` | array of regexes | `[]` | Extends `require_any` instead of replacing it. |
| `include_any_extra` | array of regexes | `[]` | Extends `include_any` instead of replacing it. |
| `exclude_any_extra` | array of regexes | `[]` | Extends `exclude_any` instead of replacing it. |

Fragment rules: case-insensitive, matched anywhere in the title on word
boundaries (`go` matches "Go Engineer", not "Django"); a fragment may
use any Python regex syntax. An empty gate is disabled, so an empty
`[filter]` keeps every title — the board's category is the only
narrowing, which is the right default for a stranger. An invalid regex
stops the load with the file, the key and the index in the error.

`exclude_companies` is not a title gate: it is checked against the
company name, so `jobtriage filter test` reports that it cannot answer
it.

### Presets

Six presets ship in `src/jobtriage/assets/presets/`, inside the package
they are read from, never copied into the op folder (so a package update
can improve them). They are ordinary TOML files with the `[filter]` list
keys.

| Preset | One line |
|---|---|
| `web-engineer` | Frontend and full-stack web roles — today's original lists |
| `backend-engineer` | APIs, services, data stores and platform work |
| `data` | Data engineering, analytics, BI, ML and AI |
| `designer` | Product, UX/UI, interaction and visual design |
| `product-manager` | Product management and product ownership |
| `any` | No title filter at all |

`preset = "web-engineer"` loads that preset's lists as the base; the
file then layers on top:

- `exclude_any` in the file **extends** the preset's turnaway list (a
  preset's exclusions are a starting point, not a ceiling).
- `require_any`, `include_any` and `exclude_companies` in the file
  **replace** the preset's.
- `require_any_extra`, `include_any_extra` and `exclude_any_extra`
  always extend, in every layer.
- Environment variables and CLI flags replace the list they name
  (higher precedence means replacement); their `_extra` forms extend.

`preset = "any"` and an empty `[filter]` are the same thing: no title
filter. `jobtriage init` writes `any` for a non-interactive setup.

### Debugging the filter

`jobtriage filter test` runs the same `TitleFilter` a scrape uses on one
title and prints the decisive gate:

    jobtriage filter test "Senior React Native Engineer"
    dropped: exclude_any matched "react native"

    jobtriage filter test "Senior Software Engineer"
    kept: require_any matched "software"
      role "software" · age gate not applied (this tests the title alone)

The title is one argument — unquoted words are joined with spaces, so
`filter test Senior React Native Engineer` asks the same question. Exit
status is the verdict (0 kept, 1 dropped), which is what a script or an
agent reads. `--preset NAME` swaps the preset layer without editing the
file; the file's own lists still apply on top, as on a real run.

`jobtriage scrape --explain` is the run-wide version. It prints one line
per board on stderr, after the board's jobs were filtered:

    explain: remotive · fetched 50 · age 8 · title 31 · duplicate 2 · kept 9
      exclude_any matched "manager" ×12
      require_any matched nothing ×10
      exclude_any matched "react native" ×6

`fetched` is what the board returned; `age`, `title` (every `[filter]`
gate, company exclusions included), `duplicate` (a cross-post merged
into another board's copy) and `kept` partition it exactly. The three
most common title reasons follow, with the gate and the word. Skipped
boards say why they were skipped; failed boards say what raised. It is
stderr because `--raw` prints jobs to stdout, and the summary line has
always been on stderr.

## `[letters]`

```toml
[letters]
paragraphs = 2                   # how many body paragraphs a cover letter has
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `paragraphs` | integer ≥ 1 | `2` | The length the `cover-letter` skill writes: one or two body paragraphs. Two is the default because a letter is a note beside the resume, not a second essay. |

The letter skill reads the effective value with
`jobtriage config get letters.paragraphs`; there is no other consumer.
The greeting, sign-off and the posting's own language are the skill's
rules, not config keys.

## `[sources]`

```toml
[sources]
disabled = ["euremotejobs"]  # the one board that ships off: its terms ban automated collection

[sources.wellfound]
role = "frontend-engineer"  # the board's own role slug

[sources.euremotejobs]
max_pages = 8               # how many AJAX pages this board may fetch
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `disabled` | array of board names | `[]` (the shipped template: `["euremotejobs"]`) | Boards to skip for every run. Names are canonical registry names (`bruntwork`, `remotive`, `wellfound`, …), and the short `-s` aliases are accepted and canonicalised: `disabled = ["rm"]` disables `remotive`. `jobtriage sources enable`/`disable` always write the canonical name. |
| `[sources.<board>]` | table | none | Per-board keyword arguments. The key may be the board's name or an alias — `[sources.rm]` tunes `remotive`, and the canonical table wins if both are present. Whatever that board's `recent_jobs` accepts is passed straight through (`max_pages`, `position_type`, `category`, `board`, …); the table is the escape hatch for a board's own knobs. |

A `[sources.<board>]` table is merged over the board's family
parameters, and the pipeline still supplies `limit` and `locations`
unless the table sets them. `--all` wins over `locations` for that run.
An override naming no board in the run is a warning on stderr, not a
silent no-op — the config loader cannot know which plugins exist, so
the pipeline checks the names after the registry has loaded. A keyword
the board's `recent_jobs` does not accept makes that one board fail for
the run (the warning names the argument); the rest of the scrape
carries on.

`euremotejobs` ships disabled because its terms ban automated
collection outright. `jobtriage sources info euremotejobs` prints the
terms; `jobtriage sources enable euremotejobs` is how you accept them.

## `[browser]`

| Key | Type | Default | What it does |
|---|---|---|---|
| `executable` | string | `""` | The browser binary to launch. Empty means auto-detect (see below). |
| `port` | integer 1–65535 | `9222` | The CDP port the TUI's browser listens on. |

Both keys are read and validated. `executable` is tried first: a path
that does not exist is reported (never silently replaced by another
browser), and an empty value means the platform's usual places are
searched — Chrome, Chromium, Brave and Edge in `/Applications` and
`~/Applications` on macOS, the package names on `PATH` on Linux, and
Program Files/LocalAppData on Windows. `jobtriage doctor` prints which
binary it found and whether `port` is free or already the TUI's browser.

## `[network]`

```toml
[network]
user_agent = ""                  # empty: a desktop browser string
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `user_agent` | string | `""` | The `User-Agent` every board request sends. Empty means the desktop-browser string the boards have always been sent; a value replaces it, for identifying the tool honestly or supplying a token a board asks for. |

A board that passes its own string still wins: euremotejobs requires a
bare `Mozilla/5.0` without parentheses, so `[network]` cannot break it.
The value must be one line — a newline is a header injection and fails
at load, not board by board at request time. `jobtriage doctor`'s
homepage check sends the same string.

## `[tui]`

```toml
[tui]
default_filter = "to_apply"
page_size = 30

[tui.keys]
skipped = "x"
applied = "d"
```

| Key | Type | Default | What it does |
|---|---|---|---|
| `default_filter` | one of `new`, `to_apply`, `applied`, `skipped`, `all` | `to_apply` | The tab the TUI opens on when there is no remembered place. |
| `page_size` | integer ≥ 1 | `30` | Rows per page. Read and validated; the TUI still pages by its built-in 30 until the layout work reads it. |
| `[tui.keys]` | table of action → key | the table in `src/jobtriage/tui/keys.py` | Rebind one TUI action. The value replaces all of that action's default keys; the footer and `?` overlay follow the same table, so they cannot go stale. |

Action names are the canonical ones from `tui/keys.py` — `skipped`,
`applied`, `undo`, `down`, `open`, `details`, `search`, `scrape`, `quit`
and so on; `jobtriage config show` prints the `[tui.keys]` entries that
are set, and README has the full default key map. Keys use Textual's
spelling: one printable character or a named key (`pagedown`, `enter`,
`question_mark`), optionally with modifiers (`ctrl+d`, `shift+tab`).
Unknown actions, invalid keys and two actions sharing a key are load
errors.

## Environment variables

Every scalar/list key has a `JOBTRIAGE_<SECTION>_<KEY>` spelling. Arrays
are TOML arrays (the syntax the file already uses), integers are TOML
integers, strings are taken verbatim — no TOML quotes needed:

    JOBTRIAGE_SEARCH_LOCATIONS='["emea", "GB"]'
    JOBTRIAGE_SEARCH_FAMILIES='["software"]'
    JOBTRIAGE_SEARCH_EMPLOYMENT='["full-time", "contract"]'
    JOBTRIAGE_SEARCH_MAX_AGE_DAYS=14
    JOBTRIAGE_SEARCH_LIMIT=100

    JOBTRIAGE_FILTER_PRESET=web-engineer
    JOBTRIAGE_FILTER_REQUIRE_ANY='["engineers?"]'
    JOBTRIAGE_FILTER_REQUIRE_ANY_EXTRA='["architects?"]'
    JOBTRIAGE_FILTER_INCLUDE_ANY='["react"]'
    JOBTRIAGE_FILTER_INCLUDE_ANY_EXTRA='["svelte"]'
    JOBTRIAGE_FILTER_EXCLUDE_ANY='["manager"]'
    JOBTRIAGE_FILTER_EXCLUDE_ANY_EXTRA='["director"]'
    JOBTRIAGE_FILTER_EXCLUDE_COMPANIES='["Acme"]'

    JOBTRIAGE_LETTERS_PARAGRAPHS=3

    JOBTRIAGE_SOURCES_DISABLED='["bruntwork"]'

    JOBTRIAGE_BROWSER_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    JOBTRIAGE_BROWSER_PORT=9223

    JOBTRIAGE_NETWORK_USER_AGENT='jobtriage/1.0 (+https://example.test)'

    JOBTRIAGE_TUI_DEFAULT_FILTER=new
    JOBTRIAGE_TUI_PAGE_SIZE=50

Per-board `[sources.<board>]` tables and `[tui.keys]` have no
environment spelling. `JOBTRIAGE_HOME` is the workspace path, not a
config key. Unrelated `JOBTRIAGE_*` names are ignored.

## The config commands

    jobtriage config show               # effective values, with the layer that set each
    jobtriage config show --json        # the sections plus a dotted-key provenance map
    jobtriage config get search.limit   # one effective value
    jobtriage config set search.locations '["emea"]'
    jobtriage config set filter.preset any
    jobtriage config edit               # $EDITOR (or vi), then validate
    jobtriage config validate           # load the file, print ok or the first error

`get` reads the merged config, so it answers what the next run will use,
not what the file literally says (an environment variable may be on
top). `set` writes through `tomlkit`, keeping your comments and layout;
the value is parsed as a TOML value first and falls back to a bare
string, which is why both quoted arrays and `any` work. The whole
config is validated after the write and the write is rolled back if
this build cannot honour it. `set`/`get`/`show` take dotted keys,
including `sources.euremotejobs.max_pages` and `tui.keys.skipped`.

A bad value fails fast, at the first command that reads the file:

    jobtriage.toml: filter.exclude_any[1]: invalid regex 'react (': missing ), unterminated subpattern at position 6

and an unknown section, key, region, employment type or preset names
itself and, when a typo is close, suggests the intended spelling. A
family is any non-empty slug: the loader accepts names no board has
heard of yet, and the pipeline skips the boards that serve none of
them.
