"""The docs' worked example, exercised only through the CLI (plan 04 step 10).

`docs/examples/demo.py` is written as a plugin author would write it from
docs/scrapers.md — one file, imports only from `jobtriage.plugin`, no
host internals. This suite copies it into a temporary op folder and walks
the acceptance path an author follows: `sources` lists it, `sources info`
describes it, `sources test` runs it, `scrape -s` and a plain `scrape`
include it. Nothing touches a network: `http.get_json` is replaced, so
the file's fake host is never resolved.
"""

import datetime
import os
import re
import shutil
from pathlib import Path

import pytest

from jobtriage import registry, store, workspace
from jobtriage.cli.main import main
from jobtriage.plugin import http

DEMO = Path(__file__).parents[1] / 'docs' / 'examples' / 'demo.py'


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
	"""No real workspace pointer, no installed boards, no stray JOBTRIAGE_*."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'no-pointer')
	monkeypatch.delenv('JOBTRIAGE_HOME', raising=False)
	for name in list(os.environ):
		if name.startswith('JOBTRIAGE_') and name != 'JOBTRIAGE_HOME':
			monkeypatch.delenv(name, raising=False)
	# The real fifteen would make this test hit the board APIs; the
	# example alone is the whole set under test.
	monkeypatch.setattr(registry, 'iter_modules', lambda paths: [])
	monkeypatch.setattr(registry, 'entry_points', lambda *, group: [])


def call(ws, *args):
	"""Run the CLI against this workspace."""
	return main(['--workspace', str(ws.root), *args])


def install_demo(ws):
	"""Put the committed example where a plugin author would put it."""
	ws.scrapers.mkdir(parents=True, exist_ok=True)
	target = ws.scrapers / 'demo.py'
	shutil.copyfile(DEMO, target)
	return target


def payload(count=6):
	"""The fake API's answer: `count` contract-shaped records."""
	now = datetime.datetime.now(datetime.UTC).isoformat()
	return {
		'jobs': [
			{
				'id': index,
				'title': f'Platform Engineer {index}',
				'company': 'Demo Co',
				'url': f'https://demo-jobs.test/jobs/{index}',
				'published_at': now,
				'locations': ['Worldwide'],
				'salary': '$120k - $150k',
			}
			for index in range(1, count + 1)
		]
	}


def serve(monkeypatch, data):
	"""Answer the example's one request, recording what it asked for."""
	calls = []

	def fake_get_json(url, *, params=None, headers=None, ua=None, delay=None):
		calls.append((url, params))
		return data

	monkeypatch.setattr(http, 'get_json', fake_get_json)
	return calls


def test_the_example_lists_inspects_and_tests_cleanly(ws, monkeypatch, capsys):
	"""The three commands a plugin author uses first, on the real file."""
	install_demo(ws)
	calls = serve(monkeypatch, payload())

	assert call(ws, 'sources') == 0
	out = capsys.readouterr().out
	assert re.search(r'^demo\s+dm\s+workspace\s+yes\s', out, re.MULTILINE)

	assert call(ws, 'sources', 'info', 'dm') == 0  # aliases resolve
	out = capsys.readouterr().out
	assert out.startswith('demo — Demo Jobs (workspace)')
	assert 'terms: A demonstration board' in out
	assert "families: software → category='engineering'" in out
	assert 'server_locations: yes' in out
	assert '[sources.demo] is where you set them' in out

	assert call(ws, 'sources', 'test', 'demo') == 0
	out = capsys.readouterr().out
	assert re.search(
		r'^demo \(workspace\) · 5 fetched · 5 valid · 0 dropped · \d+\.\d{2}s$',
		out,
		re.MULTILINE,
	)
	assert out.count('Demo Co') == 5
	assert calls[0][0] == 'https://api.demo-jobs.test/v1/jobs'
	assert calls[0][1]['category'] == 'engineering'
	assert not ws.db.exists()  # a test is not a scrape


def test_scrape_s_runs_the_example_as_a_board(ws, monkeypatch, capsys):
	"""`-s demo` is the acceptance criterion: a dropped-in file is a board."""
	install_demo(ws)
	serve(monkeypatch, payload())

	assert call(ws, 'scrape', '-s', 'demo') == 0
	err = capsys.readouterr().err
	assert '6 relevant · 6 new · 0 already seen · 6 in the database' in err
	assert sum(store.counts(ws.db).values()) == 6
	assert {row['source'] for row in store.query(ws.db)} == {'demo'}


def test_a_plain_scrape_includes_the_example(ws, monkeypatch, capsys):
	"""No registration and no -s: a workspace plugin is an ordinary board."""
	install_demo(ws)
	serve(monkeypatch, payload(2))

	assert call(ws, 'scrape') == 0
	assert '2 relevant · 2 new · 0 already seen · 2 in the database' in capsys.readouterr().err
	assert sum(store.counts(ws.db).values()) == 2


def test_a_broken_sibling_and_a_bad_row_never_stop_a_run_or_reach_the_db(ws, monkeypatch, capsys):
	"""The other half of the acceptance: a syntax error is one line, an
	invalid row is dropped before the transaction, and the good jobs land."""
	install_demo(ws)
	(ws.scrapers / 'broken.py').write_text("NAME = 'broken'\ndef recent_jobs(:\n")
	data = payload(2)
	data['jobs'][1]['url'] = ''
	serve(monkeypatch, data)

	assert call(ws, 'scrape') == 0
	err = capsys.readouterr().err
	assert 'broken: broken: import failed' in err
	assert 'dropped 1 invalid job(s)' in err
	assert 'not an absolute http(s) URL' in err

	rows = store.query(ws.db)
	assert [row['url'] for row in rows] == ['https://demo-jobs.test/jobs/1']
	assert sum(store.counts(ws.db).values()) == 1
