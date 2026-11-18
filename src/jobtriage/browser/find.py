"""Finding the Chromium-family browser this machine already has.

Three callers need the same answer and must not each invent one: the
TUI's opener (does `enter` get tabs, or the OS's default browser?),
`jobtriage open` (the same choice, one process), and `jobtriage doctor`
(which binary did we find, before blaming the user for a missing one).
The answer is a path to an executable, or None.

`browser.executable` in jobtriage.toml wins when it is set: a person
who names a binary means that one, so a missing file returns None
rather than silently falling through to a browser they did not choose
— doctor can then say the configured path is wrong instead of opening
something else.

With no config, the search is a preference order over the browsers the
tool has actually been tested against, and the first file that exists
wins:

    macOS    Chrome, Chromium, Brave, Edge — each in /Applications and
             then ~/Applications, as <app>.app/Contents/MacOS/<binary>
    Linux    google-chrome, chromium, chromium-browser, brave-browser,
             microsoft-edge on PATH
    Windows  Chrome, Chromium, Brave, Edge under %ProgramFiles%,
             %ProgramFiles(x86)% and %LOCALAPPDATA%

Everything here is filesystem and `shutil.which`: no network, no
subprocess, no launch. `system`, `home`, `env`, `which` and `roots`
exist so a test can describe each platform without owning the machine
it runs on; the tool passes none of them.
"""

import os
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

# (bundle name, executable inside Contents/MacOS). The order is the
# preference order: Chrome first because it is the one the research and
# the DevTools work were built against.
MACOS = (
	('Google Chrome', 'Google Chrome'),
	('Chromium', 'Chromium'),
	('Brave Browser', 'Brave Browser'),
	('Microsoft Edge', 'Microsoft Edge'),
)

# The names a Linux package puts on PATH, in preference order.
LINUX = (
	'google-chrome',
	'chromium',
	'chromium-browser',
	'brave-browser',
	'microsoft-edge',
)

# Relative to a Windows install root. Chrome and Chromium install under
# Program Files; Brave and Edge also write a per-user copy under
# LocalAppData when installed without an administrator. Split into path
# parts rather than one backslashed string, because joining parts works
# on the POSIX machine the tests run on as well as on Windows.
WINDOWS = (
	('Google', 'Chrome', 'Application', 'chrome.exe'),
	('Chromium', 'Application', 'chrome.exe'),
	('BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
	('Microsoft', 'Edge', 'Application', 'msedge.exe'),
)


def find_browser(
	executable: str = '',
	*,
	system: str | None = None,
	home: Path | None = None,
	env: Mapping[str, str] | None = None,
	which=None,
	roots: Sequence[Path] | None = None,
) -> Path | None:
	"""The browser binary to drive, or None when this machine has none.

	A configured `executable` is taken as given: it must exist, and a
	missing file is None (not a different browser). With no config the
	platform's candidates are tried in preference order. The keyword
	seams are for tests; production callers pass a config string at
	most.
	"""
	configured = executable.strip()
	if configured:
		path = Path(configured).expanduser()
		return path if path.is_file() else None

	for path in candidates(system=system, home=home, env=env, which=which, roots=roots):
		if path.is_file():
			return path
	return None


def candidates(
	*,
	system: str | None = None,
	home: Path | None = None,
	env: Mapping[str, str] | None = None,
	which=None,
	roots: Sequence[Path] | None = None,
) -> list[Path]:
	"""Every place this platform might keep a browser, best first.

	Paths are returned whether or not they exist: `find_browser` is the
	existence check, and doctor may want the list to explain what it
	looked at later. `roots` overrides the install folders on macOS and
	Windows — the one seam a test needs to describe those platforms in
	a tmp directory.
	"""
	system = sys.platform if system is None else system
	home = Path.home() if home is None else Path(home)
	env = os.environ if env is None else env
	which = shutil.which if which is None else which

	if system == 'darwin':
		applications = (
			tuple(roots)
			if roots is not None
			else (
				Path('/Applications'),
				home / 'Applications',
			)
		)
		return _macos_candidates(applications)
	if system.startswith('win'):
		install_roots = tuple(roots) if roots is not None else _windows_roots(home, env)
		return _windows_candidates(install_roots)
	return _linux_candidates(which)


def _macos_candidates(applications: Sequence[Path]) -> list[Path]:
	"""Chrome in both application folders, then Chromium's, and so on.

	The loop order is deliberate: an app's home copy beats a later
	browser's system-wide copy, because the user installed it for
	themselves.
	"""
	return [
		root / f'{bundle}.app' / 'Contents' / 'MacOS' / binary
		for bundle, binary in MACOS
		for root in applications
	]


def _linux_candidates(which) -> list[Path]:
	"""The first PATH hit for each name, in preference order."""
	found = []
	for name in LINUX:
		location = which(name)
		if location:
			found.append(Path(location))
	return found


def _windows_roots(home: Path, env: Mapping[str, str]) -> tuple[Path, ...]:
	"""Program Files, Program Files (x86), then the per-user install root.

	The variables are read with the defaults Windows itself sets, so a
	stripped environment (a service, a test) still searches the usual
	places. An empty variable is treated as unset, which is what a
	shell that clears it leaves behind.
	"""
	local = env.get('LOCALAPPDATA') or home / 'AppData' / 'Local'
	return (
		Path(env.get('ProgramFiles') or r'C:\Program Files'),
		Path(env.get('ProgramFiles(x86)') or r'C:\Program Files (x86)'),
		Path(local),
	)


def _windows_candidates(install_roots: Sequence[Path]) -> list[Path]:
	"""Chrome across every root, then Chromium's, and so on."""
	return [root.joinpath(*parts) for parts in WINDOWS for root in install_roots]
