# The scraper contract, v1

The full version lives in the jobtriage repo's `docs/scrapers.md`
("Writing a plugin"); this is the summary a plugin author needs beside
the code. `jobtriage sources info NAME` prints a board's own docstring
and declarations, and `jobtriage sources test NAME` checks the result.

## Required

```python
NAME = 'myboard'  # [a-z0-9-]+, unique across every board the registry finds


def recent_jobs(*, locations=None, limit=50, **options) -> list[dict]: ...
```

- `NAME` is what `-s`, `jobs.source` and `[sources.<name>]` use.
- `recent_jobs` returns at most `limit` jobs, newest first.
- `locations` is the tuple this board's own declarations produced, or
  `None` when the run asks for everything (`--all`).
- `options` receives the family parameters plus every key under
  `[sources.NAME]` in `jobtriage.toml`, so a board knob is configurable
  without a release.

## Optional declarations

| Name | Default | Meaning |
| --- | --- | --- |
| `ALIASES = ()` | none | Extra names `-s` accepts; unique, and not stored in `jobs.source`. |
| `TITLE` | the id, title-cased | The name listings show. |
| `HOMEPAGE` | `''` | The board's site. |
| `API_VERSION = 1` | 1 | The contract this file targets. A newer number than the build speaks marks the file broken. |
| `TERMS` | `''` | What the board's terms ask: a link back, a credit, a rate. Shown by `sources info`. |
| `POLITE_DELAY` | `0` | Seconds between requests, enforced by `plugin.http` for the whole call. |
| `WORLDWIDE`, `REGIONS`, `COUNTRY_STYLE`, `COUNTRY_ALIASES`, `COVERS` | none | The location vocabulary, in the board's wording. |
| `FAMILIES = {}` | every family | Job family to the board's category params; `{}` means the board serves all families. |
| `server_locations(prefs)` | none | Declarative request params for a server-filtered board; `sources info` reports it. |
| module docstring | none | The research notes. `sources info` prints them. |

## The returned jobs

A job is a `jobtriage.plugin.Job` (`TypedDict`); six keys matter:

| Key | Type | Rule |
| --- | --- | --- |
| `title` | string | non-empty |
| `company` | string | non-empty |
| `url` | string | absolute `http(s)` |
| `published` | ISO 8601 string or `None` | use `plugin.dates.iso`, `dates.relative`, `dates.yearless`; the pipeline sorts these strings |
| `locations` | list of strings | the board's own tags, not canonical geo tokens |
| `salary` | string or `None` | free text; a number must be formatted here |

Extra keys are kept. The registry validates every row before it is
stored: a missing key, an empty title or company, a relative URL, an
unparseable `published`, a non-string location or a numeric salary is
dropped and counted. `sources test` names the row and the field.

## Location vocabulary

`geo.board_tags(module, prefs)` reads the board's declarations and
returns the tuple for `recent_jobs`, so the board never parses the
user's config itself. The declarations mean:

- `WORLDWIDE`: tags for "anywhere", verbatim.
- `REGIONS`: canonical token (`emea`, `europe`, `apac`, `latam`, …) to
  the board's tags.
- `COUNTRY_STYLE`: `'name'`, `'iso2'` or `'none'`.
- `COUNTRY_ALIASES`: ISO alpha-2 code to exact wording. A code mapped
  to `''` is suppressed: the board does not carry it, and the filter
  stays exact instead of inventing a tag.
- `COVERS`: the region a single-region board serves; a user whose
  locations do not overlap it skips the board without a request.

A board whose API returns every region may skip all of this and filter
client-side with the board's own job tags; `--all` and the default then
share one code path.

## Discovery, override, broken

The registry loads three tiers, later overriding earlier:

1. built-ins in `jobtriage.scrapers.*`;
2. installed packages in the entry-point group `jobtriage.scrapers`;
3. `<op>/scrapers/*.py`, skipping names that start with `_`.

A later tier with the same `NAME` overrides an earlier one and says so
on stderr. That is how a user patches a built-in without waiting for a
release.

A file is **broken** — listed with a reason, never a crash, never a
traceback — when it fails to import, has no or an invalid `NAME`, has no
callable `recent_jobs`, targets a newer `API_VERSION`, or takes an alias
another board owns.

## Files to read

- `<op>/scrapers/_example.py` — the template `sources new` copies; every
  hook, with comments.
- `docs/examples/demo.py` in the jobtriage repo — the same contract as a
  finished board.
- `docs/scrapers.md` — the research playbook and board notes.
