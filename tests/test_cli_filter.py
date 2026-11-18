"""`jobtriage filter test`: the verdict line, the exit code that carries
it, and the preset override — the one-title half of plan 03 step 7.

Each test writes the config it means to exercise into the `ws` fixture,
so nothing reads a developer's own op folder or a stray JOBTRIAGE_*
variable.
"""

import os

import pytest

from jobtriage import workspace
from jobtriage.cli.main import main


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
	"""No real pointer file, no stray config variables."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')
	monkeypatch.delenv('JOBTRIAGE_HOME', raising=False)
	for name in list(os.environ):
		if name.startswith('JOBTRIAGE_') and name != 'JOBTRIAGE_HOME':
			monkeypatch.delenv(name, raising=False)


def run_filter(ws, *args):
	"""The command the way a person types it."""
	return main(['--workspace', str(ws.root), 'filter', 'test', *args])


def test_a_kept_title_names_the_gate_and_the_word(ws, capsys):
	ws.config.write_text('[filter]\npreset = "web-engineer"\n')

	assert run_filter(ws, 'Senior Software Engineer') == 0
	out = capsys.readouterr().out

	assert 'kept: require_any matched "software"' in out
	assert 'role "software"' in out
	assert 'age gate not applied' in out


def test_a_dropped_title_names_the_turnaway_gate(ws, capsys):
	"""The plan's own example: React Native passes both positive gates
	and is still somebody else's job."""
	ws.config.write_text('[filter]\npreset = "web-engineer"\n')

	assert run_filter(ws, 'Senior React Native Engineer') == 1
	out = capsys.readouterr().out

	assert 'dropped: exclude_any matched "react native"' in out
	assert 'age gate not applied' in out


def test_words_are_joined_so_quoting_is_optional(ws, capsys):
	"""nargs='+' is for agents: the same title with or without the shell's
	quotes asks the same question."""
	ws.config.write_text('[filter]\npreset = "web-engineer"\n')

	assert run_filter(ws, *['Senior', 'React', 'Native', 'Engineer']) == 1
	assert 'dropped: exclude_any matched "react native"' in capsys.readouterr().out

	assert run_filter(ws, 'Senior React Native Engineer') == 1
	assert 'dropped: exclude_any matched "react native"' in capsys.readouterr().out


def test_a_failed_positive_gate_says_it_matched_nothing(ws, capsys):
	ws.config.write_text('[filter]\npreset = "web-engineer"\n')

	assert run_filter(ws, 'Account Executive') == 1
	assert 'dropped: require_any matched nothing' in capsys.readouterr().out


def test_a_failed_include_gate_says_which_one(ws, capsys):
	ws.config.write_text('[filter]\nrequire_any = ["react"]\ninclude_any = ["engineers?"]\n')

	assert run_filter(ws, 'Senior React Designer') == 1
	assert 'dropped: include_any matched nothing' in capsys.readouterr().out


def test_include_only_kept_names_the_include_gate(ws, capsys):
	ws.config.write_text('[filter]\ninclude_any = ["engineers?"]\n')

	assert run_filter(ws, 'Software Engineer') == 0
	out = capsys.readouterr().out
	assert 'kept: include_any matched "engineer"' in out
	assert 'role "engineer"' in out


def test_an_empty_filter_keeps_every_title(ws, capsys):
	"""The stranger's default: `[filter]` empty is no title filter, and
	the exit status still answers the question."""
	assert run_filter(ws, 'Account Executive') == 0
	out = capsys.readouterr().out
	assert 'kept: no title filter' in out
	assert 'age gate not applied' in out


def test_preset_any_turns_the_file_preset_off(ws, capsys):
	"""`--preset` swaps the preset layer without editing the file: the
	same title the file drops is kept under `any`."""
	ws.config.write_text('[filter]\npreset = "web-engineer"\n')

	assert run_filter(ws, 'Account Executive') == 1
	capsys.readouterr()

	assert run_filter(ws, 'Account Executive', '--preset', 'any') == 0
	assert 'kept: no title filter' in capsys.readouterr().out


def test_an_unknown_preset_is_a_clean_config_error(ws, capsys):
	assert run_filter(ws, 'Software Engineer', '--preset', 'nope') == 1
	err = capsys.readouterr().err
	assert err.startswith('error:')
	assert 'unknown preset' in err


def test_only_exclude_configured_keeps_with_a_named_gate(ws, capsys):
	ws.config.write_text('[filter]\nexclude_any = ["manager"]\n')

	assert run_filter(ws, 'Software Engineer') == 0
	assert 'kept: exclude_any matched nothing' in capsys.readouterr().out

	assert run_filter(ws, 'Engineering Manager') == 1
	assert 'dropped: exclude_any matched "manager"' in capsys.readouterr().out


def test_exclude_companies_is_reported_as_not_checked(ws, capsys):
	"""The title alone cannot answer the company gate; the answer says so
	rather than pretending "kept" means "saved"."""
	ws.config.write_text('[filter]\nexclude_companies = ["Acme"]\n')

	assert run_filter(ws, 'Software Engineer') == 0
	out = capsys.readouterr().out
	assert 'kept: no title filter' in out
	assert 'exclude_companies is checked against the company, not the title' in out
