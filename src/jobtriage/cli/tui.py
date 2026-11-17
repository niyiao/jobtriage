"""`jobtriage tui` — the triage TUI (the old `apply` command, retired).

The TUI itself is jobtriage.tui.TriageApp; this module is the command
around it: resolve the workspace, build the tracker (the database) and
the browser (the workspace profile), run, and hand the app the two
things that are decisions rather than plumbing: which scraper `R` runs,
and where the session's place is remembered.
"""

import argparse

from jobtriage import chrome, config, store
from jobtriage.cli.scrape import background_scrape
from jobtriage.tui import TriageApp


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser('tui', help='the triage TUI: open, re-status, mark applied')
	parser.set_defaults(run=run)
	return parser


def opener_for(ws):
	"""The browser `enter` drives: the workspace's Chromium when it is
	installed, the machine's default browser when it is not.

	The binary and the port are the config's `[browser]` (plan 03 and
	plan 08 step 7): `executable` may name a specific Chromium, and a
	second Chrome on the machine may need a second CDP port. A broken
	config still opens the TUI — its own reader (`config.load_tui`) is
	deliberately narrow, so a typo in `[search]` must not stop the
	screen from appearing; the browser falls back to the defaults, the
	same way a missing browser falls back to DefaultBrowser.
	"""
	try:
		settings = config.load(ws).browser
	except config.ConfigError:
		settings = config.BrowserSettings()
	browser = chrome.Browser(
		ws.browser_profile,
		port=settings.port,
		executable=settings.executable,
	)
	return browser if browser.available() else chrome.DefaultBrowser()


def run(args, ws) -> int:
	tracker = store.Tracker(ws.db)
	TriageApp(
		tracker,
		opener=opener_for(ws),
		config=config.load_tui(ws.config),
		scrape=background_scrape(ws),
		state_path=ws.tui_state,
	).run()
	return 0
