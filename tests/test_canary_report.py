"""The canary's report script: the decision half, without GitHub.

`scripts/canary_report.py` is a repo script — the workflow runs it from a
checkout, so it is loaded by path, like the skill generator. The tests
cover what it decides: which boards are broken, what one issue body says,
and that a dry run calls no API. The REST calls themselves are the
workflow's business, not the suite's.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / 'scripts' / 'canary_report.py'


def load():
	spec = importlib.util.spec_from_file_location('canary_report', SCRIPT)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def document(**overrides):
	"""A minimal `sources test --all --json` document."""
	base = {'boards': [], 'broken': {}, 'ok': True}
	base.update(overrides)
	return base


def test_a_clean_report_has_no_failures():
	canary = load()
	assert canary.failures(document(boards=[{'name': 'alpha', 'ok': True}])) == []


def test_a_failed_board_carries_its_error():
	canary = load()
	rows = canary.failures(document(boards=[{'name': 'remotive', 'ok': False, 'error': 'HTTP 429'}]))

	assert rows == [('remotive', 'HTTP 429')]


def test_invalid_rows_are_a_failure_with_the_first_problem():
	canary = load()
	rows = canary.failures(
		document(
			boards=[
				{
					'name': 'alpha',
					'ok': False,
					'error': None,
					'dropped': 2,
					'problems': ['job 1: url is not https'],
				}
			]
		)
	)

	assert rows == [('alpha', '2 invalid row(s); first: job 1: url is not https')]


def test_a_broken_plugin_is_named_too():
	canary = load()
	rows = canary.failures(document(broken={'half': 'import failed: SyntaxError'}))

	assert rows == [('half', 'plugin broken: import failed: SyntaxError')]


def test_healthy_boards_are_the_ones_that_pass():
	canary = load()

	assert canary.healthy(
		document(
			boards=[
				{'name': 'alpha', 'ok': True},
				{'name': 'beta', 'ok': False},
			]
		)
	) == ['alpha']


def test_the_issue_body_says_what_broke_and_how_to_see_it():
	canary = load()
	body = canary.issue_body('remotive', 'HTTP 429: rate limited', 'https://run.example/1')

	assert 'The nightly canary could not get jobs from **remotive**.' in body
	assert '    HTTP 429: rate limited' in body
	assert 'Run: https://run.example/1' in body
	assert 'jobtriage sources test remotive' in body
	assert 'closes by itself' in body


def test_a_dry_run_prints_actions_without_an_api(monkeypatch, capsys):
	canary = load()

	def no_network(*args, **kwargs):
		raise AssertionError('the dry run called the API')

	monkeypatch.setattr(canary.urllib.request, 'urlopen', no_network)
	client = canary.Client('niyiao/jobtriage', 'token', dry_run=True)

	code = canary.report(document(boards=[{'name': 'alpha', 'ok': False, 'error': 'down'}]), client)

	assert code == 0
	assert 'create: canary: alpha broken — down' in capsys.readouterr().out


def test_the_cli_refuses_a_document_it_does_not_understand(tmp_path, capsys):
	canary = load()
	path = tmp_path / 'canary.json'
	path.write_text('{"nope": true}', encoding='utf-8')

	assert canary.main([str(path), '--dry-run']) == 1
	assert 'not a `sources test --all --json` document' in capsys.readouterr().err


def test_the_cli_needs_a_token_outside_a_dry_run(tmp_path, capsys, monkeypatch):
	canary = load()
	for name in ('GITHUB_TOKEN', 'GITHUB_REPOSITORY'):
		monkeypatch.delenv(name, raising=False)
	path = tmp_path / 'canary.json'
	path.write_text('{"boards": [], "broken": {}, "ok": true}', encoding='utf-8')

	assert canary.main([str(path)]) == 1
	assert 'GITHUB_REPOSITORY and GITHUB_TOKEN' in capsys.readouterr().err
