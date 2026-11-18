"""The browser finder: a configured path first, then the platform's
known locations, first existing file wins.

Each platform is described to `find_browser` rather than detected from
the machine running the test: `system`, `home`, `env`, `which` and
`roots` are the seams, so the macOS, Linux and Windows tables are all
checked on any developer's machine without a Chrome installed.
"""

from pathlib import Path

from jobtriage.browser.find import candidates, find_browser


def touch(path: Path) -> Path:
	"""A file that exists, parents and all — what `is_file()` looks for."""
	path.parent.mkdir(parents=True, exist_ok=True)
	path.write_text('')
	return path


# -- the configured path ---------------------------------------------------


def test_a_configured_executable_wins_over_an_installed_browser(tmp_path):
	configured = touch(tmp_path / 'my-chromium')
	root = tmp_path / 'Applications'
	touch(root / 'Google Chrome.app' / 'Contents' / 'MacOS' / 'Google Chrome')
	found = find_browser(str(configured), system='darwin', home=tmp_path, roots=[root])
	assert found == configured


def test_a_configured_executable_that_does_not_exist_returns_none(tmp_path):
	"""A path the user named is not silently swapped for another browser:
	doctor has to be able to report the typo."""
	assert find_browser(str(tmp_path / 'gone'), system='darwin', home=tmp_path) is None


def test_an_empty_config_and_no_browser_is_none(tmp_path):
	assert find_browser(system='linux', which=lambda name: None) is None


# -- macOS -----------------------------------------------------------------


def test_macos_finds_chrome_in_the_home_applications_folder(tmp_path):
	binary = touch(
		tmp_path / 'Applications' / 'Google Chrome.app' / 'Contents' / 'MacOS' / 'Google Chrome'
	)
	found = find_browser(
		system='darwin',
		home=tmp_path,
		roots=[tmp_path / 'missing', tmp_path / 'Applications'],
	)
	assert found == binary


def test_macos_prefers_chrome_to_a_later_browser(tmp_path):
	root = tmp_path / 'Applications'
	chrome = touch(root / 'Google Chrome.app' / 'Contents' / 'MacOS' / 'Google Chrome')
	touch(root / 'Brave Browser.app' / 'Contents' / 'MacOS' / 'Brave Browser')
	assert find_browser(system='darwin', home=tmp_path, roots=[root]) == chrome


def test_macos_preference_order_is_chrome_chromium_brave_edge(tmp_path):
	paths = candidates(system='darwin', home=tmp_path, roots=[Path('/Applications')])
	assert [path.parent.parent.parent.name for path in paths] == [
		'Google Chrome.app',
		'Chromium.app',
		'Brave Browser.app',
		'Microsoft Edge.app',
	]


def test_macos_searches_both_application_folders_for_each_browser(tmp_path):
	home = tmp_path / 'home'
	binary = touch(home / 'Applications' / 'Chromium.app' / 'Contents' / 'MacOS' / 'Chromium')
	found = find_browser(
		system='darwin',
		home=home,
		roots=[tmp_path / 'system-applications', home / 'Applications'],
	)
	assert found == binary


# -- Linux -----------------------------------------------------------------


def test_linux_uses_the_first_name_path_answers(tmp_path):
	chrome = touch(tmp_path / 'google-chrome')
	brave = touch(tmp_path / 'brave-browser')
	locations = {'google-chrome': str(chrome), 'brave-browser': str(brave)}
	found = find_browser(system='linux', which=locations.get)
	assert found == chrome


def test_linux_falls_through_names_it_does_not_have(tmp_path):
	chromium = touch(tmp_path / 'chromium-browser')
	locations = {'chromium': None, 'chromium-browser': str(chromium)}
	found = find_browser(system='linux', which=locations.get)
	assert found == chromium


def test_linux_with_a_path_hit_that_is_not_a_file_is_none(tmp_path):
	"""`which` promises an executable, but the seam can lie; the finder
	still only returns something that exists."""
	assert find_browser(system='linux', which=lambda name: str(tmp_path / name)) is None


# -- Windows ---------------------------------------------------------------


def windows_env(tmp_path: Path) -> dict[str, str]:
	"""Program Files, its 32-bit sibling and LocalAppData, all in tmp."""
	return {
		'ProgramFiles': str(tmp_path / 'Program Files'),
		'ProgramFiles(x86)': str(tmp_path / 'Program Files (x86)'),
		'LOCALAPPDATA': str(tmp_path / 'Local'),
	}


def test_windows_finds_chrome_under_program_files(tmp_path):
	binary = touch(tmp_path / 'Program Files' / 'Google' / 'Chrome' / 'Application' / 'chrome.exe')
	found = find_browser(system='win32', home=tmp_path, env=windows_env(tmp_path))
	assert found == binary


def test_windows_finds_edge_in_local_appdata(tmp_path):
	binary = touch(tmp_path / 'Local' / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe')
	found = find_browser(system='win32', home=tmp_path, env=windows_env(tmp_path))
	assert found == binary


def test_windows_finds_brave_under_program_files_x86(tmp_path):
	binary = touch(
		tmp_path
		/ 'Program Files (x86)'
		/ 'BraveSoftware'
		/ 'Brave-Browser'
		/ 'Application'
		/ 'brave.exe'
	)
	found = find_browser(system='win32', home=tmp_path, env=windows_env(tmp_path))
	assert found == binary


def test_windows_prefers_chrome_to_edge(tmp_path):
	env = windows_env(tmp_path)
	chrome = touch(tmp_path / 'Program Files' / 'Google' / 'Chrome' / 'Application' / 'chrome.exe')
	touch(tmp_path / 'Local' / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe')
	assert find_browser(system='win32', home=tmp_path, env=env) == chrome
