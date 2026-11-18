"""The window-raising interface: one behaviour per platform, all silent.

Nothing here touches the real desktop: `platform.system`, `shutil.which`
and `subprocess.run` are the seams, so each platform can be described on
the machine the tests happen to run on. The one rule every test pins is
that a missing tool or a failed call is a no-op, never an exception.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobtriage import chrome
from jobtriage.browser import front


@pytest.fixture(autouse=True)
def no_real_calls(monkeypatch):
	"""A test that forgets to fake a call gets a loud failure, not osascript."""
	calls = []

	def fake_run(command, **options):
		calls.append((command, options))
		return SimpleNamespace(stdout='', returncode=0)

	monkeypatch.setattr(front.subprocess, 'run', fake_run)
	return calls


# -- app_name --------------------------------------------------------------


def test_a_bundle_name_is_wanted_without_the_app_suffix():
	binary = Path('/Applications/Brave Browser.app/Contents/MacOS/Brave Browser')
	assert front.app_name(binary) == 'Brave Browser'


def test_edge_and_chromium_bundles_name_themselves():
	assert (
		front.app_name(Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'))
		== 'Microsoft Edge'
	)
	assert front.app_name(Path('~/Applications/Chromium.app/Contents/MacOS/Chromium')) == 'Chromium'


def test_a_path_outside_a_bundle_falls_back_to_the_file_name():
	"""browser.executable may point anywhere; the binary's own name is
	the best name left, and a dotted one keeps its extension."""
	assert front.app_name(Path('/opt/weird-chrome')) == 'weird-chrome'
	assert front.app_name(Path('/usr/bin/chrome.exe')) == 'chrome.exe'


# -- macOS -----------------------------------------------------------------


def test_macos_activates_the_found_browsers_bundle(monkeypatch, no_real_calls):
	monkeypatch.setattr(front.platform, 'system', lambda: 'Darwin')
	monkeypatch.setattr(front.shutil, 'which', lambda name: '/usr/bin/osascript')

	front.front(Path('/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'))

	assert len(no_real_calls) == 1
	command, options = no_real_calls[0]
	assert command == ['osascript', '-e', 'tell application "Brave Browser" to activate']
	assert options['check'] is False
	assert options['timeout'] == front.TIMEOUT


def test_macos_without_osascript_does_nothing(monkeypatch, no_real_calls):
	monkeypatch.setattr(front.platform, 'system', lambda: 'Darwin')
	monkeypatch.setattr(front.shutil, 'which', lambda name: None)

	front.front(Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))

	assert no_real_calls == []


# -- Linux -----------------------------------------------------------------


def test_linux_prefers_wmctrl_and_uses_the_display_name(monkeypatch, no_real_calls):
	monkeypatch.setattr(front.platform, 'system', lambda: 'Linux')
	monkeypatch.setattr(front.shutil, 'which', lambda name: '/usr/bin/wmctrl')

	front.front(Path('/usr/bin/brave-browser'))

	assert [call[0] for call in no_real_calls] == [['wmctrl', '-a', 'Brave']]


def test_linux_falls_back_to_xdotool(monkeypatch, no_real_calls):
	monkeypatch.setattr(front.platform, 'system', lambda: 'Linux')
	monkeypatch.setattr(
		front.shutil, 'which', lambda name: '/usr/bin/xdotool' if name == 'xdotool' else None
	)

	front.front(Path('/usr/bin/google-chrome'))

	assert [call[0] for call in no_real_calls] == [
		['xdotool', 'search', '--onlyvisible', '--name', 'Google Chrome', 'windowactivate']
	]


def test_linux_with_neither_tool_does_nothing(monkeypatch, no_real_calls):
	monkeypatch.setattr(front.platform, 'system', lambda: 'Linux')
	monkeypatch.setattr(front.shutil, 'which', lambda name: None)

	front.front(Path('/usr/bin/chromium'))

	assert no_real_calls == []


# -- Windows and failures --------------------------------------------------


def test_windows_is_a_deliberate_no_op(monkeypatch, no_real_calls):
	monkeypatch.setattr(front.platform, 'system', lambda: 'Windows')
	monkeypatch.setattr(front.shutil, 'which', lambda name: '/whatever')

	front.front(Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe'))

	assert no_real_calls == []


def test_a_failed_or_missing_command_is_swallowed(monkeypatch):
	monkeypatch.setattr(front.platform, 'system', lambda: 'Darwin')
	monkeypatch.setattr(front.shutil, 'which', lambda name: '/usr/bin/osascript')

	def refuse(command, **options):
		raise OSError('no Automation consent')

	monkeypatch.setattr(front.subprocess, 'run', refuse)

	front.front(Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))  # no raise


def test_a_timed_out_command_is_swallowed(monkeypatch):
	monkeypatch.setattr(front.platform, 'system', lambda: 'Darwin')
	monkeypatch.setattr(front.shutil, 'which', lambda name: '/usr/bin/osascript')

	def hang(command, **options):
		raise subprocess.TimeoutExpired(command, front.TIMEOUT)

	monkeypatch.setattr(front.subprocess, 'run', hang)

	front.front(Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'))


# -- the AppleScript tab fallback, behind chrome.Browser -------------------


def test_the_tab_url_fallback_is_macos_only(monkeypatch):
	"""Linux and Windows rely on URL matching; no osascript is attempted."""
	monkeypatch.setattr(chrome.platform, 'system', lambda: 'Linux')
	monkeypatch.setattr(chrome, 'find_browser', lambda executable='': Path('/usr/bin/chromium'))
	called = []
	monkeypatch.setattr(chrome, 'active_tab_url', lambda binary: called.append(binary))

	assert chrome.Browser(Path('/tmp/profile'))._front_tab_url() is None
	assert called == []


def test_the_tab_url_fallback_names_the_found_browser(monkeypatch):
	monkeypatch.setattr(chrome.platform, 'system', lambda: 'Darwin')
	binary = Path('/Applications/Brave Browser.app/Contents/MacOS/Brave Browser')
	monkeypatch.setattr(chrome, 'find_browser', lambda executable='': binary)
	seen = []
	monkeypatch.setattr(chrome, 'active_tab_url', lambda found: seen.append(found) or 'https://x')

	assert chrome.Browser(Path('/tmp/profile'))._front_tab_url() == 'https://x'
	assert seen == [binary]


def test_activate_raises_the_browser_the_finder_found(monkeypatch):
	binary = Path('/Applications/Brave Browser.app/Contents/MacOS/Brave Browser')
	browser = chrome.Browser(Path('/tmp/profile'))
	monkeypatch.setattr(chrome, 'find_browser', lambda executable='': binary)
	monkeypatch.setattr(browser, '_request', lambda path, method='GET': '')
	seen = []
	monkeypatch.setattr(chrome, 'front', seen.append)

	browser.activate('tab-id')

	assert seen == [binary]
