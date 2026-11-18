"""Tests for the MCP server's tool surface (plan 05 step 7).

Every tool is exercised through `dispatch`, the same callable the
server's `tools/call` handler awaits, so no test starts a subprocess or
speaks JSON-RPC on stdin. The schemas are checked directly because they
are the client-facing contract; one test constructs the real server
object, and one drives the handler to prove an expected failure becomes
an error result rather than a traceback.
"""

import asyncio
import os
from datetime import UTC, datetime

import pytest
from mcp import types as mcp_types

from jobtriage import __version__, registry, store, workspace
from jobtriage import mcp as mcp_server
from jobtriage.cli import mcp as mcp_cli
from jobtriage.cli.main import build_parser


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
	"""No pointer file and no stray JOBTRIAGE_* variable: the tools read
	the workspace they are given and the config inside it."""
	monkeypatch.setattr(workspace, 'pointer_path', lambda: tmp_path / 'pointer')
	for name in list(os.environ):
		if name.startswith('JOBTRIAGE_') and name != 'JOBTRIAGE_HOME':
			monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_installed_boards(monkeypatch):
	"""The scrape tool's board set is exactly what a test installs."""
	monkeypatch.setattr(registry, 'iter_modules', lambda paths: [])
	monkeypatch.setattr(registry, 'entry_points', lambda *, group: [])


def seed(db_path, job, *specs):
	"""Put jobs in the database and return their ids, oldest first.

	No spec means one default job: `(job_id,) = seed(db, job)` is the
	common case, and every test keeps its variations visible.
	"""
	jobs = [job(**(spec or {})) for spec in (specs or ({},))]
	return [entry['id'] for entry in store.ingest(jobs, db_path).new]


def test_the_tool_table_advertises_the_seven_tools():
	assert list(mcp_server.TOOLS) == [
		'list_jobs',
		'get_job',
		'set_status',
		'add_note',
		'attach',
		'scrape',
		'filter_test',
	]

	definitions = mcp_server.tool_definitions()

	assert [tool.name for tool in definitions] == list(mcp_server.TOOLS)
	for tool in definitions:
		assert tool.description
		assert tool.input_schema['type'] == 'object'
		assert 'properties' in tool.input_schema

	by_name = {tool.name: tool.input_schema for tool in definitions}
	assert set(by_name['list_jobs']['properties']) == {'status', 'source', 'query', 'limit'}
	assert by_name['get_job']['required'] == ['id']
	assert set(by_name['set_status']['required']) == {'id', 'status'}
	assert by_name['add_note']['required'] == ['id', 'note']
	assert set(by_name['attach']['required']) == {'id', 'path'}
	assert by_name['scrape']['properties']['source']['default'] == 'all'
	assert by_name['filter_test']['required'] == ['title']
	assert by_name['set_status']['properties']['status']['enum'] == [
		'new',
		'skipped',
		'to_apply',
		'applied',
	]


def test_the_parser_registers_the_mcp_command():
	args = build_parser().parse_args(['mcp'])

	assert args.command == 'mcp'
	assert args.run is mcp_cli.run


def test_list_jobs_returns_the_same_rows_as_the_list_command(ws, job):
	ids = seed(
		ws.db,
		job,
		{},
		{'title': 'Platform Engineer', 'company': 'Beta', 'source': 'beta'},
		{'title': 'Product Designer', 'company': 'Acme', 'source': 'gamma'},
	)
	store.set_status(ws.db, ids[0], 'to_apply')

	result = mcp_server.dispatch(ws, 'list_jobs', {'limit': 10})

	assert result['count'] == 3
	assert [entry['id'] for entry in result['jobs']] == sorted(ids, reverse=True)
	assert result['jobs'][0]['title'] == 'Product Designer'

	only = mcp_server.dispatch(ws, 'list_jobs', {'status': 'to_apply'})
	assert [entry['id'] for entry in only['jobs']] == [ids[0]]

	found = mcp_server.dispatch(ws, 'list_jobs', {'query': 'beta'})
	assert [entry['company'] for entry in found['jobs']] == ['Beta']

	board = mcp_server.dispatch(ws, 'list_jobs', {'source': 'gamma'})
	assert [entry['title'] for entry in board['jobs']] == ['Product Designer']

	limited = mcp_server.dispatch(ws, 'list_jobs', {'limit': 1})
	assert limited['count'] == 1


def test_get_job_returns_the_row_its_history_and_attachments(ws, job):
	(job_id,) = seed(ws.db, job)
	letter = ws.root / 'letters' / 'acme-engineer.md'
	letter.parent.mkdir()
	letter.write_text('Dear Acme\n')
	store.attach(ws.db, job_id, 'letters/acme-engineer.md')
	store.set_status(ws.db, job_id, 'to_apply', actor='cli')

	result = mcp_server.dispatch(ws, 'get_job', {'id': job_id})

	assert result['id'] == job_id
	assert result['status'] == 'to_apply'
	assert result['history'][0]['new'] == 'to_apply'
	assert result['attachments'][0]['path'] == 'letters/acme-engineer.md'

	with pytest.raises(KeyError, match='no job with id 99'):
		mcp_server.dispatch(ws, 'get_job', {'id': 99})


def test_set_status_records_the_agent_actor_and_an_optional_note(ws, job):
	(job_id,) = seed(ws.db, job)

	result = mcp_server.dispatch(
		ws,
		'set_status',
		{'id': job_id, 'status': 'to_apply', 'note': 'worth a letter'},
	)

	assert result == {
		'id': job_id,
		'old': 'new',
		'status': 'to_apply',
		'changed': True,
		'notes': 'worth a letter',
	}
	history = store.history(ws.db, job_id)
	assert [(entry['old'], entry['new'], entry['actor']) for entry in history] == [
		('new', 'to_apply', 'agent')
	]

	again = mcp_server.dispatch(ws, 'set_status', {'id': job_id, 'status': 'to_apply'})
	assert again['changed'] is False
	assert len(store.history(ws.db, job_id)) == 1


def test_an_unknown_status_names_the_slug(ws, job):
	(job_id,) = seed(ws.db, job)

	with pytest.raises(KeyError, match='to_aply'):
		mcp_server.dispatch(ws, 'set_status', {'id': job_id, 'status': 'to_aply'})


def test_add_note_appends_and_returns_the_whole_column(ws, job):
	(job_id,) = seed(ws.db, job)

	first = mcp_server.dispatch(ws, 'add_note', {'id': job_id, 'note': 'recruiter called'})
	second = mcp_server.dispatch(ws, 'add_note', {'id': job_id, 'note': 'sent the letter'})

	assert first['notes'] == 'recruiter called'
	assert second['notes'] == 'recruiter called\nsent the letter'


def test_attach_resolves_from_the_op_folder_and_refuses_a_missing_file(ws, job):
	(job_id,) = seed(ws.db, job)
	letter = ws.root / 'letters' / 'acme-engineer.md'
	letter.parent.mkdir()
	letter.write_text('Dear Acme\n')

	created = mcp_server.dispatch(
		ws,
		'attach',
		{'id': job_id, 'path': 'letters/acme-engineer.md'},
	)

	assert created == {
		'id': job_id,
		'path': 'letters/acme-engineer.md',
		'kind': 'cover_letter',
		'created': True,
	}

	again = mcp_server.dispatch(
		ws,
		'attach',
		{'id': job_id, 'path': 'letters/acme-engineer.md', 'kind': 'file'},
	)
	assert again['created'] is False
	assert again['kind'] == 'file'

	with pytest.raises(OSError, match='not a file'):
		mcp_server.dispatch(ws, 'attach', {'id': job_id, 'path': 'letters/missing.md'})


def test_scrape_runs_the_pipeline_and_reports_each_board(ws, job):
	today = datetime.now(UTC).date().isoformat()
	ws.scrapers.mkdir()
	(ws.scrapers / 'extra.py').write_text(
		f"""
NAME = 'extra'
ALIASES = ('ex',)
TITLE = 'Extra Jobs'
HOMEPAGE = 'https://extra.test'


def recent_jobs(*, locations=None, limit=50, **options):
	return [{{
		'title': 'Extra Software Engineer',
		'company': 'Extra Co',
		'url': 'https://extra.test/1',
		'published': {today!r},
		'locations': ['Worldwide'],
		'salary': None,
	}}]
"""
	)

	result = mcp_server.dispatch(ws, 'scrape', {})

	assert result['relevant'] == 1
	assert result['new'] == 1
	assert result['total'] == 1
	assert result['boards'] == [
		{
			'source': 'extra',
			'fetched': 1,
			'age_dropped': 0,
			'title_dropped': 0,
			'duplicate_dropped': 0,
			'kept': 1,
			'skipped': None,
			'failed': None,
		}
	]
	assert store.query(ws.db)[0]['title'] == 'Extra Software Engineer'

	alias = mcp_server.dispatch(ws, 'scrape', {'source': 'ex'})
	assert alias['seen'] == 1
	assert alias['boards'][0]['source'] == 'extra'


def test_filter_test_returns_the_cli_verdict_and_reason(ws):
	ws.config.write_text('[filter]\npreset = "web-engineer"\n')

	dropped = mcp_server.dispatch(
		ws,
		'filter_test',
		{'title': 'Senior React Native Engineer'},
	)

	assert dropped['kept'] is False
	assert dropped['gate'] == 'exclude_any'
	assert dropped['word'].casefold() == 'react native'
	assert dropped['verdict'] == 'dropped: exclude_any matched "react native"'
	assert 'age gate not applied' in dropped['notes'][0]

	kept = mcp_server.dispatch(ws, 'filter_test', {'title': 'Senior Software Engineer'})
	assert kept['kept'] is True
	assert kept['verdict'] == 'kept: require_any matched "software"'

	plain = mcp_server.dispatch(
		ws,
		'filter_test',
		{'title': 'Account Executive', 'preset': 'any'},
	)
	assert plain['verdict'] == 'kept: no title filter'


def test_an_unknown_tool_is_named_in_the_error(ws):
	with pytest.raises(KeyError, match='unknown tool'):
		mcp_server.dispatch(ws, 'write_sql', {})


def test_the_server_constructs_with_the_tool_table(ws):
	server = mcp_server.build_server(ws)

	assert server.server_info.name == 'jobtriage'
	assert server.server_info.version == __version__


def test_a_known_failure_becomes_an_mcp_error_result(ws):
	params = mcp_types.CallToolRequestParams(name='get_job', arguments={'id': 99})

	result = asyncio.run(mcp_server._call_tool(ws, None, params))

	assert result.is_error is True
	assert 'no job with id 99' in result.content[0].text


def test_a_successful_call_returns_json_text_and_structured_content(ws, job):
	seed(ws.db, job)
	params = mcp_types.CallToolRequestParams(name='list_jobs', arguments={'limit': 5})

	result = asyncio.run(mcp_server._call_tool(ws, None, params))

	assert result.is_error is False
	assert result.structured_content['count'] == 1
	assert result.structured_content['jobs'][0]['title'] == 'Software Engineer'
	assert '"count": 1' in result.content[0].text
