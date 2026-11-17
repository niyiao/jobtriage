"""The triage TUI's Chrome: the workspace profile's browser, driven
over its DevTools port.

The session is the op folder's launch (see the repo's docs/scrapers.md,
"Browser research") — the real Chrome on the workspace's
.browser-profile with --remote-debugging-port=9222 — and this module
owns it for the triage TUI. It attaches when that Chrome is already up
and launches it when it isn't: a window of its own on the first job
that asked (the DevTools endpoints make a window themselves when the
app still runs but every window has been closed).

From there it is one tab per job. open_or_focus() focuses the tab a
job already has and opens a fresh one only when that tab has been
closed, so a second enter never navigates away from — or stacks a
duplicate on — an application in progress; a stray tab at the same
URL from an earlier session is adopted rather than duplicated. The
DevTools HTTP endpoints are the whole interface (/json/list,
/json/new, /json/activate), plus one AppleScript line to lift
Chrome over the terminal: activate brings a tab's window to the
front of Chrome, but not the app to the front of the screen.

The class is the old module's functions, gathered so a Browser can
carry the profile (from the workspace), the port (config, plan 03) and
the configured executable (plan 08 step 7) as arguments — the TUI's
opener injection still just wants anything with
open_or_focus(url, remembered). The binary itself comes from
jobtriage.browser.find, which knows the per-platform locations; the
platform automation — raising the window, and the AppleScript tab
fallback after a cold launch — lives in jobtriage.browser.front, so
this module is the same protocol on every system. DefaultBrowser is
that same opener for a machine with no Chromium: the OS's browser
through stdlib `webbrowser`, no tab memory, and it says so
(focuses_tabs).
"""

import json
import platform
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from jobtriage.browser.find import find_browser
from jobtriage.browser.front import active_tab_url, front

HOST = '127.0.0.1'
PORT = 9222
HTTP_TIMEOUT = 3
LAUNCH_TIMEOUT = 20


class Browser:
	"""One Chrome profile, one DevTools port, driven over HTTP."""

	# The TUI reads this to decide what to say after enter: a Browser
	# remembers a tab per job, the fallback cannot (see DefaultBrowser).
	focuses_tabs = True

	def __init__(self, profile: Path, port: int = PORT, executable: str = ''):
		self.profile = profile
		self.port = port
		# '' means auto-detect; anything else is browser.executable from
		# jobtriage.toml and is taken as given (see browser.find).
		self.executable = executable

	def binary(self) -> Path | None:
		"""The Chromium-family binary this Browser would launch.

		Resolved on every call rather than in __init__, so a browser
		installed while the TUI is open is found on the next enter —
		and so `available()` and `launch()` can never disagree about
		which file they mean.
		"""
		return find_browser(self.executable)

	def available(self) -> bool:
		"""Is there a browser to drive at all?

		True only when the finder (config first, then the platform's
		known locations) returns an existing executable. The TUI uses
		this to choose between a Browser and DefaultBrowser before the
		first enter, so a machine without Chrome never sees a launch
		fail.
		"""
		return self.binary() is not None

	def _request(self, path: str, method: str = 'GET') -> str:
		"""One DevTools HTTP call, as text — some endpoints answer plain
		text, so the JSON parsing is left to the callers."""
		request = urllib.request.Request(f'http://{HOST}:{self.port}{path}', method=method)
		with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
			return response.read().decode()

	def _json(self, path: str, method: str = 'GET'):
		return json.loads(self._request(path, method))

	def alive(self) -> bool:
		"""Is the session's Chrome up — the DevTools port answering?"""
		try:
			self._request('/json/version')
		except OSError:  # refused, timed out: not up (URLError is one)
			return False
		return True

	def page_tabs(self) -> list[dict]:
		"""The open page targets — the tabs a user can see and focus."""
		return [tab for tab in self._json('/json/list') if tab.get('type') == 'page']

	def open_tab(self, url: str) -> dict:
		"""A brand-new tab on `url` — in the frontmost window, or a fresh
		window of its own when Chrome is running windowless."""
		encoded = urllib.parse.quote(url, safe='')
		try:
			tab = self._json(f'/json/new?{encoded}', method='PUT')
		except urllib.error.HTTPError:
			tab = self._json(f'/json/new?{encoded}', method='GET')  # Chrome < 111
		if not isinstance(tab, dict) or 'id' not in tab:
			raise RuntimeError(f'the DevTools port refused a new tab: {tab!r}')
		return tab

	def activate(self, tab_id: str) -> None:
		"""The tab to the front of its window, its window to the front of
		Chrome, Chrome to the front of the screen."""
		try:
			self._request(f'/json/activate/{tab_id}')
		except OSError:
			return  # Chrome went away mid-press; the next one relaunches it
		self._front_app()

	def _front_app(self) -> None:
		"""Lift the browser over the terminal — best effort, per platform.

		The app is the one the finder chose: a user on Brave gets
		`tell application "Brave Browser" to activate`, not Chrome's
		name (which would raise the wrong app, or nothing). front()
		swallows every failure — no Automation consent, no wmctrl, no
		Windows equivalent yet — because a window that sits behind the
		TUI is findable, not fatal.
		"""
		binary = self.binary()
		if binary is not None:
			front(binary)

	def _front_tab_url(self) -> str | None:
		"""The URL of the active tab of the front window: macOS only.

		AppleScript tab ids and DevTools target ids are different things,
		so URLs are the only shared key — this is how a just-launched
		Chrome's job tab is identified when the site redirected before
		the port came up and a plain URL match misses.

		Linux and Windows have no equivalent without a per-browser
		extension, so they return None at once and `launch` relies on
		the URL match: a redirect there means the job's tab is open on
		screen but not remembered, and the next enter opens a fresh tab.
		"""
		if platform.system() != 'Darwin':
			return None
		binary = self.binary()
		if binary is None:
			return None
		return active_tab_url(binary)

	def launch(self, url: str) -> str | None:
		"""Start the session's browser, a window on `url`, and return the
		job tab's DevTools id — None if the tab can't be identified (the
		job is still open on screen; the next enter on it just opens a
		fresh tab).

		A Chrome already running on this profile *without* the debug port
		hijacks the launch: it opens the url in a window of its own and
		our process exits without ever owning the port. The timeout error
		says what to do about that.
		"""
		binary = self.binary()
		if binary is None:
			raise RuntimeError(_missing(self.executable))
		subprocess.Popen(
			[
				str(binary),
				f'--user-data-dir={self.profile}',
				f'--remote-debugging-port={self.port}',
				'--no-first-run',
				'--no-default-browser-check',
				url,
			],
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			start_new_session=True,
		)
		deadline = time.monotonic() + LAUNCH_TIMEOUT
		while not self.alive():
			if time.monotonic() > deadline:
				raise RuntimeError(
					f'Chrome did not open its DevTools port within '
					f'{LAUNCH_TIMEOUT}s — a Chrome is probably already '
					f'running on {self.profile.name} without it; quit '
					'that Chrome entirely (⌘Q) and press enter again'
				)
			time.sleep(0.25)
		tabs = self.page_tabs()
		for tab in tabs:
			if tab.get('url') == url:
				return tab['id']
		front = self._front_tab_url()
		if front:
			for tab in tabs:
				if tab.get('url') == front:
					return tab['id']
		return None

	def open_or_focus(self, url: str, remembered: str | None = None) -> str | None:
		"""Enter, for one job: focus the tab the job already has, or open
		it a fresh one — a new tab only when the old has been closed (or
		never existed), never a second while the first lives. A stray tab
		at the same URL, from an earlier session, is adopted rather than
		duplicated.

		Returns the tab's DevOps id to remember for next time; `None`
		when the tab couldn't be identified after a cold launch.
		"""
		try:
			tabs = self.page_tabs()
		except OSError:
			return self.launch(url)  # no session yet: a window of its own on the job
		if remembered:
			for tab in tabs:
				if tab['id'] == remembered:
					self.activate(remembered)
					return remembered
		for tab in tabs:  # a tab from an earlier session, same URL
			if tab.get('url') == url:
				self.activate(tab['id'])
				return tab['id']
		tab = self.open_tab(url)
		self.activate(tab['id'])
		return tab['id']


def _missing(executable: str) -> str:
	"""The launch error, worded for what the user can do about it.

	A configured path that is wrong is its own mistake and should be
	named as one; an empty config means the platform search found
	nothing, and the fix is install-or-configure.
	"""
	if executable.strip():
		return (
			f'browser.executable is {executable!r}, which does not exist — '
			'fix the path in jobtriage.toml, or clear it to auto-detect'
		)
	return (
		'no Chrome, Chromium, Brave or Edge found — install one, or set '
		'browser.executable in jobtriage.toml'
	)


class DefaultBrowser:
	"""`enter` without a Chromium: the machine's default browser.

	The TUI only ever needs `open_or_focus(url, remembered)`, so the
	fallback is the same interface through stdlib `webbrowser` — one
	window/tab per press, handed to whatever the OS has registered. It
	has no DevTools port, so it cannot focus a tab it opened earlier:
	`remembered` is accepted and ignored, `open_or_focus` returns None
	(there is no id to remember), and `focuses_tabs = False` is how the
	app knows to say so in the footer.
	"""

	focuses_tabs = False

	def available(self) -> bool:
		"""A default browser is whatever the OS says it is; there is
		nothing here to check for, and failing at open is the OS's job."""
		return True

	def open_or_focus(self, url: str, remembered: str | None = None) -> None:
		"""Open `url` in the default browser. No tab id: `webbrowser`
		does not offer one back."""
		webbrowser.open(url)
