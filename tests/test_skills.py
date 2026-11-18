"""Tests for the shipped agent skills (plan 05): their format, the
`skills` command, and the global install.

The skills are instructions, not code, so the format is the contract an
agent and a tool both depend on: one directory, a `SKILL.md`, YAML front
matter with `name` and a description that says when to use it. The
generated references have their own drift test in test_skill_refs.py.
"""

import json

import pytest

from jobtriage import workspace
from jobtriage.cli import skills as skills_cli
from jobtriage.cli.main import main

SKILLS = workspace.assets_dir() / 'skills'

EXPECTED = {
	'jobtriage',
	'jobtriage-db',
	'triage',
	'cover-letter',
	'fill-application',
	'add-scraper',
	'tune-filters',
	'setup',
}


@pytest.fixture(autouse=True)
def sandbox_pointer(tmp_path, monkeypatch):
	"""`workspace.create` writes the platform pointer; keep it in tmp."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')


@pytest.fixture
def op(tmp_path):
	return tmp_path / 'op'


def test_every_skill_is_a_directory_with_front_matter():
	folders = {
		path.name for path in SKILLS.iterdir() if path.is_dir() and (path / 'SKILL.md').is_file()
	}

	assert folders == EXPECTED
	for name in EXPECTED:
		text = (SKILLS / name / 'SKILL.md').read_text(encoding='utf-8')
		assert text.startswith('---\n'), f'{name} has no front matter'
		front = text[3 : text.index('\n---', 3)]
		assert f'name: {name}' in front
		description = next(line for line in front.splitlines() if line.startswith('description:'))
		assert len(description.removeprefix('description:')) > 60
		assert len(text.splitlines()) <= 150, f'{name} is too long for one skill page'


def test_the_umbrella_has_the_generated_cli_reference():
	assert (SKILLS / 'jobtriage' / 'references' / 'cli.md').is_file()


def test_the_db_skill_has_the_generated_schema_and_recipes():
	assert (SKILLS / 'jobtriage-db' / 'references' / 'schema.md').is_file()
	assert (SKILLS / 'jobtriage-db' / 'references' / 'recipes.sql').is_file()


def test_the_listing_names_every_shipped_and_installed_skill(op):
	ws, _notes = workspace.create(op)

	document = skills_cli.listing(ws, home=op / 'home', xdg=op / 'xdg')

	assert {record['name'] for record in document['shipped']} == EXPECTED
	assert {record['name'] for record in document['installed']} == EXPECTED
	assert all(record['description'] for record in document['shipped'])
	assert document['global']['claude'] == []


def test_skills_list_json_is_one_document(op, capsys):
	workspace.create(op)

	assert main(['--workspace', str(op), 'skills', 'list', '--json']) == 0

	document = json.loads(capsys.readouterr().out)
	assert {record['name'] for record in document['installed']} == EXPECTED


def test_global_install_copies_each_skill_to_each_agent(op, tmp_path):
	ws, _notes = workspace.create(op)
	home = tmp_path / 'home'
	xdg = tmp_path / 'xdg'

	results = skills_cli.install(ws, ['claude', 'opencode', 'codex'], home=home, xdg=xdg)

	assert [agent for agent, _target, _count in results] == ['claude', 'opencode', 'codex']
	for _agent, target, count in results:
		assert count == len(EXPECTED)
		assert {path.name for path in target.iterdir()} == EXPECTED
		assert (target / 'jobtriage' / 'references' / 'cli.md').is_file()

	assert skills_cli.global_dir('claude', home=home, xdg=xdg) == home / '.claude' / 'skills'
	assert skills_cli.global_dir('codex', home=home, xdg=xdg) == home / '.codex' / 'skills'
	assert skills_cli.global_dir('opencode', home=home, xdg=xdg) == xdg / 'opencode' / 'skills'


def test_global_install_needs_the_global_flag(op, capsys):
	workspace.create(op)

	assert main(['--workspace', str(op), 'skills', 'install', '--agent', 'claude']) == 2
	assert '--global' in capsys.readouterr().err
