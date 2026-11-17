"""Tests for `jobtriage init`: the tree it builds, its idempotence,
and the upgrade rule — untouched files replaced, edited files left
alone with the new version beside them as <file>.new.

The tests swap the shipped assets for a fixture directory, so they
exercise the logic without depending on the real templates (and can
"ship" a new version mid-test).
"""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobtriage import db, workspace
from jobtriage.cli import init as init_cli
from jobtriage.workspace import create, upgrade

V1 = {
	'jobtriage.toml': '# config v1\n',
	'AGENTS.md': 'agents v1',
	'gitignore': 'db\n',
	'profile/README.md': 'profile\n',
	'scrapers/_example.py': 'NAME = "example"\n',
}
V2 = {
	'jobtriage.toml': '# config v2\n',
	'AGENTS.md': 'agents v2',
	'gitignore': 'db\nlogs\n',
	'profile/README.md': 'profile\n',
	'scrapers/_example.py': 'NAME = "example"\n',
	'skills/README.md': 'skills\n',  # new in v2: the upgrade adds it
}


def _write(folder: Path, files: dict) -> Path:
	"""One version of the assets, written into `folder`."""
	for rel, text in files.items():
		path = folder / rel
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(text)
	return folder


@pytest.fixture(autouse=True)
def sandbox_pointer(tmp_path, monkeypatch):
	"""Never let a test write the real platform pointer file."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')


@pytest.fixture
def assets(tmp_path, monkeypatch):
	"""A fake assets directory the tests can re-version."""
	folder = tmp_path / 'assets'
	folder.mkdir()
	_write(folder, V1)
	monkeypatch.setattr(workspace, 'assets_dir', lambda: folder)
	return folder


@pytest.fixture
def op(tmp_path):
	return tmp_path / 'op'


def test_fresh_init_builds_the_tree(op, assets):
	ws, notes = create(op)
	assert ws.config.is_file()
	assert (ws.root / '.gitignore').read_text() == 'db\n'  # renamed from 'gitignore'
	assert ws.profile.is_dir()
	assert ws.letters.is_dir()
	assert ws.scrapers.is_dir()
	assert ws.skills.is_dir()
	assert ws.browser_profile.is_dir()
	assert ws.state.is_dir()
	assert ws.version_file.read_text() == workspace.__version__
	manifest = json.loads(ws.manifest.read_text())
	assert set(manifest) == set(V1)
	assert manifest['AGENTS.md'] == hashlib.sha256(b'agents v1').hexdigest()
	assert notes


def test_the_scraper_template_lands_in_the_op_folder(op, assets):
	"""Plan 04 step 7: `sources new` copies `_example.py`, so `init` has
	to ship it — and the underscore keeps the registry from loading it."""
	create(op)
	assert (op / 'scrapers' / '_example.py').read_text() == V1['scrapers/_example.py']


def test_second_init_changes_nothing(op, assets):
	create(op)
	before = {p: p.stat().st_mtime_ns for p in sorted(op.rglob('*'))}
	create(op)
	after = {p: p.stat().st_mtime_ns for p in sorted(op.rglob('*'))}
	assert before == after  # nothing rewritten, not even identically


def test_init_never_overwrites_a_user_file(op, assets):
	op.mkdir()
	(op / 'AGENTS.md').write_text('my own instructions')
	_ws, _ = create(op)
	assert (op / 'AGENTS.md').read_text() == 'my own instructions'


def test_upgrade_replaces_an_untouched_file(op, assets):
	create(op)
	_write(assets, V2)
	_ws, _notes = upgrade(op)
	assert (op / 'AGENTS.md').read_text() == 'agents v2'
	assert (op / '.gitignore').read_text() == 'db\nlogs\n'
	assert not (op / 'AGENTS.md.new').exists()


def test_upgrade_leaves_an_edited_file_alone_with_new_beside_it(op, assets):
	create(op)
	(op / 'AGENTS.md').write_text('my own instructions, edited')
	_write(assets, V2)
	upgrade(op)
	assert (op / 'AGENTS.md').read_text() == 'my own instructions, edited'
	assert (op / 'AGENTS.md.new').read_text() == 'agents v2'


def test_upgrade_leaves_a_file_we_never_shipped_alone(op, assets):
	"""An asset that's new this version, sitting where the user
	already has a file of their own: theirs stays, no .new, no
	manifest claim."""
	create(op)
	(op / 'skills' / 'README.md').write_text('my own skills notes')
	_write(assets, V2)
	upgrade(op)
	assert (op / 'skills' / 'README.md').read_text() == 'my own skills notes'
	assert not (op / 'skills' / 'README.md.new').exists()


def test_upgrade_rewrites_an_old_version_stamp(op, assets):
	"""The stamp is what the upgrade notice compares; `--upgrade` is the
	command that silences it."""
	create(op)
	ws = workspace.Workspace(op)
	ws.version_file.write_text('0.0.1')

	upgrade(op)

	assert ws.version_file.read_text() == workspace.__version__


def test_upgrade_on_a_non_workspace_is_refused(tmp_path, assets):
	with pytest.raises(workspace.NoWorkspace):
		upgrade(tmp_path / 'not-there')


def test_the_upgrade_command_migrates_the_database(op, assets, capsys):
	"""Doctor's remedy for an old schema is `init --upgrade`, so that
	command has to actually move the schema — not just the assets."""
	create(op)
	db.migrate(op / 'jobtriage.db', op / '.jobtriage' / 'backups')
	connection = db.connect(op / 'jobtriage.db')
	# A real version-2 database: migration 3's table is not there yet.
	connection.execute('DROP TABLE source_runs')
	connection.execute(f'PRAGMA user_version = {db.LATEST - 1}')
	connection.commit()
	connection.close()

	code = init_cli.run(SimpleNamespace(upgrade=True, path=op))
	assert code == 0
	assert db.schema_version(op / 'jobtriage.db') == db.LATEST
	assert f'database: schema {db.LATEST}' in capsys.readouterr().out


def test_manifest_tracks_the_shipped_version(op, assets):
	create(op)
	_write(assets, V2)
	upgrade(op)
	manifest = json.loads((op / '.jobtriage' / 'manifest.json').read_text())
	assert manifest['AGENTS.md'] == hashlib.sha256(b'agents v2').hexdigest()


# -- the agent discovery links -----------------------------------------


@pytest.mark.skipif(
	os.name == 'nt',
	reason='symlinks need a privilege on Windows; the copy-fallback test covers it',
)
def test_init_links_the_agent_folders_to_skills(op, assets):
	create(op)

	for name in workspace.AGENT_FOLDERS:
		link = op / name / 'skills'
		assert link.is_symlink(), f'{name}/skills should be a link'
		assert link.resolve() == (op / 'skills').resolve()

	claude = op / 'CLAUDE.md'
	assert claude.is_symlink()
	assert claude.resolve() == (op / 'AGENTS.md').resolve()


def test_init_leaves_an_agent_folder_of_the_users_alone(op, assets):
	(op / '.claude' / 'skills').mkdir(parents=True)
	(op / '.claude' / 'skills' / 'mine.txt').write_text('mine')
	(op / 'CLAUDE.md').write_text('my own claude file')

	create(op)

	assert not (op / '.claude' / 'skills').is_symlink()
	assert (op / '.claude' / 'skills' / 'mine.txt').read_text() == 'mine'
	assert (op / 'CLAUDE.md').read_text() == 'my own claude file'


def test_init_copies_when_symlinks_are_not_permitted(op, assets, monkeypatch):
	"""Windows needs a privilege for symlinks; the fallback is a copy,
	and `--upgrade` has to keep that copy in step."""

	def refuse(self, target, target_is_directory=False):
		raise OSError('symlink not permitted')

	monkeypatch.setattr(Path, 'symlink_to', refuse)
	create(op)

	for name in workspace.AGENT_FOLDERS:
		copied = op / name / 'skills'
		assert copied.is_dir() and not copied.is_symlink()
	assert not (op / 'CLAUDE.md').is_symlink()
	assert (op / 'CLAUDE.md').read_text() == (op / 'AGENTS.md').read_text()

	# a skill added to the assets after the first init is refreshed
	# into the copies by --upgrade, not just into <op>/skills
	(assets / 'skills' / 'jobtriage').mkdir(parents=True)
	(assets / 'skills' / 'jobtriage' / 'SKILL.md').write_text('v2 skill')
	upgrade(op)
	assert (op / '.claude' / 'skills' / 'jobtriage' / 'SKILL.md').read_text() == 'v2 skill'
