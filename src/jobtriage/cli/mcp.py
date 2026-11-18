"""`jobtriage mcp` — the MCP server on stdin/stdout.

An MCP client launches this as its server command and speaks JSON-RPC
over the pipes; there is no port and no prompt. The workspace is the
same one every other command resolves (`--workspace`, `JOBTRIAGE_HOME`
or the folder itself), so the client needs the folder or the flag and
nothing else:

    {"command": ["jobtriage", "mcp"], "env": {"JOBTRIAGE_HOME": "/path/to/op"}}

The SDK is imported inside `run`, not at module import: `jobtriage
--help` and every other command stay free of the MCP package's import
cost, and a broken install is one command's error rather than the
whole CLI failing.
"""

import argparse
import sys


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser(
		'mcp',
		help='run the MCP server over stdio, for agents without skill support',
	)
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	try:
		from jobtriage.mcp import serve
	except ImportError as error:  # the dependency is optional at runtime for every other command
		print(f'error: the MCP server needs the "mcp" package: {error}', file=sys.stderr)
		return 1
	serve(ws)
	return 0
