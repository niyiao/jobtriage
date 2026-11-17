"""Tests for workspace resolution: the four ways to find the op folder
and the error when none of them does.

The resolution takes `env` and `start` as arguments, so no test needs
to monkeypatch os.environ or chdir.
"""

import pytest

from jobtriage import workspace
from jobtriage.workspace import CONFIG_NAME, NoWorkspace, find


@pytest.fixture(autouse=True)
def sandbox_pointer(tmp_path, monkeypatch):
	"""Never read or write the real platform pointer file."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'no-pointer')


@pytest.fixture
def home(tmp_path):
	"""A workspace folder with the marker file, in tmp."""
	marker = tmp_path / 'op'
	marker.mkdir()
	(marker / CONFIG_NAME).write_text('# test workspace\n')
	return marker


def test_explicit_path_wins(home):
	assert find(path=home).root == home


def test_explicit_path_without_marker_is_rejected(tmp_path):
	with pytest.raises(NoWorkspace) as missing:
		find(path=tmp_path)
	assert str(tmp_path) in str(missing.value)


def test_environment_variable(tmp_path, home):
	elsewhere = tmp_path / 'elsewhere'
	elsewhere.mkdir()
	assert find(env={'JOBTRIAGE_HOME': str(home)}, start=elsewhere).root == home


def test_walk_up_from_a_nested_directory(tmp_path, home):
	nested = home / 'letters' / 'draft'
	nested.mkdir(parents=True)
	assert find(start=nested).root == home
	assert find(start=home, env={}).root == home


def test_walk_up_stops_at_the_marker(tmp_path, home):
	"""A folder below the marker resolves to it; a sibling doesn't."""
	sibling = tmp_path / 'sibling'
	sibling.mkdir()
	with pytest.raises(NoWorkspace):
		find(start=sibling, env={})


def test_pointer_file(tmp_path, home, monkeypatch):
	pointer = tmp_path / 'pointer'
	pointer.write_text(str(home) + '\n')
	monkeypatch.setattr(workspace, 'pointer_path', lambda: pointer)
	assert find(start=tmp_path / 'elsewhere', env={}).root == home


def test_stale_pointer_is_ignored(tmp_path, home, monkeypatch):
	"""A pointer at a folder that no longer has its marker resolves to
	nothing."""
	pointer = tmp_path / 'pointer'
	pointer.write_text(str(tmp_path / 'gone'))
	monkeypatch.setattr(workspace, 'pointer_path', lambda: pointer)
	with pytest.raises(NoWorkspace):
		find(start=tmp_path / 'elsewhere', env={})


def test_no_workspace_message(tmp_path, monkeypatch):
	pointer = tmp_path / 'pointer'
	monkeypatch.setattr(workspace, 'pointer_path', lambda: pointer)
	with pytest.raises(NoWorkspace) as missing:
		find(start=tmp_path / 'elsewhere', env={})
	assert 'jobtriage init' in str(missing.value)


def test_workspace_paths_all_live_under_root(home):
	from jobtriage.workspace import Workspace

	ws = Workspace(home)
	assert ws.config == home / CONFIG_NAME
	assert ws.db == home / 'jobtriage.db'
	assert ws.profile == home / 'profile'
	assert ws.letters == home / 'letters'
	assert ws.scrapers == home / 'scrapers'
	assert ws.skills == home / 'skills'
	assert ws.browser_profile == home / '.browser-profile'
	assert ws.state == home / '.jobtriage'
	assert ws.env == home / '.env'
	assert ws.manifest == home / '.jobtriage' / 'manifest.json'


def test_the_tui_state_file_lives_under_the_state_dir(home):
	"""The TUI's bookmark is tool state, not job data: it sits with the
	manifest, under .jobtriage, where deleting it costs a remembered tab
	and nothing else."""
	from jobtriage.workspace import Workspace

	ws = Workspace(home)
	assert ws.tui_state == home / '.jobtriage' / 'tui.json'
	assert ws.tui_state.parent == ws.state


# -- the version stamp and the upgrade notice (plan 08 step 21) ---------


def _stamp(home, text):
	"""Write `.jobtriage/version`; the folder is the only fact that matters."""
	state = home / '.jobtriage'
	state.mkdir(parents=True, exist_ok=True)
	(state / 'version').write_text(text)


def test_an_older_stamp_asks_for_an_upgrade(home):
	from jobtriage.workspace import Workspace

	_stamp(home, '0.1.0')
	notice = workspace.upgrade_notice(Workspace(home))

	assert notice.startswith('notice:')
	assert '0.1.0' in notice
	assert 'jobtriage init --upgrade' in notice
	assert 'templates and skills' in notice


def test_the_current_stamp_is_silent(home):
	from jobtriage.workspace import Workspace, __version__

	_stamp(home, __version__)
	assert workspace.upgrade_notice(Workspace(home)) is None


def test_no_stamp_is_silent(home):
	"""A folder from before the stamp, or one made by hand: nothing to
	compare, and advice would be a guess."""
	from jobtriage.workspace import Workspace

	assert workspace.upgrade_notice(Workspace(home)) is None


def test_a_newer_stamp_is_silent(home):
	"""A downgrade is not "refresh the templates": the folder may be
	ahead on purpose."""
	from jobtriage.workspace import Workspace

	_stamp(home, '99.0.0')
	assert workspace.upgrade_notice(Workspace(home)) is None


def test_the_comparison_is_numeric_not_lexical(home, monkeypatch):
	"""0.10.0 is newer than 0.9.0; string ordering would say otherwise,
	and would nag a 0.10 user about their 0.9 templates forever."""
	from jobtriage.workspace import Workspace

	monkeypatch.setattr(workspace, '__version__', '0.10.0')
	_stamp(home, '0.9.0')
	assert workspace.upgrade_notice(Workspace(home)) is not None
	_stamp(home, '0.10.0')
	assert workspace.upgrade_notice(Workspace(home)) is None
