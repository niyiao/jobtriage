"""The MCP server: the CLI's own reads and writes, offered to an agent.

An agent that does not read skills can still drive the tool if its
client speaks the Model Context Protocol. The seven tools are thin
wrappers over the same `store`, `config`, `relevance`, `pipeline` and
`attachments` functions the CLI calls — never a second SQL path and
never a browser or submit tool. `list_jobs` is `list --json`,
`get_job` is `show --json`, `scrape` is `scrape`, and the writes are
`status`, `note` and `attach`; `filter_test` is `filter test`. Keeping
the surface on this side of the store is what stops an agent's idea of
the schema drifting from the database's.

`TOOLS` is the contract and the test seam: each entry carries the JSON
Schema a client reads and a callable that takes `(ws, arguments)` and
returns a JSON-serialisable dict. `build_server` only turns that table
into `mcp.types.Tool` objects and routes `tools/call`, so a test can
exercise every tool without stdin, stdout or a subprocess.

MCP stdio is the transport: stdout is the protocol, so nothing here
prints. The wrapped functions are allowed to write progress and
warnings to stderr, which is where the pipeline already puts them.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from jobtriage import __version__, config, pipeline, relevance, status, store, workspace
from jobtriage.attachments import default_kind, resolve_path, stored_path
from jobtriage.cli.filter import explain as explain_verdict
from jobtriage.cli.scrape import scrape_into

# The one-line instruction block an MCP client shows its agent before
# the tools. It repeats the op folder's ground rules that matter at
# this boundary: these tools are the write path, not SQL.
INSTRUCTIONS = (
	'The job search in one jobtriage op folder. Read with list_jobs and get_job; '
	'write with set_status, add_note and attach; scrape runs the boards. '
	'Do not write to the database with SQL. Never submit an application or create '
	'an account without the user asking in this session. Facts about the user come '
	'only from the folder profile; ask for anything missing.'
)

_STATUS_SLUGS = [entry.slug for entry in status.STATUSES]


@dataclass(frozen=True, slots=True)
class ToolSpec:
	"""One tool's advertised schema and the callable behind it.

	`call` takes the workspace (resolved once by the command, as every
	other module does) and the arguments object the client sent, and
	returns a dict. It is deliberately synchronous: SQLite and the
	scrape pipeline are, and the server's async layer only awaits this
	call once per request.
	"""

	description: str
	schema: dict[str, Any]
	call: Callable[[workspace.Workspace, dict], dict]


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
	"""An object schema with no undocumented properties.

	The MCP client reads this before calling and the handlers re-check
	the values they cannot use; this table is the advertised contract,
	not a validator the server runs.
	"""
	schema: dict[str, Any] = {
		'type': 'object',
		'properties': properties,
		'additionalProperties': False,
	}
	if required:
		schema['required'] = list(required)
	return schema


def _id_property() -> dict[str, Any]:
	return {
		'type': 'integer',
		'description': 'The job id, as `list_jobs` prints it.',
	}


TOOLS: dict[str, ToolSpec] = {
	'list_jobs': ToolSpec(
		description=(
			'List jobs from the tracker, newest published first (the same rows as '
			'`jobtriage list --json`). Filters are independent and optional.'
		),
		schema=_schema(
			{
				'status': {
					'type': 'string',
					'enum': _STATUS_SLUGS,
					'description': 'Only jobs with this status.',
				},
				'source': {
					'type': 'string',
					'description': 'Only jobs from this board (name or alias).',
				},
				'query': {
					'type': 'string',
					'description': 'Only jobs whose title, company, role or URL contains this text.',
				},
				'limit': {
					'type': 'integer',
					'minimum': 1,
					'default': 50,
					'description': 'How many jobs to return (default: 50).',
				},
			},
		),
		call=lambda ws, arguments: _list_jobs(ws, arguments),
	),
	'get_job': ToolSpec(
		description=(
			'One job in full: every database field, its status history and its '
			'attachments (the same object as `jobtriage show ID --json`).'
		),
		schema=_schema({'id': _id_property()}, ('id',)),
		call=lambda ws, arguments: _get_job(ws, arguments),
	),
	'set_status': ToolSpec(
		description=(
			'Move one job to a status, recording the change with actor "agent". '
			'Optionally append a note in the same call. Setting the status it '
			'already has is a no-op and writes no history.'
		),
		schema=_schema(
			{
				'id': _id_property(),
				'status': {
					'type': 'string',
					'enum': _STATUS_SLUGS,
					'description': 'The status slug to set.',
				},
				'note': {
					'type': 'string',
					'description': 'An optional diary entry to append after the change.',
				},
			},
			('id', 'status'),
		),
		call=lambda ws, arguments: _set_status(ws, arguments),
	),
	'add_note': ToolSpec(
		description=(
			'Append a note to one job, keeping the notes already there. '
			'Returns the whole notes column after the append.'
		),
		schema=_schema(
			{
				'id': _id_property(),
				'note': {'type': 'string', 'description': 'The note to append.'},
			},
			('id', 'note'),
		),
		call=lambda ws, arguments: _add_note(ws, arguments),
	),
	'attach': ToolSpec(
		description=(
			'Record a file against one job (a cover letter, a PDF) — the same rows '
			'`jobtriage attach` writes. A relative path resolves from the op folder; '
			'a path under letters/ defaults to kind "cover_letter".'
		),
		schema=_schema(
			{
				'id': _id_property(),
				'path': {
					'type': 'string',
					'description': 'The file; relative paths resolve from the op folder.',
				},
				'kind': {
					'type': 'string',
					'description': 'What it is (default: cover_letter under letters/, else file).',
				},
			},
			('id', 'path'),
		),
		call=lambda ws, arguments: _attach(ws, arguments),
	),
	'scrape': ToolSpec(
		description=(
			'Run the scrape pipeline into the database: every usable board in '
			'parallel, or one named board. Hits the network and writes; returns the '
			"same summary as the CLI plus each board's counts, including skipped "
			'and failed boards.'
		),
		schema=_schema(
			{
				'limit': {
					'type': 'integer',
					'minimum': 1,
					'description': 'How many jobs to request per board (default: search.limit).',
				},
				'source': {
					'type': 'string',
					'default': pipeline.ALL,
					'description': 'A board name or alias, or "all" (default).',
				},
			},
		),
		call=lambda ws, arguments: _scrape(ws, arguments),
	),
	'filter_test': ToolSpec(
		description=(
			'Ask the configured title filter whether it would keep a job title, and '
			'why (the same verdict as `jobtriage filter test`). The age gate and '
			'exclude_companies are not applied to a title.'
		),
		schema=_schema(
			{
				'title': {
					'type': 'string',
					'description': 'The job title to judge.',
				},
				'preset': {
					'type': 'string',
					'description': "Try a shipped preset without editing the file; the file's own [filter] lists still apply.",
				},
			},
			('title',),
		),
		call=lambda ws, arguments: _filter_test(ws, arguments),
	),
}


def dispatch(ws: workspace.Workspace, name: str, arguments: dict) -> dict:
	"""Run one tool by name — the seam every test uses.

	The handlers raise the same exceptions the CLI catches (`KeyError`
	for a missing id or slug, `ValueError` for a bad argument, `OSError`
	for a missing file); the server turns them into error results.
	"""
	spec = TOOLS.get(name)
	if spec is None:
		raise KeyError(f'unknown tool {name!r}; choose from {", ".join(TOOLS)}')
	if not isinstance(arguments, dict):
		raise TypeError('arguments must be an object')
	return spec.call(ws, arguments)


def tool_definitions() -> list[types.Tool]:
	"""The `tools/list` answer, in the order the registry declares.

	The order is the plan's and is stable, so a client that shows the
	tools in list order shows the read path before the writes.
	"""
	return [
		types.Tool(name=name, description=spec.description, inputSchema=spec.schema)
		for name, spec in TOOLS.items()
	]


def build_server(ws: workspace.Workspace) -> Server:
	"""An MCP server bound to one workspace, not yet on a transport.

	Keeping the server and the transport apart is what lets a test
	construct the real object and inspect its tool list without
	starting a subprocess.
	"""
	return Server(
		'jobtriage',
		version=__version__,
		instructions=INSTRUCTIONS,
		on_list_tools=_list_tools,
		on_call_tool=partial(_call_tool, ws),
	)


def serve(ws: workspace.Workspace) -> None:
	"""Run the server on stdin/stdout until the client closes the pipe.

	`stdio_server` restores the real descriptors on the way out, so an
	EOF is a clean return to the CLI rather than an exception. Nothing
	else may write to stdout while it runs: that stream is the protocol.
	"""
	asyncio.run(_serve(ws))


async def _serve(ws: workspace.Workspace) -> None:
	server = build_server(ws)
	async with stdio_server() as (read_stream, write_stream):
		await server.run(read_stream, write_stream, server.create_initialization_options())


async def _list_tools(_context, _params) -> types.ListToolsResult:
	"""MCP's `tools/list`: the same table on every request."""
	return types.ListToolsResult(tools=tool_definitions())


async def _call_tool(ws: workspace.Workspace, _context, params) -> types.CallToolResult:
	"""MCP's `tools/call`: JSON text for the agent, plus the same object
	as structured content for clients that read it.

	A known failure (a missing job, a bad slug, an unknown board) is an
	error result with the same one-line sentence the CLI prints. The
	scrape pipeline can also raise `SystemExit` — its "every board
	failed" path predates this server — so that is caught here too
	rather than allowed to end the process.
	"""
	try:
		result = dispatch(ws, params.name, params.arguments or {})
	except (Exception, SystemExit) as error:  # noqa: BLE001 — every failure is the caller's error result
		return types.CallToolResult(
			content=[types.TextContent(type='text', text=f'error: {_message(error)}')],
			isError=True,
		)
	return types.CallToolResult(
		content=[
			types.TextContent(
				type='text',
				text=json.dumps(result, indent=2, ensure_ascii=False),
			)
		],
		structuredContent=result,
	)


def _message(error: BaseException) -> str:
	"""One line for an agent, in the CLI's voice.

	KeyError's str() adds quotes around the sentence a caller wrote;
	`_dispatch` already undoes that for the CLI, so the server does the
	same. A SystemExit from the pipeline carries its text in `code`,
	often with an `error: ` prefix the result will add again.
	"""
	if isinstance(error, KeyError) and error.args:
		text = str(error.args[0])
	elif isinstance(error, SystemExit):
		text = str(error.code) if error.code not in (None, 0) else 'the command exited early'
		text = text.removeprefix('error: ')
	else:
		text = str(error)
	return ' '.join(text.splitlines()) or type(error).__name__


# ---------------------------------------------------------------------------
# The tool bodies. Each one is the CLI's path through the same module.
# ---------------------------------------------------------------------------


def _list_jobs(ws: workspace.Workspace, arguments: dict) -> dict:
	limit = _integer(arguments, 'limit', 50)
	if limit < 1:
		raise ValueError('limit must be at least 1')
	jobs = store.query(
		ws.db,
		status=_text(arguments, 'status'),
		source=_text(arguments, 'source'),
		search=_text(arguments, 'query'),
		limit=limit,
	)
	return {'jobs': jobs, 'count': len(jobs)}


def _get_job(ws: workspace.Workspace, arguments: dict) -> dict:
	job_id = _required_id(arguments)
	job = dict(store.get(ws.db, job_id))
	job['history'] = store.history(ws.db, job_id)
	job['attachments'] = store.attachments(ws.db, job_id)
	return job


def _set_status(ws: workspace.Workspace, arguments: dict) -> dict:
	job_id = _required_id(arguments)
	slug = status.by_slug(_required_text(arguments, 'status')).slug
	old = store.get(ws.db, job_id)['status']
	store.set_status(ws.db, job_id, slug, actor='agent')
	result = {'id': job_id, 'old': old, 'status': slug, 'changed': old != slug}
	note = _text(arguments, 'note')
	if note:
		result['notes'] = store.add_note(ws.db, job_id, note)
	return result


def _add_note(ws: workspace.Workspace, arguments: dict) -> dict:
	job_id = _required_id(arguments)
	notes = store.add_note(ws.db, job_id, _required_text(arguments, 'note'))
	return {'id': job_id, 'notes': notes}


def _attach(ws: workspace.Workspace, arguments: dict) -> dict:
	job_id = _required_id(arguments)
	target = resolve_path(_required_text(arguments, 'path'), ws.root)
	stored = stored_path(target, ws.root)
	kind = _text(arguments, 'kind') or default_kind(target, ws.root)
	created = store.attach(ws.db, job_id, stored, kind)
	return {'id': job_id, 'path': stored, 'kind': kind, 'created': created}


def _scrape(ws: workspace.Workspace, arguments: dict) -> dict:
	limit = _integer(arguments, 'limit')
	source = _text(arguments, 'source') or pipeline.ALL
	report = pipeline.Report()
	try:
		summary = scrape_into(ws, limit=limit, source=source, report=report)
	except SystemExit as stop:
		# The pipeline's old "every board failed" path writes its line to
		# stderr and exits. In a server that would end the session.
		raise ValueError(_message(stop)) from None
	return {
		'source': source,
		'relevant': summary.relevant,
		'new': summary.new,
		'seen': summary.seen,
		'total': summary.total,
		'boards': [_board_row(name, tally) for name, tally in report.boards.items()],
	}


def _board_row(name: str, tally: pipeline.BoardTally) -> dict:
	"""One board's numbers, in the same shape `--explain` counts them."""
	return {
		'source': name,
		'fetched': tally.fetched,
		'age_dropped': tally.age,
		'title_dropped': tally.title,
		'duplicate_dropped': tally.duplicate,
		'kept': tally.kept,
		'skipped': tally.skipped or None,
		'failed': tally.failed or None,
	}


def _filter_test(ws: workspace.Workspace, arguments: dict) -> dict:
	title = _required_text(arguments, 'title')
	preset = _text(arguments, 'preset')
	overrides = {} if preset is None else {'filter.preset': preset}
	settings = config.load(ws, overrides=overrides).filter
	verdict = relevance.TitleFilter(settings).verdict(title)
	line, notes = explain_verdict(settings, verdict)
	return {
		'title': title,
		'kept': verdict.kept,
		'gate': verdict.gate,
		'word': verdict.word,
		'role': verdict.role,
		'verdict': line,
		'notes': notes,
	}


# ---------------------------------------------------------------------------
# Argument handling: the schema is the advert, these are the checks.
# ---------------------------------------------------------------------------


def _required_id(arguments: dict) -> int:
	job_id = _integer(arguments, 'id')
	if job_id is None:
		raise ValueError('id is required')
	return job_id


def _required_text(arguments: dict, key: str) -> str:
	value = _text(arguments, key)
	if value is None:
		raise ValueError(f'{key} is required')
	return value


def _text(arguments: dict, key: str) -> str | None:
	value = arguments.get(key)
	if value is None:
		return None
	if not isinstance(value, str):
		raise TypeError(f'{key} must be a string')
	return value


def _integer(arguments: dict, key: str, default: int | None = None) -> int | None:
	"""An integer from JSON, tolerating the numeric string a client may send.

	`bool` is an int in Python and is not an id; refusing it keeps a
	stray `true` from becoming job 1. A string of digits is accepted
	because some clients serialise every argument as text.
	"""
	value = arguments.get(key)
	if value is None:
		return default
	if isinstance(value, bool):
		raise TypeError(f'{key} must be an integer')
	try:
		return int(value)
	except (TypeError, ValueError):
		raise ValueError(f'{key} must be an integer') from None
