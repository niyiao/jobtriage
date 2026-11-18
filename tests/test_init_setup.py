"""Tests for init's setup questions (plan 03 step 6): the plan's
non-interactive defaults, the TTY answers, re-asking on a typo, and the
rule that an existing workspace is never asked about or overwritten.

`_interactive` is the one function that looks at the terminal, so the
tests patch it instead of wrestling pytest's captured stdin; `input` is
patched with a script of answers, which is exactly what a person would
type.
"""

import os
import tomllib

import pytest

from jobtriage import geo, workspace
from jobtriage.cli import init as init_cli
from jobtriage.cli.main import main


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
	"""No real pointer file, no stray config variables."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')
	monkeypatch.delenv('JOBTRIAGE_HOME', raising=False)
	for name in list(os.environ):
		if name.startswith('JOBTRIAGE_') and name != 'JOBTRIAGE_HOME':
			monkeypatch.delenv(name, raising=False)


@pytest.fixture
def op(tmp_path):
	return tmp_path / 'op'


def answers(monkeypatch, *typed):
	"""Make init read exactly these answers, in order."""
	script = iter(typed)
	monkeypatch.setattr('builtins.input', lambda prompt='': next(script))


def document(op):
	return tomllib.loads((op / workspace.CONFIG_NAME).read_text(encoding='utf-8'))


def test_init_non_interactive_writes_the_plan_defaults(op, monkeypatch):
	monkeypatch.setattr(init_cli, '_interactive', lambda: False)

	assert main(['init', str(op)]) == 0

	config = document(op)
	assert config['search']['locations'] == ['worldwide']
	assert config['search']['families'] == ['software']
	assert config['filter']['preset'] == 'any'
	# the plan names three defaults, not employment; it stays commented
	assert 'employment' not in config['search']
	assert '# employment = ["full-time"]' in (op / workspace.CONFIG_NAME).read_text()
	assert 'max_age_days' not in config['search']
	assert '# max_age_days = 21' in (op / workspace.CONFIG_NAME).read_text()


def test_init_yes_asks_nothing_even_at_a_terminal(op, monkeypatch):
	monkeypatch.setattr(init_cli, '_interactive', lambda: True)

	def never(prompt=''):
		raise AssertionError('--yes must not ask anything')

	monkeypatch.setattr('builtins.input', never)

	assert main(['init', str(op), '--yes']) == 0

	config = document(op)
	assert config['search']['locations'] == ['worldwide']
	assert config['search']['families'] == ['software']
	assert config['filter']['preset'] == 'any'
	# the plugin template ships with the op folder (plan 04 step 7)
	assert (op / 'scrapers' / '_example.py').is_file()


def test_init_tty_writes_the_chosen_answers(op, monkeypatch):
	monkeypatch.setattr(init_cli, '_interactive', lambda: True)
	answers(monkeypatch, 'emea, NG', 'design, product', 'data', 'both')

	assert main(['init', str(op)]) == 0

	config = document(op)
	assert config['search']['locations'] == ['emea', 'NG']
	assert config['search']['families'] == ['design', 'product']
	assert config['search']['employment'] == ['full-time', 'contract']
	assert config['filter']['preset'] == 'data'
	# everything not asked stays commented
	assert 'limit' not in config['search']
	assert 'exclude_any' not in config['filter']


def test_init_tty_accepts_a_number_for_the_preset(op, monkeypatch):
	monkeypatch.setattr(init_cli, '_interactive', lambda: True)
	answers(monkeypatch, '', '', '2', '')

	assert main(['init', str(op)]) == 0
	assert document(op)['filter']['preset'] == 'backend-engineer'


def test_init_tty_reasks_after_an_unknown_place(op, monkeypatch, capsys):
	monkeypatch.setattr(init_cli, '_interactive', lambda: True)
	answers(monkeypatch, 'atlantis', 'emea', 'software', 'any', 'full-time')

	assert main(['init', str(op)]) == 0
	out = capsys.readouterr().out

	assert 'unknown location' in out
	assert document(op)['search']['locations'] == ['emea']


def test_init_tty_gives_up_after_three_bad_answers(op, monkeypatch, capsys):
	monkeypatch.setattr(init_cli, '_interactive', lambda: True)
	answers(monkeypatch, 'atlantis', 'atlantis', 'atlantis')

	assert main(['init', str(op)]) == 1
	assert 'no valid answer after 3 tries' in capsys.readouterr().err
	assert not op.exists()  # questions come before anything is created


def test_init_ctrl_d_cancels_cleanly(op, monkeypatch, capsys):
	monkeypatch.setattr(init_cli, '_interactive', lambda: True)

	def eof(prompt=''):
		raise EOFError

	monkeypatch.setattr('builtins.input', eof)

	assert main(['init', str(op)]) == 1
	assert 'cancelled' in capsys.readouterr().err
	assert not op.exists()


def test_init_other_family_asks_for_a_name(op, monkeypatch):
	monkeypatch.setattr(init_cli, '_interactive', lambda: True)
	answers(monkeypatch, '', 'other', 'security', '', '')

	assert main(['init', str(op)]) == 0

	config = document(op)
	assert config['search']['families'] == ['security']
	assert config['search']['locations'] == ['worldwide']
	assert config['search']['employment'] == ['full-time']
	assert config['filter']['preset'] == 'any'


def test_init_never_asks_about_an_existing_workspace(op, monkeypatch):
	op.mkdir()
	(op / workspace.CONFIG_NAME).write_text('[search]\nlimit = 7\n')
	monkeypatch.setattr(init_cli, '_interactive', lambda: True)

	def never(prompt=''):
		raise AssertionError('an existing workspace is not asked about')

	monkeypatch.setattr('builtins.input', never)

	assert main(['init', str(op)]) == 0

	config = document(op)
	assert config['search']['limit'] == 7
	assert 'locations' not in config['search']


def test_every_offered_preset_is_a_shipped_one():
	shipped = {path.stem for path in workspace.presets_dir().glob('*.toml')}
	assert set(init_cli.PRESETS) == shipped


def test_every_offered_place_is_canonical():
	assert init_cli.PLACES[0] == 'worldwide'
	assert len(init_cli.PLACES) == len(geo.REGIONS) + 1  # worldwide + the regions
	assert all(geo.canonical(place) == place for place in init_cli.PLACES)
