"""Tests for `jobtriage config`: show's provenance, get/set round trips
that keep the user's comments, validate's exit codes, and edit's editor
handling.

The `ws` fixture is a folder with only the marker file, so each test
writes exactly the config it means to exercise. `config show` reads the
environment, so the usual autouse fixture removes a developer's
JOBTRIAGE_* variables; tests that need one set it explicitly.
"""

import json
import os
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobtriage import workspace
from jobtriage.cli import config as config_cli
from jobtriage.cli.main import main


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
	"""No real pointer file, no stray config variables."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')
	monkeypatch.delenv('JOBTRIAGE_HOME', raising=False)
	for name in list(os.environ):
		if name.startswith('JOBTRIAGE_') and name != 'JOBTRIAGE_HOME':
			monkeypatch.delenv(name, raising=False)


def run_config(ws, *args):
	"""The command the way a person types it."""
	return main(['--workspace', str(ws.root), 'config', *args])


# -- show: the effective config and where each value came from ---------


def test_show_prints_sections_and_the_layer_for_each_value(ws, capsys):
	ws.config.write_text('[search]\nlimit = 10\n')

	assert run_config(ws, 'show') == 0
	out = capsys.readouterr().out

	assert '[search]' in out
	assert '[filter]' in out
	assert 'limit = 10  # file' in out
	assert 'locations = ["worldwide"]  # default' in out
	assert 'preset = ""  # default' in out
	# the template's commented keys are not part of the effective config
	assert 'max_age_days = 21  # default' in out
	assert '# max_age_days' not in out


def test_show_json_is_the_config_plus_a_provenance_map(ws, capsys):
	ws.config.write_text(
		'[search]\nlimit = 10\n\n[filter]\npreset = "web-engineer"\nexclude_any = ["wordpress"]\n'
	)

	assert run_config(ws, 'show', '--json') == 0
	document = json.loads(capsys.readouterr().out)

	assert document['search']['limit'] == 10
	assert document['search']['locations'] == ['worldwide']
	assert document['search']['max_age_days'] == 21
	assert document['filter']['preset'] == 'web-engineer'
	assert document['filter']['exclude_any'][-1] == 'wordpress'
	assert document['sources']['disabled'] == []
	assert document['provenance']['search.limit'] == 'file'
	assert document['provenance']['search.max_age_days'] == 'default'
	assert document['provenance']['filter.preset'] == 'file'
	assert document['provenance']['filter.exclude_any'] == 'preset:web-engineer+file'
	assert document['provenance']['filter.require_any'] == 'preset:web-engineer'


def test_show_json_names_the_environment_variable(ws, monkeypatch, capsys):
	monkeypatch.setenv('JOBTRIAGE_SEARCH_LIMIT', '7')

	assert run_config(ws, 'show', '--json') == 0
	document = json.loads(capsys.readouterr().out)

	assert document['search']['limit'] == 7
	assert document['provenance']['search.limit'] == 'env:JOBTRIAGE_SEARCH_LIMIT'


def test_show_reports_a_broken_config_instead_of_a_traceback(ws, capsys):
	ws.config.write_text('[search]\nlimit = "ten"\n')

	assert run_config(ws, 'show') == 1
	err = capsys.readouterr().err

	assert err.startswith('error:')
	assert 'search.limit' in err
	assert 'expected an integer' in err


# -- get: the effective value, not just the file ------------------------


def test_get_reads_the_effective_value(ws, monkeypatch, capsys):
	ws.config.write_text('[search]\nlimit = 10\n')
	monkeypatch.setenv('JOBTRIAGE_SEARCH_LIMIT', '7')

	assert run_config(ws, 'get', 'search.limit') == 0
	assert capsys.readouterr().out.strip() == '7'

	assert run_config(ws, 'get', 'search.locations') == 0
	assert capsys.readouterr().out.strip() == '["worldwide"]'

	assert run_config(ws, 'get', 'filter.preset') == 0
	assert capsys.readouterr().out.strip() == '""'


def test_get_json_keeps_the_values_type(ws, capsys):
	ws.config.write_text('[search]\nlimit = 10\n')

	assert run_config(ws, 'get', 'search.limit', '--json') == 0
	assert capsys.readouterr().out.strip() == '10'

	assert run_config(ws, 'get', 'search.locations', '--json') == 0
	assert json.loads(capsys.readouterr().out) == ['worldwide']

	assert run_config(ws, 'get', 'filter.preset', '--json') == 0
	assert json.loads(capsys.readouterr().out) is None  # an unset preset is JSON null

	assert run_config(ws, 'get', 'search', '--json') == 0
	section = json.loads(capsys.readouterr().out)
	assert section['limit'] == 10  # a whole section is a JSON object, not a traceback
	assert section['locations'] == ['worldwide']


def test_get_reads_a_per_board_override(ws, capsys):
	ws.config.write_text('[sources.wellfound]\nmax_pages = 8\n')

	assert run_config(ws, 'get', 'sources.wellfound.max_pages') == 0
	assert capsys.readouterr().out.strip() == '8'


def test_get_unknown_key_is_a_clean_error(ws, capsys):
	assert run_config(ws, 'get', 'search.nope') == 1
	err = capsys.readouterr().err
	assert err.startswith('error:')
	assert 'no such config key' in err

	assert run_config(ws, 'get', 'sources.wellfound.nope') == 1
	assert 'no such per-board config value' in capsys.readouterr().err


# -- set: tomlkit edits that keep the comments, validated ---------------


def test_set_get_round_trip_preserves_an_unrelated_comment(ws, capsys):
	original = '# keep this comment — it documents the file\n[search]\n# and this one\nlimit = 10\n'
	# UTF-8 explicitly: the loader's contract is UTF-8 TOML, and
	# `write_text` alone would use the locale codec (cp1252 on Windows),
	# whose em-dash byte the loader rightly refuses.
	ws.config.write_text(original, encoding='utf-8')

	assert run_config(ws, 'set', 'search.limit', '20') == 0
	assert capsys.readouterr().out.strip() == 'search.limit = 20'

	text = ws.config.read_text()
	assert '# keep this comment' in text
	assert '# and this one' in text
	assert 'limit = 20' in text

	assert run_config(ws, 'get', 'search.limit') == 0
	assert capsys.readouterr().out.strip() == '20'


def test_set_parses_toml_values_then_bare_strings(ws, capsys):
	assert run_config(ws, 'set', 'search.locations', '["emea", "NG"]') == 0
	assert run_config(ws, 'set', 'filter.preset', 'any') == 0

	document = tomllib.loads(ws.config.read_text(encoding='utf-8'))
	assert document['search']['locations'] == ['emea', 'NG']
	assert document['filter']['preset'] == 'any'
	assert capsys.readouterr().out.splitlines() == [
		'search.locations = ["emea", "NG"]',
		'filter.preset = "any"',
	]


def test_set_creates_a_missing_board_table(ws, capsys):
	assert run_config(ws, 'set', 'sources.wellfound.max_pages', '8') == 0
	capsys.readouterr()

	assert '[sources.wellfound]' in ws.config.read_text()
	assert run_config(ws, 'get', 'sources.wellfound.max_pages') == 0
	assert capsys.readouterr().out.strip() == '8'


def test_set_rolls_back_a_value_the_loader_refuses(ws, capsys):
	original = '# keep\n[search]\nlimit = 10\n'
	ws.config.write_text(original)

	assert run_config(ws, 'set', 'search.locations', '["emae"]') == 1
	err = capsys.readouterr().err
	assert 'search.locations[0]' in err
	assert 'emae' in err and 'emea' in err
	assert ws.config.read_text() == original


def test_set_rejects_a_key_without_a_section(ws, capsys):
	original = ws.config.read_text()
	assert run_config(ws, 'set', 'limit', '20') == 1
	err = capsys.readouterr().err
	assert 'section.name' in err
	assert ws.config.read_text() == original


# -- validate -----------------------------------------------------------


def test_validate_prints_ok(ws, capsys):
	ws.config.write_text('[search]\nlimit = 3\n')

	assert run_config(ws, 'validate') == 0
	assert capsys.readouterr().out.strip() == 'ok'


def test_validate_exits_1_with_the_loader_message(ws, capsys):
	ws.config.write_text('[search]\nlimit = "ten"\n')

	assert run_config(ws, 'validate') == 1
	err = capsys.readouterr().err
	assert err.startswith('error:')
	assert 'search.limit' in err
	assert 'expected an integer' in err


# -- edit: $EDITOR, then the same validation ---------------------------


def test_edit_runs_editor_and_validates(ws, monkeypatch, capsys):
	seen = {}

	def fake_run(argv, check=False):
		seen['argv'] = argv
		Path(argv[-1]).write_text('[search]\nlimit = 3\n')
		return SimpleNamespace(returncode=0)

	monkeypatch.setattr(config_cli.subprocess, 'run', fake_run)
	monkeypatch.setenv('EDITOR', 'my-editor --wait')

	assert run_config(ws, 'edit') == 0
	assert seen['argv'][:2] == ['my-editor', '--wait']
	assert seen['argv'][-1] == str(ws.config)
	assert capsys.readouterr().out.strip() == 'ok'


def test_edit_reports_what_the_editor_broke(ws, monkeypatch, capsys):
	def fake_run(argv, check=False):
		Path(argv[-1]).write_text('[search]\nlimit = "ten"\n')
		return SimpleNamespace(returncode=0)

	monkeypatch.setattr(config_cli.subprocess, 'run', fake_run)
	monkeypatch.setenv('EDITOR', 'vi')

	assert run_config(ws, 'edit') == 1
	err = capsys.readouterr().err
	assert 'search.limit' in err
	assert 'expected an integer' in err


def test_edit_missing_editor_is_a_clean_error(ws, monkeypatch, capsys):
	def fake_run(argv, check=False):
		raise FileNotFoundError

	monkeypatch.setattr(config_cli.subprocess, 'run', fake_run)
	monkeypatch.setenv('EDITOR', 'no-such-editor')

	assert run_config(ws, 'edit') == 1
	err = capsys.readouterr().err
	assert "'no-such-editor' not found" in err
	assert '$EDITOR' in err


def test_edit_creates_a_missing_file_from_the_shipped_template(tmp_path, monkeypatch, capsys):
	"""A workspace normally has the marker, but Workspace can be built
	directly; the editor should still open a documented file."""
	root = tmp_path / 'bare'
	root.mkdir()
	ws = workspace.Workspace(root)
	seen = {}

	def fake_run(argv, check=False):
		seen['text'] = Path(argv[-1]).read_text()
		return SimpleNamespace(returncode=0)

	monkeypatch.setattr(config_cli.subprocess, 'run', fake_run)
	monkeypatch.setenv('EDITOR', 'vi')

	assert config_cli.run_edit(SimpleNamespace(), ws) == 0
	assert ws.config.is_file()
	assert '[search]' in seen['text']
	assert '# locations' in seen['text']
	assert capsys.readouterr().out.strip() == 'ok'


def test_edit_reports_a_nonzero_editor_status(ws, monkeypatch, capsys):
	def fake_run(argv, check=False):
		return SimpleNamespace(returncode=1)

	monkeypatch.setattr(config_cli.subprocess, 'run', fake_run)
	monkeypatch.setenv('EDITOR', 'vi')

	assert run_config(ws, 'edit') == 1
	assert 'exited with status 1' in capsys.readouterr().err
