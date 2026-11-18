"""Where the browser lives: finding it, and (next) fronting its window.

`find.py` answers "which Chromium-family executable should this build
drive?" for the three platforms the tool supports. `chrome.py` — the
DevTools and AppleScript glue — stays at the package root for now,
because it predates the package and every caller imports it by that
name; the window-fronting step (plan 08 step 8) moves the macOS-only
assumptions into this package.
"""
