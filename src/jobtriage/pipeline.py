"""The pipeline's merge step: every board in parallel, one pool.

Moved here from the package __init__ when the CLI grew subcommands
(plan 01); the behaviour is the one the characterisation tests
pinned: dedup by (company, title) keeping the newest, sorted
newest first, a board that raises skipped with a warning on stderr.

Plan 03 step 4 makes the user's config the only source of a run's
shape: which boards (the disabled list), where on each board
(`geo.board_tags`), which family's category parameters (`FAMILIES`),
and any per-board tuning (`[sources.<board>]`). A board the config
cannot use is skipped before the request, with one line on stderr
saying why, instead of paying a network round trip for nothing.

Plan 04 step 4 makes the board set the registry's: `scrape_all` and
`scrape_board` load `registry.load(ws, config)`, so a workspace plugin
runs next to the fifteen built-ins with no change here, and a broken
one is one stderr line instead of a crash. The registry's `run` is the
contract boundary — it validates rows, tags them with the canonical
`NAME` and folds a board's exception into `RunStats.error` — so this
module branches on the stats rather than on a raised future.

Step 6 makes the pipeline the place a run is written down: every
attempted board gets one `source_runs` row, in the workspace database,
whatever the outcome. The registry stays pure (it is also what
`sources test` runs without a scrape), and the writer is a keyword
argument (`recorder=None`) defaulting to `store.record_run` on
`ws.db`, so a test can watch the calls or leave the database alone.
Health is an accessory: a database that refuses the write warns once
and never sinks the scrape.

The location tags still go to the board. That is what keeps `limit`
meaning "newest *matching*": filtering everything centrally would ask
wellfound for 50 jobs of which ~4 are in EMEA. A board that declares
no location vocabulary gets `locations=None` and the central
relevance filter does the narrowing.

The three server-filtered boards (euremotejobs, jobgether,
remotefrontendjobs) consume the same `locations=` tuple in their own
`recent_jobs` and derive their server params from it. Their
`server_locations(prefs)` hooks stay declarative (the parity tests
call them); the pipeline does not call them, so no board is asked
twice what it already computes once.

`Report` is the optional accounting half (plan 03 step 7): a caller
that wants to know *why* a board contributed fewer jobs passes one in,
the pipeline records what each board served and which boards were
skipped or failed, `relevance.select` calls `report.drop` for every
job a gate removed, and the caller records the survivors with
`report.keep`. The counters were already being computed for the
summary and the progress line; this is the same run, written down.
"""

import logging
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial

from jobtriage import geo, registry, store

# Meta-source: every board the registry found, queried in parallel.
ALL = 'all'

# The run's warnings: one `log.warning` becomes the same one-line
# `warning: ...` on stderr the CLI has always printed (jobtriage.logs
# owns the handler) and a timestamped copy in the workspace log, so a
# board that broke last Tuesday is still findable today.
log = logging.getLogger('jobtriage.pipeline')


class BoardError(Exception):
	"""An explicitly requested board ran and failed.

	`scrape_all` turns a failing board into a warning because the other
	boards still contributed; `scrape -s NAME` has no other board to
	fall back on, so it raises this and the CLI prints one clean line.
	"""


def _load(ws, config) -> registry.Registry:
	"""The registry for this run, with its notices and broken lines once.

	`load` deliberately does not print — a library that writes to stderr
	is hard to test — so every caller that is about to run boards goes
	through this one place and the messages appear exactly once per
	command.
	"""
	reg = registry.load(ws, config)
	for notice in reg.notices:
		print(f'scraper: {notice}', file=sys.stderr)
	for name, reason in reg.broken.items():
		print(f'broken: {name}: {reason}', file=sys.stderr)
	return reg


@dataclass
class BoardTally:
	"""One board's numbers in a `Report`.

	`fetched` is what the board returned, before validation and any
	filtering of the pool; the four drop counters and `kept` partition
	it exactly for contract-valid output: fetched = age + title +
	duplicate + kept. Rows `registry.run` rejected are not in any
	counter — the pipeline warns with the first problem — so a board
	emitting bad rows is the one case the arithmetic comes up short,
	which is itself the signal. `title` folds every `[filter]` gate,
	including `exclude_companies` — it is the same table, and the
	reason line names which gate decided. `reasons` maps `(gate, word)`
	to a count, with an empty word for a positive gate that matched
	nothing.
	"""

	fetched: int = 0
	age: int = 0
	title: int = 0
	duplicate: int = 0
	kept: int = 0
	reasons: Counter[tuple[str, str]] = field(default_factory=Counter)
	skipped: str = ''
	failed: str = ''


@dataclass
class Report:
	"""One scrape's per-board accounting, filled in while it runs.

	Only `scrape --explain` asks for one today. The object is passed
	down to `scrape_all` (which records fetched, skipped, failed and
	the merge's duplicates) and across to `relevance.select` (which
	records each gate's drops), so every number comes from the same
	pass that produced the jobs — no second filtering run to drift.
	"""

	boards: dict[str, BoardTally] = field(default_factory=dict)

	def board(self, name: str) -> BoardTally:
		"""The tally for one board, created on first mention."""
		return self.boards.setdefault(name, BoardTally())

	def skip(self, name: str, reason: str) -> None:
		"""A board the config vetoed before any request."""
		self.board(name).skipped = reason

	def fail(self, name: str, error: object) -> None:
		"""A board that raised; `error` is kept as its one-line reason."""
		self.board(name).failed = str(error)

	def fetch(self, name: str, count: int) -> None:
		"""How many jobs the board returned this run."""
		self.board(name).fetched = count

	def keep(self, jobs) -> None:
		"""Record the survivors, by the board that served each one."""
		for job in jobs:
			self.board(job.get('source') or 'unknown').kept += 1

	def drop(self, job: dict, gate: str | None, word: str | None) -> None:
		"""Record one job the filter removed; `select`'s observer."""
		tally = self.board(job.get('source') or 'unknown')
		if gate == 'age':
			tally.age += 1
		elif gate == 'duplicate':
			tally.duplicate += 1
		else:
			tally.title += 1
			tally.reasons[(gate or 'filter', (word or '').casefold())] += 1

	def explain(self) -> list[str]:
		"""The block `scrape --explain` prints: one line per board.

		Skipped and failed boards get a line of their own because the
		absence of a tally is itself the explanation. A working board's
		line carries the same numbers as the tally, followed by its
		three most common title-drop reasons — the pattern, not the
		whole list; `filter test` is where one title gets the detail.
		The order is the order boards were considered, which is the
		registry's, so two runs are diffable.
		"""
		lines = []
		for name, tally in self.boards.items():
			if tally.skipped:
				lines.append(f'explain: {name} · skipped: {tally.skipped}')
				continue
			if tally.failed:
				lines.append(f'explain: {name} · failed: {tally.failed}')
				continue
			lines.append(
				f'explain: {name} · fetched {tally.fetched} · age {tally.age} · '
				f'title {tally.title} · duplicate {tally.duplicate} · kept {tally.kept}'
			)
			for (gate, word), count in _top_reasons(tally.reasons):
				said = f'{gate} matched nothing' if not word else f'{gate} matched "{word}"'
				lines.append(f'  {said} ×{count}')
		return lines


def _top_reasons(reasons: Counter, limit: int = 3):
	"""The `limit` most frequent (gate, word) reasons, ties by name.

	Sorted rather than `most_common` so equal counts have a stable
	order: a run's explain block should be diffable between days.
	"""
	return sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[:limit]


def scrape_all(
	ws, config, all_locations=False, on_board=None, report=None, recorder=None
) -> list[dict]:
	"""Scrape every usable board in parallel and return the newest jobs.

	`ws` is the op folder the registry loads workspace plugins from;
	`config` is `config.load(ws)`'s Config: `search` supplies the
	locations, families and per-board limit; `sources` supplies the
	disabled boards and per-board overrides.

	Each board is first checked against the config. A broken board is
	already named by the load; a disabled board, one whose `COVERS`
	does not overlap the user's locations, and one whose `FAMILIES`
	serves none of the requested families are skipped with one stderr
	line naming the board and the reason. A worldwide user matches
	every board's `COVERS`: open to anywhere means open to Europe too.

	Each surviving board contributes up to `search.limit` jobs through
	its own location filter (none, if `all_locations`), each tagged
	with the board's canonical name ('source') by `registry.run`.
	`all_locations` is `--all`: no board-side filter, so
	country-restricted jobs are included as well. The pool is deduped
	by (company, title) — the same posting rides several boards —
	keeping the newest, then sorted newest first. It is not truncated:
	the relevance filter is the narrowing step now, and it wants
	everything the boards sent.

	A board that fails is skipped with a warning on stderr: one flaky
	board (remotive rate-limits hard) must not sink the whole run.
	`registry.run` never raises for a board's own failure, so the
	stats carry the error here.

	`on_board` is an observer, for the TUI's progress line: it is
	called `on_board(name, done, total)` as each board finishes —
	failures included, because a progress count that skipped them
	would stall short of the total. Skipped boards are not part of
	the total: nothing was attempted for them. It runs on this
	(pool-owning) thread, so a UI caller hops to its own thread, and
	the default None leaves the scrape exactly as it was before there
	was a TUI to tell.

	`report` is the optional `Report` for `scrape --explain`: when it
	is given, every board considered gets an entry (skipped, broken and
	failed ones with the reason), and the merge's losing duplicates
	are counted so the per-board arithmetic stays exact. None leaves
	the run untouched.

	`recorder` receives one keyword call per attempted board with the
	numbers `source_runs` stores; None writes to `ws.db` through
	`store.record_run`. It is called on this (pool-owning) thread as
	each board lands, so an injected fake needs no locking, and a
	recorder that raises warns on stderr rather than failing the run.
	"""
	reg = _load(ws, config)
	record = _recorder_for(ws, recorder)
	_warn_unknown_overrides(config, reg)
	disabled = reg.disabled(config)

	selected = []
	for board in reg.all():
		name = board.name
		if board.broken:
			# The load already printed the reason; the explain block
			# repeats it where the board's numbers would have been.
			if report is not None:
				report.skip(name, f'broken: {board.broken}')
			continue
		reason = _skip_reason(name, board.module, config, disabled)
		if reason is not None:
			print(f'skip: {name}: {reason}', file=sys.stderr)
			if report is not None:
				report.skip(name, reason)
			continue
		if report is not None:
			report.board(name)  # fix the explain block's order before the pool
		selected.append(board)
	# A file that never imported has no Board, only a broken entry; give
	# `--explain` the line the load already printed to stderr.
	if report is not None:
		for name, reason in reg.broken.items():
			if name not in reg.boards:
				report.skip(name, f'broken: {reason}')

	jobs = []
	failed = 0
	done = 0
	total = len(selected)
	if not total:
		log.warning('no boards to scrape: every board was skipped')
		return []

	with ThreadPoolExecutor(max_workers=total) as pool:
		futures = {
			pool.submit(_run_board, reg, board, config, all_locations): board for board in selected
		}
		for future in as_completed(futures):
			board = futures[future]
			name = board.name
			done += 1
			board_jobs, stats = future.result()
			_record(record, name, stats)
			# registry.run turns a board's own exception into stats.error;
			# one broken site still must not lose the scrape.
			if stats.error:
				failed += 1
				log.warning('%s: %s', name, stats.error)
				if report is not None:
					report.fail(name, stats.error)
				if on_board is not None:
					on_board(name, done, total)
				continue
			_say_invalid(name, stats)
			log.debug(
				'%s: fetched %d, dropped %d, %.0fms',
				name,
				stats.fetched,
				stats.dropped_invalid,
				stats.duration * 1000,
			)
			jobs.extend(board_jobs)
			if report is not None:
				report.fetch(name, stats.fetched)
			if on_board is not None:
				on_board(name, done, total)

	if failed == total:
		sys.exit('error: every board failed')

	newest = {}
	for job in jobs:
		key = (job['company'].lower(), job['title'].lower())
		previous = newest.get(key)
		if previous is None:
			newest[key] = job
		elif (job['published'] or '') > (previous['published'] or ''):
			if report is not None:
				report.drop(previous, 'duplicate', None)
			newest[key] = job
		elif report is not None:
			report.drop(job, 'duplicate', None)
	return sorted(newest.values(), key=lambda job: job['published'] or '', reverse=True)


def scrape_board(ws, config, name, all_locations=False, report=None, recorder=None) -> list[dict]:
	"""One board's jobs, for `jobtriage scrape -s <board>`.

	Naming the board is itself the choice: the disabled list, COVERS
	and family matching do not veto an explicit request. The tags,
	family parameters and per-source overrides are still computed from
	the config, so this is the same request scrape_all would have made
	for that board. `report` is `scrape_all`'s, one board's worth, and
	`recorder` its `source_runs` writer.

	`name` is a canonical NAME or an ALIASES entry. An unknown name
	raises `registry.UnknownBoard`; a broken board raises
	`registry.BrokenBoard`; a board that runs and fails raises
	`BoardError` — all three carry one clean line for the CLI. A board
	that ran is recorded, success or failure, before that last raise:
	the failure is exactly what health needs to see.
	"""
	reg = _load(ws, config)
	board = reg.by_alias(name)
	if board is None:
		raise registry.UnknownBoard(f'unknown board {name!r}')
	if board.broken:
		raise registry.BrokenBoard(board.name, board.broken)

	jobs, stats = _run_board(reg, board, config, all_locations)
	_record(_recorder_for(ws, recorder), board.name, stats)
	if stats.error:
		raise BoardError(f'{board.name}: {stats.error}')
	_say_invalid(board.name, stats)
	if report is not None:
		report.fetch(board.name, stats.fetched)
	return jobs


def _recorder_for(ws, recorder):
	"""The health writer this run uses: the caller's, or the workspace db."""
	return recorder if recorder is not None else partial(store.record_run, ws.db)


def _record(recorder, name: str, stats: registry.RunStats) -> None:
	"""Write one board's run for the health surfaces, never raising.

	`RunStats.duration` is seconds around the whole registry wrapper, so
	milliseconds is the conversion the column wants; `started_at` is
	derived from the finish time for the same reason, without threading
	a clock through the registry. Health is an accessory to the scrape:
	a recorder (or database) that fails prints one warning and the run
	carries on with its jobs.
	"""
	try:
		recorder(
			source=name,
			started_at=(datetime.now(UTC) - timedelta(seconds=stats.duration)).isoformat(
				timespec='seconds'
			),
			duration_ms=round(stats.duration * 1000),
			ok=not stats.error,
			fetched=stats.fetched,
			dropped_invalid=stats.dropped_invalid,
			error=stats.error or None,
		)
	except Exception as error:  # noqa: BLE001 — health must not sink a scrape
		log.warning('could not record the run of %s: %s', name, error)


def _run_board(reg, board, config, all_locations) -> tuple[list[dict], registry.RunStats]:
	"""One registry call with the pipeline's computed arguments."""
	return reg.run(board.name, **_board_call(reg, board, config, all_locations))


def _say_invalid(name: str, stats: registry.RunStats) -> None:
	"""Warn about rows the contract rejected, pointing at the first one.

	The rows are already dropped and counted; this line is the only
	place a user learns a board's output drifted. It names one example
	rather than all of them — `sources test` (plan 04 step 8) is the
	exhaustive view.
	"""
	if not stats.dropped_invalid:
		return
	first = stats.problems[0] if stats.problems else ''
	log.warning(
		'%s: dropped %d invalid job(s); first: %s',
		name,
		stats.dropped_invalid,
		first,
	)


def _skip_reason(name, module, config, disabled) -> str | None:
	"""Why this board cannot serve the config, or None to run it.

	The order is the cheapest and clearest first: a disabled board
	needs no vocabulary inspection, and the notice should say "you
	disabled it" rather than "your families don't match".

	`disabled` is `Registry.disabled(config)`, canonicalised once per
	run so `disabled = ["rm"]` skips remotive exactly as the long name
	does.
	"""
	if name in disabled:
		return 'disabled in config.sources.disabled'

	families = config.search.families
	if families and _family_params(module, families) is None:
		return f'serves none of the configured families ({", ".join(families)})'

	covers = _covers(module)
	locations = config.search.locations
	if covers is not None and not _overlaps(covers, locations):
		return (
			f'only covers {", ".join(covers)}, '
			f'which does not overlap {", ".join(locations) or "no locations"}'
		)
	return None


def _board_call(reg, board, config, all_locations) -> dict:
	"""The keyword arguments one board's `recent_jobs` receives.

	Family parameters first (the soft default a board declared for the
	requested family), then the board's `[sources.<board>]` table, then
	the pipeline-owned `limit` and `locations` for anything the
	override did not set. An override therefore wins for the board's
	own knobs; `--all` is absolute, because it is a flag on this run
	and not a stored preference.

	`locations` is None when the config produces no tags for this
	board: either the board declares no vocabulary, or it has no word
	for anything the user asked for. In both cases the central filter
	is the only narrowing there can be.

	The override table is found by the canonical NAME *and* by any
	alias: `[sources.rm]` tunes remotive, because a user who types `-s
	rm` has no reason to learn the long name twice. When both spellings
	are present the canonical table wins — it is the more deliberate
	write — and `_warn_unknown_overrides` treats an alias as known.
	"""
	params = _family_params(board.module, config.search.families) or {}
	params.update(_overrides(reg, config, board))

	call = dict(params)
	call.setdefault('limit', config.search.limit)
	if all_locations:
		call['locations'] = None
	else:
		call.setdefault('locations', geo.board_tags(board.module, config.search) or None)
	return call


def _overrides(reg, config, board) -> dict:
	"""The `[sources.<board>]` tables that name this board, merged.

	Alias keys are folded in first, in file order, then the canonical
	table: when the user wrote both, the deliberate canonical spelling
	wins any keyword they share. An alias for a different board cannot
	leak in — `reg.canonical` maps each key separately.
	"""
	merged: dict = {}
	for name, table in config.sources.overrides.items():
		if name != board.name and reg.canonical(name) == board.name:
			merged.update(table)
	merged.update(config.sources.overrides.get(board.name, {}))
	return merged


def _family_params(module, families) -> dict | None:
	"""The merged `recent_jobs` parameters for the families this board serves.

	None means the board declares `FAMILIES` and none of the user's
	families is in it: the pipeline skips the board. A module that
	declares no `FAMILIES` at all is a board with no category
	parameter (a plugin, an old-style scraper), so it is taken to
	serve whatever was asked and gets no parameters.

	Today every built-in declares only `software`, so this is that
	table. The loop merges in the user's family order, which makes
	adding a `design` or `data` entry purely additive: a board that
	declares both gets the union of their parameters, later families
	winning a key they share.
	"""
	declared = getattr(module, 'FAMILIES', None)
	if not declared:
		return {}
	served = [family for family in families if family in declared]
	if not served:
		return None
	params = {}
	for family in served:
		params.update(declared[family])
	return params


def _covers(module) -> tuple[str, ...] | None:
	"""The board's `COVERS` declaration, or None when it covers everything."""
	declared = getattr(module, 'COVERS', None)
	if not declared:
		return None
	if isinstance(declared, str):
		return (declared,)
	return tuple(declared)


def _overlaps(covers, locations) -> bool:
	"""Does the board serve at least one place the user can work from?

	`worldwide` on either side means yes: a user open to anywhere can
	use a Europe-only board, and a board open to anywhere can serve a
	user who named one region. Both sides expand through `geo.expand`,
	so a country preference overlaps a region declaration and vice
	versa (`DE` against `('europe',)`).
	"""
	if not locations:
		return True
	if geo.WORLDWIDE in locations or geo.WORLDWIDE in covers:
		return True
	return bool(geo.expand(locations) & geo.expand(covers))


def _warn_unknown_overrides(config, reg) -> None:
	"""Say when `[sources.<name>]` names no board in this run.

	The config loader cannot check the name — plugins (plan 04) add
	boards after it runs — so the pipeline gives the notice instead of
	silently ignoring a table the user meant to take effect. A key that
	is an alias counts as known: `_overrides` applies it to the board it
	names, so warning about it would be a lie.
	"""
	for name in config.sources.overrides:
		if reg.by_alias(name) is None:
			log.warning('sources.%s: no such board; its overrides were ignored', name)
