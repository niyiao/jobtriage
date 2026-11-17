"""The CLI: one entry point, one module per subcommand group.

`jobtriage` with no subcommand opens the TUI (plans/README decision
3) — the daily action; everything else is explicit. main.py builds
the parser and dispatches; each command module (scrape, list, show,
status, note, export, config, db, tui, init) adds its own subparser and runs
the command. workspace resolution happens once, in main, so every
command — and the error for when there isn't one — behaves the same.
"""
