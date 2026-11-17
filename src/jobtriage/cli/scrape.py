"""`jobtriage scrape` — the config-driven run.

The pipeline: relevant jobs are ingested into the database in one
transaction. The database is the single source of truth, so there is no
second step to fail — the whole scrape lands or none of it does — and
the summary reports what relevance kept and what the ingest did.

Plan 03 step 4 makes `jobtriage.toml` the run's defaults: locations,
families, the per-board limit, disabled boards and per-board overrides
all come from `config.load(ws)`. A flag only overrides a config value
the user actually typed; the parser's limit default is None so the
config's `search.limit` (50) is what carries when no number is given.
Every board-level decision is pipeline.py's, so `-s <board>` uses the
same computed tags and overrides the full run would.

The flow itself lives in `scrape_into`, not in `run`, because the TUI's
`R` runs exactly the same one in a background thread (plan 06 step 11).
One implementation means the two can never disagree about what a scrape
means — the same config, the same relevance gate, the same single
transaction — and the CLI's output stays the line it always printed.

`--explain` is the debugging view (plan 03 step 7): the pipeline's
per-board counts and `relevance.select`'s drop reasons, collected in one
`pipeline.Report` as the run happens and printed to stderr. It is off by
default and costs nothing when off; `filter test` is the one-title
version of the same question.

Plan 04 step 6 adds the end-of-run nudge: after the summary, a board
that has now failed `FAILING_STREAK` runs in a row gets one stderr line
pointing at `jobtriage sources test NAME`. The numbers come from
`store.board_health`, the same read `jobtriage sources` prints, and the
run just finished is already recorded, so the streak includes it.
"""

import argparse
import sys
from dataclasses import dataclass

from jobtriage import config, pipeline, registry, relevance, store, workspace
from jobtriage.cli.output import print_jobs


@dataclass(frozen=True, slots=True)
class Summary:
	"""What one scrape kept, added and saw — the one-line summary's
	numbers, gathered where they are known instead of formatted into a
	string no caller but the CLI's stderr could use."""

	relevant: int
	new: int
	seen: int
	total: int

	def line(self) -> str:
		"""The summary exactly as `jobtriage scrape` prints it."""
		return (
			f'{self.relevant} relevant · {self.new} new · '
			f'{self.seen} already seen · {self.total} in the database'
		)


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser('scrape', help='scrape the boards and run the pipeline (default: all)')
	parser.add_argument(
		'-s',
		'--source',
		default=pipeline.ALL,
		metavar='BOARD',
		help='job board to scrape (by name or alias), or "all" to query every board '
		'in parallel (default: all); the boards are the built-ins plus <op>/scrapers/',
	)
	parser.add_argument(
		'limit',
		nargs='?',
		type=int,
		default=None,
		help='how many jobs to scrape per board (default: search.limit in jobtriage.toml, 50)',
	)
	parser.add_argument(
		'--all',
		action='store_true',
		help='include jobs restricted to specific countries (default: the configured locations)',
	)
	parser.add_argument(
		'--raw',
		action='store_true',
		help='list every scraped job unfiltered and save nothing (the pre-filter behaviour)',
	)
	parser.add_argument(
		'--explain',
		action='store_true',
		help='per board, report fetched, dropped by age/title (with the top reasons) and kept',
	)
	parser.set_defaults(run=run)
	return parser


def collect(
	source: str,
	settings: config.Config,
	ws: workspace.Workspace,
	all_locations: bool = False,
	on_board=None,
	report: pipeline.Report | None = None,
	recorder=None,
) -> list[dict]:
	"""Scrape one board, or every usable board in parallel; jobs are tagged.

	The board set is `registry.load(ws, settings)`'s, read here at call
	time rather than captured at import, which is what lets a workspace
	plugin join the run and lets the tests swap the board set for
	fakes. The per-board tags, family parameters and overrides are
	pipeline.py's job in both branches, so `-s` and `all` cannot drift
	apart. `report` is the `--explain` accounting, passed straight
	through; `recorder` is the pipeline's `source_runs` writer, likewise.

	`source` is `all` or a canonical board name or alias; an unknown or
	broken one raises `registry.RegistryError`, and a board that fails
	mid-run raises `pipeline.BoardError` — `run` turns either into one
	clean line.
	"""
	if source == pipeline.ALL:
		return pipeline.scrape_all(
			ws,
			settings,
			all_locations=all_locations,
			on_board=on_board,
			report=report,
			recorder=recorder,
		)
	return pipeline.scrape_board(
		ws,
		settings,
		source,
		all_locations=all_locations,
		report=report,
		recorder=recorder,
	)


def _overrides(limit: int | None) -> dict[str, object]:
	"""The flags that map onto config keys, as `config.load` wants them.

	Only a flag the user typed is here: `limit=None` is the parser's
	"not given", so the config's own `search.limit` wins and no
	personal default survives in this module.
	"""
	return {} if limit is None else {'search.limit': limit}


def scrape_into(
	ws: workspace.Workspace,
	limit: int | None = None,
	all_locations: bool = False,
	source: str = pipeline.ALL,
	on_board=None,
	report: pipeline.Report | None = None,
	recorder=None,
) -> Summary:
	"""The whole pipeline into `ws.db`: load config, scrape, filter, ingest.

	The worker and the CLI both end here; `on_board` is passed straight
	to the pipeline for the TUI's progress line (None: no observer).
	Config is read per run, so an edit to `jobtriage.toml` takes effect
	on the next `R` without restarting the TUI.

	`report` is the optional `--explain` tally. When given, the
	pipeline fills in what each board served, `select` reports every
	drop into the same object, and the survivors are recorded here —
	one pass, one set of numbers. `recorder` is the pipeline's
	`source_runs` writer; None means the workspace database, which is
	what the CLI and the TUI both want.
	"""
	settings = config.load(ws, overrides=_overrides(limit))
	jobs = collect(
		source,
		settings,
		ws,
		all_locations,
		on_board=on_board,
		report=report,
		recorder=recorder,
	)
	matched = relevance.select(
		jobs,
		settings.filter,
		settings.search.max_age_days,
		on_drop=report.drop if report is not None else None,
	)
	if report is not None:
		report.keep(matched)
	result = store.ingest(matched, ws.db)
	return Summary(len(matched), len(result.new), result.seen, result.total)


def background_scrape(
	ws: workspace.Workspace, limit: int | None = None, all_locations: bool = False
):
	"""The callable `R` gets in the TUI: a config-driven scrape of every
	board, returning just how many jobs were new.

	The TUI is injected with this rather than importing the pipeline
	(run(args, ws) is the CLI's shape, not a library's), and a fake in
	its place is how the tests drive `R` with no network.
	"""

	def scrape(on_board=None) -> int:
		summary = scrape_into(ws, limit=limit, all_locations=all_locations, on_board=on_board)
		return summary.new

	return scrape


def run(args, ws) -> int:
	report = pipeline.Report() if args.explain else None
	try:
		if args.raw:
			settings = config.load(ws, overrides=_overrides(args.limit))
			jobs = collect(args.source, settings, ws, args.all, report=report)
			print_jobs(jobs)
			if report is not None:
				report.keep(jobs)  # raw saves nothing, so every fetched job is "kept"
				print_report(report)
			warn_failing_boards(ws)
			return 0

		summary = scrape_into(
			ws,
			limit=args.limit,
			all_locations=args.all,
			source=args.source,
			report=report,
		)
	except registry.UnknownBoard as error:
		# `-s` is dynamic now (workspace plugins join the set), so the
		# parser cannot police it; the registry's line is the usage error.
		print(
			f'error: {error}; see `jobtriage scrape --help` or drop -s to run every board',
			file=sys.stderr,
		)
		return 2
	except (registry.BrokenBoard, pipeline.BoardError) as error:
		print(f'error: {error}', file=sys.stderr)
		return 1
	except config.ConfigError as error:
		# A config this build cannot honour stops the run with the file,
		# the key and the suggestion, not a traceback. Plan 03 step 5's
		# `config validate` is the exhaustive checker; this is the one
		# command that reads the file today.
		print(f'error: {error}', file=sys.stderr)
		return 1

	if report is not None:
		print_report(report)
	print(summary.line(), file=sys.stderr)
	warn_failing_boards(ws)
	return 0


def warn_failing_boards(ws: workspace.Workspace, streak: int = store.FAILING_STREAK) -> None:
	"""One stderr line per board that has failed `streak` runs in a row.

	The plan's end-of-scrape nudge (04 step 6): a board whose layout
	changed fails quietly every morning unless something says so. It
	reads the same `source_runs` rows `jobtriage sources` will print,
	and points at `sources test`, the command that names the broken
	field. The run just finished is already recorded, so a board that
	failed again is counted at its new streak. Like the write side, a
	health read that fails warns and leaves the scrape's result alone.
	"""
	try:
		health = store.board_health(ws.db)
	except Exception as error:  # noqa: BLE001 — a health read must not fail a scrape
		print(f'warning: could not read board health: {error}', file=sys.stderr)
		return
	for source, summary in health.items():
		if summary.failing(streak):
			runs = 'run' if summary.streak == 1 else 'runs'
			print(
				f'warning: {source} has failed its last {summary.streak} {runs}; '
				f'try `jobtriage sources test {source}`',
				file=sys.stderr,
			)


def print_report(report: pipeline.Report) -> None:
	"""Print `--explain`'s block on stderr, where the rest of scrape's
	prose already goes.

	`--raw` prints jobs to stdout, and a filter report mixed into that
	stream would corrupt the one thing a pipe can consume. stderr is
	the same choice the summary and the warnings made, so a script can
	still read the jobs and an agent can read the reasons.
	"""
	for line in report.explain():
		print(line, file=sys.stderr)
