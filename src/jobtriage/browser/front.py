"""Bringing the session's browser window back over the terminal.

`chrome.Browser.activate` already brings one tab's window to the front
*of the browser* through the DevTools endpoint, but nothing in the
protocol can lift the application over the terminal — that is the
window manager's business. This module is the small, best-effort
interface for it, one implementation per platform:

    macOS    osascript, `tell application "<name>" to activate`, where
             the name comes from the browser bundle the finder chose
    Linux    wmctrl -a <name>, or the xdotool search/activate pair
             when wmctrl is not installed
    Windows  nothing yet: a silent no-op, because a PowerShell window
             flashing over the terminal is worse than a tab that stays
             behind it

Every failure is swallowed: no Automation consent, no wmctrl, a window
manager that ignores the request, no browser at all. The tab is still
focused in its window; it is findable, just not raised. None of this is
worth an error a person can act on.

`app_name` is the piece both callers share: a macOS executable lives
inside `<name>.app`, and its display name is not always the binary's
(`Brave Browser.app` holds `Contents/MacOS/Brave Browser`, but the
AppleScript vocabulary wants 'Brave Browser', not the path). A
configured executable outside a bundle falls back to its file name.
"""

import platform
import shutil
import subprocess
from pathlib import Path

# One automation call either works at once or never will; a hung
# osascript must not hold up the TUI's enter.
TIMEOUT = 5

# Linux window titles end in the app's name ("Role — Google Chrome"),
# so wmctrl's substring match needs the name a person sees, not the
# package's binary name. Anything not named here is tried as-is, which
# is right for a Chromium build a package called something else.
LINUX_NAMES = {
	'google-chrome': 'Google Chrome',
	'google-chrome-stable': 'Google Chrome',
	'chromium': 'Chromium',
	'chromium-browser': 'Chromium',
	'brave-browser': 'Brave',
	'microsoft-edge': 'Microsoft Edge',
	'microsoft-edge-stable': 'Microsoft Edge',
}


def front(binary: Path) -> None:
	"""Lift the browser that owns `binary` to the front, if this platform
	knows how. Never raises: a window that stays behind the terminal is
	not a failure worth reporting."""
	system = platform.system()
	if system == 'Darwin':
		_macos(binary)
	elif system == 'Linux':
		_linux(binary)
	# Windows (and anything else): deliberately nothing yet.


def app_name(binary: Path) -> str:
	"""The browser's display name, from its macOS bundle when it has one.

	The outermost `.app` part is the application bundle: a configured
	executable may sit deeper in `Contents/MacOS/`, but the bundle is
	what AppleScript addresses. `Path.stem` would also strip a real
	extension from a non-bundle file, so the suffix is removed
	explicitly instead.
	"""
	for part in binary.parts:
		if part.endswith('.app'):
			return part[:-4]
	return binary.name


def active_tab_url(binary: Path) -> str | None:
	"""The URL of the active tab of the browser's front window.

	AppleScript tab ids and DevTools target ids are different things,
	so URLs are the only shared key. This is what identifies a job's
	tab after a cold launch, when the site redirected before the
	DevTools port came up and a plain URL match misses.

	macOS-only by nature; `Browser._front_tab_url` guards the platform
	and other systems rely on URL matching instead. A browser without
	Automation consent (or no osascript at all) returns None, which is
	the same "can't identify it" the caller already handles.
	"""
	if shutil.which('osascript') is None:
		return None
	result = _run(
		[
			'osascript',
			'-e',
			f'tell application "{app_name(binary)}" to get URL of active tab of front window',
		],
		text=True,
	)
	if result is None:
		return None
	return (result.stdout or '').strip() or None


def _macos(binary: Path) -> None:
	"""`tell application "<bundle name>" to activate` — the one line
	that raises the app, best effort. A failed osascript (no Automation
	consent yet) is as silent as a missing one."""
	if shutil.which('osascript') is None:
		return
	_run(['osascript', '-e', f'tell application "{app_name(binary)}" to activate'])


def _linux(binary: Path) -> None:
	"""wmctrl's substring match on the window title, or xdotool.

	wmctrl takes the visible name and matches any window whose title
	contains it, which is how a Chromium window ("Role — Google
	Chrome") is found without knowing its window id. xdotool is the
	fallback: `search --onlyvisible --name` prints matching ids and
	`windowactivate` raises one; with no match it exits non-zero and
	nothing happens, which is the same no-op as no tool at all.
	"""
	name = LINUX_NAMES.get(binary.name, binary.name)
	if shutil.which('wmctrl') is not None:
		_run(['wmctrl', '-a', name])
		return
	if shutil.which('xdotool') is not None:
		_run(['xdotool', 'search', '--onlyvisible', '--name', name, 'windowactivate'])


def _run(command: list[str], *, text: bool = False) -> subprocess.CompletedProcess | None:
	"""One external call, with every way it can fail folded to None.

	A window manager is optional software on a machine; a missing
	binary, a refused permission or a stuck call all mean the same
	thing here — the window was not raised — and none of them may
	interrupt the TUI's enter.
	"""
	try:
		return subprocess.run(
			command,
			capture_output=True,
			text=text,
			timeout=TIMEOUT,
			check=False,
		)
	except (OSError, subprocess.TimeoutExpired):
		return None
