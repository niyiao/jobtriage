"""Job-board scrapers: the built-in plugins, one module per board.

Each module is a plugin under the contract `jobtriage.registry` reads:
`NAME`, `recent_jobs(*, locations, limit, **options)`, and — for the
ones that split the network from the parsing — `fetch()` plus
`parse(raw)`, both documented in `jobtriage.plugin`. A board also
declares its location and family vocabulary as data (`WORLDWIDE`,
`REGIONS`, `COUNTRY_STYLE` / `COUNTRY_ALIASES`, `FAMILIES`, and
`COVERS` where the board only serves a region), which
`geo.board_tags(module, prefs)` translates and `pipeline.py` reads for
a run.

There is deliberately no hand-written list of boards here: the
registry finds every non-private module in this package with
`pkgutil.iter_modules`, so dropping a new file in is all it takes.
Workspace plugins live in `<op>/scrapers/` and are loaded the same way.
A plugin imports its helpers from `jobtriage.plugin`, never from this
package.
"""
