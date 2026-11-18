"""`jobtriage filter test TITLE` — why would the title filter keep this?

The filter is regex fragments from `[filter]`, merged from a preset, the
file, `JOBTRIAGE_*` and flags. Reading the result off a scrape is a bad
way to debug it: a board round trip answers with the jobs that survived,
never with the gate that removed the one you were looking at. This
command asks the same `TitleFilter` the pipeline uses, on one title, and
prints the decisive gate and the text it matched:

    jobtriage filter test "Senior React Native Engineer"
    dropped: exclude_any matched "react native"

The title is one argument made of `nargs='+'` words joined by spaces, so
`filter test Senior React Native Engineer` and the quoted form are the
same question — agents can pass words without shell-quoting a title that
contains quotes.

Only the title gates are testable here. The age gate needs a publish
date, and `exclude_companies` needs a company; neither is part of a
title, and every answer says so. `--preset NAME` swaps the preset layer
without editing the file (`jobtriage filter test "Staff Designer"
--preset designer`), while the file's own `[filter]` lists still apply on
top of it — the same precedence a real run would use.

Exit status is the verdict, so a script can ask without parsing: 0 kept,
1 dropped (or a config this build cannot read, the convention `scrape`
and `config` already follow). argparse's own misuse stays 2.
"""

import argparse
import sys

from jobtriage import config, relevance


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser(
		'filter',
		help='inspect the title filter: test a title against the config',
	)
	actions = parser.add_subparsers(dest='filter_command', metavar='ACTION', required=True)

	test = actions.add_parser(
		'test',
		help='say why the config would keep or drop a title (exit 0 kept, 1 dropped)',
	)
	test.add_argument(
		'title',
		nargs='+',
		metavar='TITLE',
		help='the job title; words are joined with spaces, so quoting is optional',
	)
	test.add_argument(
		'--preset',
		metavar='NAME',
		help="try a shipped preset without editing the file (the file's own [filter] lists still apply)",
	)
	test.set_defaults(run=run_test)
	return parser


def run_test(args, ws) -> int:
	"""Resolve the filter the next scrape would use, then judge one title."""
	overrides = {} if args.preset is None else {'filter.preset': args.preset}
	try:
		settings = config.load(ws, overrides=overrides).filter
	except config.ConfigError as error:
		print(f'error: {error}', file=sys.stderr)
		return 1

	title = ' '.join(args.title)
	verdict = relevance.TitleFilter(settings).verdict(title)
	_print_verdict(settings, verdict)
	return 0 if verdict.kept else 1


def explain(settings: config.FilterSettings, verdict: relevance.Verdict) -> tuple[str, list[str]]:
	"""The verdict line and the context that keeps it honest.

	The first line is deliberately stable and short — `kept: ...` or
	`dropped: ...` plus the gate and word — because the tune-filters
	skill (plan 05) and the MCP server read it as well as a person
	does. The notes say what this test did *not* check, so nobody
	reads "kept" as "will be in the database": age is a separate gate,
	and `exclude_companies` is about the company, not the title.

	Structured as two return values rather than printed here because
	the MCP `filter_test` tool needs the same words in JSON.
	"""
	if not (settings.require_any or settings.include_any or settings.exclude_any):
		line = 'kept: no title filter'
	elif verdict.kept:
		if verdict.gate is None:
			line = 'kept: exclude_any matched nothing'
		else:
			line = f'kept: {verdict.gate} matched "{verdict.word.casefold()}"'
	elif verdict.word is None:
		line = f'dropped: {verdict.gate} matched nothing'
	else:
		line = f'dropped: {verdict.gate} matched "{verdict.word.casefold()}"'

	notes = ['age gate not applied (this tests the title alone)']
	if verdict.kept and verdict.role:
		notes.insert(0, f'role "{verdict.role}"')
	if settings.exclude_companies:
		notes.append('exclude_companies is checked against the company, not the title')
	return line, notes


def _print_verdict(settings: config.FilterSettings, verdict: relevance.Verdict) -> None:
	"""The human view: `explain`'s line, then its notes indented."""
	line, notes = explain(settings, verdict)
	print(line)
	print('  ' + ' · '.join(notes))
