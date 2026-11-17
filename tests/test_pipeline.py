"""Characterisation tests for scrape_all: the parallel merge's dedup,
its error isolation, and (plan 03 step 4) the config's board choices,
using fake scraper modules so no test touches a network.

Plan 04 step 4 moved the board set behind `registry.load(ws, config)`,
so these tests install fakes as built-ins through the same two seams
the registry uses (`iter_modules` and `import_module`) and give the
pipeline a workspace. A fake declares the same data every real board
does — `NAME` (the registry's key), plus WORLDWIDE and FAMILIES —
because that is the contract the config is matched against.
"""

import types
from datetime import UTC, datetime

import pytest

from jobtriage import config, pipeline, registry, relevance
from jobtriage.pipeline import scrape_all, scrape_board


def board(name, jobs=None, error=None, *, families=None, covers=None, tags=('Worldwide',)):
	"""A fake plugin honouring the contract: NAME, declarations, and
	recent_jobs(*, locations, limit, **options)."""
	module = types.ModuleType(f'jobtriage.scrapers.{name}')
	module.__name__ = f'jobtriage.scrapers.{name}'
	module.NAME = name
	module.WORLDWIDE = tags
	module.COUNTRY_STYLE = 'none'
	module.FAMILIES = {'software': {}} if families is None else families
	if covers is not None:
		module.COVERS = covers

	def recent_jobs(locations=None, limit=50, **options):
		if error is not None:
			raise error
		return [dict(job) for job in (jobs or [])][:limit]

	module.recent_jobs = recent_jobs
	return module


def spy(name, seen, *, families=None, tags=('Worldwide',)):
	"""A fake board that records exactly what the pipeline passed it."""
	module = types.ModuleType(f'jobtriage.scrapers.{name}')
	module.__name__ = f'jobtriage.scrapers.{name}'
	module.NAME = name
	module.WORLDWIDE = tags
	module.COUNTRY_STYLE = 'none'
	module.FAMILIES = {'software': {}} if families is None else families

	def recent_jobs(**kwargs):
		seen.update(kwargs)
		return []

	module.recent_jobs = recent_jobs
	return module


def settings(**search):
	"""A Config with the given [search] values and code defaults elsewhere."""
	return config.Config(search=config.SearchSettings(**search))


@pytest.fixture(autouse=True)
def only_the_boards_this_test_installs(monkeypatch):
	"""Start from nothing — no real built-ins, no installed packs — so a
	test's board set is exactly what it installs or writes."""
	monkeypatch.setattr(registry, 'iter_modules', lambda paths: [])
	monkeypatch.setattr(registry, 'entry_points', lambda *, group: [])


def install(monkeypatch, *modules):
	"""Make the built-in scan find exactly these fake modules."""
	by_name = {module.NAME: module for module in modules}
	monkeypatch.setattr(
		registry,
		'iter_modules',
		lambda paths: [types.SimpleNamespace(name=name) for name in by_name],
	)
	monkeypatch.setattr(
		registry, 'import_module', lambda fullname: by_name[fullname.rsplit('.', 1)[-1]]
	)


def workspace_plugin(ws, source, stem):
	"""Drop one plugin file in the op folder's scrapers/."""
	folder = ws.scrapers
	folder.mkdir(parents=True, exist_ok=True)
	(folder / f'{stem}.py').write_text(source)


GOOD_PLUGIN = """
NAME = 'extra'
ALIASES = ('ex',)
TITLE = 'Extra Jobs'
HOMEPAGE = 'https://extra.test'


def recent_jobs(*, locations=None, limit=50, **options):
	return [{
		'title': 'Extra Engineer',
		'company': 'Extra Co',
		'url': 'https://extra.test/1',
		'published': '2026-11-03',
		'locations': ['Worldwide'],
		'salary': None,
	}]
"""


def test_merges_and_tags_each_job_with_its_board(job, monkeypatch, ws):
	install(
		monkeypatch,
		board('aa', [job(title='A', company='A', published='2026-11-01')]),
		board('bb', [job(title='B', company='B', published='2026-11-02')]),
	)
	merged = scrape_all(ws, settings())
	assert [j['source'] for j in merged] == ['bb', 'aa']  # newest first


def test_dedupes_by_company_and_title_keeping_newest(job, monkeypatch, ws):
	install(
		monkeypatch,
		board('aa', [job(title='Frontend Engineer', company='Acme', published='2026-11-01')]),
		board('bb', [job(title='frontend engineer', company='ACME', published='2026-11-05')]),
	)
	merged = scrape_all(ws, settings())
	assert len(merged) == 1
	assert merged[0]['published'] == '2026-11-05'


def test_one_flaky_board_is_isolated_with_a_warning(job, monkeypatch, ws, capsys):
	install(
		monkeypatch,
		board('good', [job(title='A', company='A')]),
		board('flaky', error=RuntimeError('rate limited')),
	)
	merged = scrape_all(ws, settings())
	assert [j['title'] for j in merged] == ['A']
	assert 'flaky' in capsys.readouterr().err


def test_every_board_failing_fails_the_run(job, monkeypatch, ws):
	install(
		monkeypatch,
		board('aa', error=RuntimeError('down')),
		board('bb', error=RuntimeError('down')),
	)
	with pytest.raises(SystemExit):
		scrape_all(ws, settings())


def test_limit_and_locations_are_passed_through(job, monkeypatch, ws):
	seen = {}
	install(monkeypatch, spy('spy', seen))
	scrape_all(ws, settings(limit=7))
	assert seen == {'locations': ('Worldwide',), 'limit': 7}
	scrape_all(ws, settings(limit=9), all_locations=True)
	assert seen == {'locations': None, 'limit': 9}


def test_a_disabled_board_is_skipped_with_a_notice(job, monkeypatch, ws, capsys):
	install(
		monkeypatch,
		board('on', [job(title='A', company='A')]),
		board('off', [job(title='B', company='B')]),
	)
	merged = scrape_all(ws, config.Config(sources=config.SourcesSettings(disabled=('off',))))

	assert [j['title'] for j in merged] == ['A']
	err = capsys.readouterr().err
	assert 'skip: off' in err
	assert 'disabled' in err
	assert 'skip: on' not in err


def test_a_disabled_alias_skips_the_canonical_board(job, monkeypatch, ws, capsys):
	"""The disabled list may carry the short name a user types for `-s`;
	the skip decision is still about the board."""
	module = board('off', [job(title='B', company='B')])
	module.ALIASES = ('of',)
	install(monkeypatch, board('on', [job(title='A', company='A')]), module)

	merged = scrape_all(ws, config.Config(sources=config.SourcesSettings(disabled=('of',))))

	assert [j['title'] for j in merged] == ['A']
	assert 'skip: off' in capsys.readouterr().err


def test_an_override_under_an_alias_reaches_the_board(monkeypatch, ws):
	"""`[sources.rm]` is the spelling a user who types `-s rm` reaches
	for; it tunes remotive, not nothing."""
	seen = {}
	module = spy('spy', seen)
	module.ALIASES = ('sy',)
	install(monkeypatch, module)

	scrape_all(
		ws,
		config.Config(sources=config.SourcesSettings(overrides={'sy': {'max_pages': 8}})),
	)

	assert seen == {'max_pages': 8, 'locations': ('Worldwide',), 'limit': 50}


def test_an_alias_override_is_not_reported_as_unknown(monkeypatch, ws, capsys):
	seen = {}
	module = spy('spy', seen)
	module.ALIASES = ('sy',)
	install(monkeypatch, module)

	scrape_all(
		ws,
		config.Config(sources=config.SourcesSettings(overrides={'sy': {'max_pages': 8}})),
	)

	assert 'sources.sy' not in capsys.readouterr().err


def test_the_canonical_override_wins_over_an_alias_table(monkeypatch, ws):
	"""Both spellings present: the deliberate long name is the last word."""
	seen = {}
	module = spy('spy', seen)
	module.ALIASES = ('sy',)
	install(monkeypatch, module)

	scrape_all(
		ws,
		config.Config(
			sources=config.SourcesSettings(overrides={'sy': {'max_pages': 1}, 'spy': {'max_pages': 8}})
		),
	)

	assert seen['max_pages'] == 8


def test_a_board_whose_covers_do_not_overlap_is_skipped(job, monkeypatch, ws, capsys):
	install(
		monkeypatch,
		board('eu', [job(title='A', company='A')], covers=('europe',)),
		board('everywhere', [job(title='B', company='B')]),
	)
	merged = scrape_all(ws, settings(locations=('north-america',)))

	assert [j['title'] for j in merged] == ['B']
	err = capsys.readouterr().err
	assert 'skip: eu' in err
	assert 'covers europe' in err


def test_a_worldwide_user_matches_a_regional_board(job, monkeypatch, ws):
	"""Open to anywhere means open to Europe too: COVERS must not skip
	a board for a worldwide user."""
	install(
		monkeypatch,
		board('eu', [job(title='A', company='A')], covers=('europe',)),
	)
	assert [j['title'] for j in scrape_all(ws, settings(locations=('worldwide',)))] == ['A']


def test_a_country_preference_overlaps_a_region_declaration(job, monkeypatch, ws):
	install(
		monkeypatch,
		board('eu', [job(title='A', company='A')], covers=('europe',)),
	)
	assert [j['title'] for j in scrape_all(ws, settings(locations=('DE',)))] == ['A']


def test_a_board_serving_none_of_the_families_is_skipped(job, monkeypatch, ws, capsys):
	install(
		monkeypatch,
		board('dev', [job(title='A', company='A')]),
		board('design', [job(title='B', company='B')], families={'design': {}}),
	)
	merged = scrape_all(ws, settings(families=('software',)))

	assert [j['title'] for j in merged] == ['A']
	err = capsys.readouterr().err
	assert 'skip: design' in err
	assert 'families' in err


def test_family_parameters_are_passed_as_kwargs(job, monkeypatch, ws):
	seen = {}

	def recent_jobs(category=None, locations=None, limit=50, **options):
		seen.update(category=category, locations=locations, limit=limit)
		return []

	module = board('fam', families={'software': {'category': 'development'}})
	module.recent_jobs = recent_jobs
	install(monkeypatch, module)

	scrape_all(ws, settings(limit=3))

	assert seen == {'category': 'development', 'locations': ('Worldwide',), 'limit': 3}


def test_per_source_overrides_are_passed_as_kwargs(job, monkeypatch, ws):
	seen = {}
	install(monkeypatch, spy('spy', seen))
	scrape_all(
		ws,
		config.Config(
			search=config.SearchSettings(limit=7),
			sources=config.SourcesSettings(overrides={'spy': {'max_pages': 8}}),
		),
	)
	assert seen == {'max_pages': 8, 'locations': ('Worldwide',), 'limit': 7}


def test_a_per_source_override_wins_over_the_family_default(job, monkeypatch, ws):
	seen = {}

	def recent_jobs(category=None, locations=None, limit=50, **options):
		seen.update(category=category)
		return []

	module = board('fam', families={'software': {'category': 'development'}})
	module.recent_jobs = recent_jobs
	install(monkeypatch, module)

	scrape_all(
		ws,
		config.Config(sources=config.SourcesSettings(overrides={'fam': {'category': 'design'}})),
	)

	assert seen['category'] == 'design'


def test_empty_board_tags_mean_no_board_filter(job, monkeypatch, ws):
	"""A board with no tag for anything the user asked for must get None,
	not an empty tuple: the central filter is the only narrowing then."""
	seen = {}
	install(monkeypatch, spy('spy', seen, tags=()))
	scrape_all(ws, settings(locations=('NG',)))
	assert seen['locations'] is None


def test_a_board_with_no_declarations_gets_no_location_filter(job, monkeypatch, ws):
	seen = {}
	module = types.ModuleType('jobtriage.scrapers.legacy')
	module.__name__ = 'jobtriage.scrapers.legacy'
	module.NAME = 'legacy'
	module.recent_jobs = lambda locations=None, limit=50, **options: (
		seen.update(locations=locations) or []
	)
	install(monkeypatch, module)

	scrape_all(ws, settings(locations=('NG',)))

	assert seen['locations'] is None


def test_every_board_skipped_is_a_notice_not_a_failure(monkeypatch, ws, capsys):
	install(
		monkeypatch,
		board('aa', families={'design': {}}),
		board('bb', families={'design': {}}),
	)
	assert scrape_all(ws, settings(families=('software',))) == []
	err = capsys.readouterr().err
	assert 'no boards to scrape' in err


def test_the_progress_callback_fires_once_per_board_including_failures(
	job, monkeypatch, ws, capsys
):
	"""The TUI's `scraping N/M boards…` counts boards landed, not boards
	that worked: a failed board is one of the M, or the line would stall
	one short of the end whenever a board was down. Skipped boards are
	not in M: nothing was attempted for them."""
	install(
		monkeypatch,
		board('aa', [job(title='A', company='A')]),
		board('bb', error=RuntimeError('down')),
		board('cc', [job(title='C', company='C')]),
	)
	seen = []
	scrape_all(
		ws,
		settings(),
		on_board=lambda name, done, total: seen.append((name, done, total)),
	)
	capsys.readouterr()  # the failing board still warns on stderr
	assert [done for _, done, _ in seen] == [1, 2, 3]
	assert [total for _, _, total in seen] == [3, 3, 3]
	assert {name for name, _, _ in seen} == {'aa', 'bb', 'cc'}


def test_no_callback_is_the_old_behaviour(job, monkeypatch, ws):
	"""The default is None, so the CLI and every existing caller keep
	the pipeline they had: no observer, no extra work."""
	install(
		monkeypatch,
		board('aa', [job(title='A', company='A')]),
	)
	assert [j['title'] for j in scrape_all(ws, settings())] == ['A']


def test_an_unknown_sources_table_is_reported(monkeypatch, ws, capsys):
	"""The loader cannot know plugin names; an override naming nothing in
	this run must not vanish silently."""
	install(monkeypatch, board('aa', []))
	scrape_all(ws, config.Config(sources=config.SourcesSettings(overrides={'ghost': {'x': 1}})))
	assert 'sources.ghost' in capsys.readouterr().err


def test_published_none_is_a_legal_date(job, monkeypatch, ws):
	"""The contract allows an undated job; it sorts last and never
	breaks the dedupe's newest-wins comparison."""
	install(
		monkeypatch,
		board(
			'aa',
			[
				job(title='Undated Engineer', company='Acme', published=None),
				job(title='Dated Engineer', company='Acme', published='2026-11-01'),
			],
		),
		board('bb', [job(title='Undated Engineer', company='ACME', published='2026-11-02')]),
	)
	merged = scrape_all(ws, settings())

	assert [j['title'] for j in merged] == ['Undated Engineer', 'Dated Engineer']
	assert merged[0]['published'] == '2026-11-02'  # bb's copy won the merge


def test_an_invalid_row_is_dropped_warned_and_counted_in_fetched(monkeypatch, ws, capsys):
	"""registry.run drops bad rows; the report's fetched is what the board
	returned (the contract's number), while the warning names the first
	problem."""
	jobs = [
		{
			'title': 'Software Engineer',
			'company': 'Acme',
			'url': 'https://alpha.test/1',
			'published': '2026-11-10',
			'locations': ['Worldwide'],
			'salary': None,
		},
		{
			'title': 'Missing Salary',
			'company': 'Acme',
			'url': 'https://alpha.test/2',
			'published': '2026-11-10',
			'locations': ['Worldwide'],
		},
	]
	install(monkeypatch, board('alpha', jobs))
	report = pipeline.Report()

	merged = scrape_all(ws, settings(), report=report)
	err = capsys.readouterr().err

	assert [j['title'] for j in merged] == ['Software Engineer']
	assert report.boards['alpha'].fetched == 2
	assert 'dropped 1 invalid job' in err
	assert 'missing "salary"' in err


# -- registry integration: workspace plugins, aliases, broken boards ------


def test_a_workspace_plugin_runs_alongside_the_builtins(job, monkeypatch, ws):
	"""The plan's acceptance: dropping foo.py in <op>/scrapers/ makes it
	part of an ordinary scrape, with no registration anywhere."""
	install(monkeypatch, board('base', [job(title='Base Engineer', company='Base')]))
	workspace_plugin(ws, GOOD_PLUGIN, 'extra')

	merged = scrape_all(ws, settings())

	assert {job['source'] for job in merged} == {'base', 'extra'}
	assert 'Extra Engineer' in {job['title'] for job in merged}


def test_a_broken_workspace_plugin_is_one_line_and_the_run_goes_on(job, monkeypatch, ws, capsys):
	install(monkeypatch, board('base', [job(title='Base Engineer', company='Base')]))
	workspace_plugin(ws, "NAME = 'broken'\ndef recent_jobs(:\n", 'broken')
	report = pipeline.Report()

	assert [job['title'] for job in scrape_all(ws, settings(), report=report)] == ['Base Engineer']

	err = capsys.readouterr().err
	assert 'broken: broken: import failed' in err
	assert any(line.startswith('explain: broken · skipped: broken:') for line in report.explain())


def test_a_workspace_override_is_noticed_once(job, monkeypatch, ws, capsys):
	install(monkeypatch, board('base', [job(title='Built In', company='Old')]))
	workspace_plugin(
		ws,
		"NAME = 'base'\n\ndef recent_jobs(*, locations=None, limit=50, **options):\n\treturn []\n",
		'base',
	)

	scrape_all(ws, settings())

	err = capsys.readouterr().err
	assert 'using workspace scraper "base" instead of the built-in' in err
	assert err.count('using workspace scraper') == 1


def test_scrape_board_by_alias_tags_the_canonical_name(job, monkeypatch, ws):
	install(monkeypatch, board('base', [job(title='A', company='A')]))
	workspace_plugin(ws, GOOD_PLUGIN, 'extra')

	jobs = scrape_board(ws, settings(), 'ex')

	assert [job['title'] for job in jobs] == ['Extra Engineer']
	assert jobs[0]['source'] == 'extra'  # the alias resolved to NAME


def test_scrape_board_reports_unknown_and_broken_names(monkeypatch, ws):
	install(monkeypatch, board('base', []))
	with pytest.raises(registry.UnknownBoard, match='ghost'):
		scrape_board(ws, settings(), 'ghost')

	workspace_plugin(ws, "NAME = 'inert'\n", 'inert')
	with pytest.raises(registry.BrokenBoard, match='missing recent_jobs'):
		scrape_board(ws, settings(), 'inert')


def test_scrape_board_raises_one_line_when_the_board_fails(monkeypatch, ws):
	install(monkeypatch, board('flaky', error=RuntimeError('rate limited')))

	with pytest.raises(pipeline.BoardError, match='flaky: RuntimeError: rate limited'):
		scrape_board(ws, settings(), 'flaky')


# -- Report: the per-board accounting `scrape --explain` prints ---------


def test_a_report_partitions_every_boards_jobs(job, monkeypatch, ws):
	"""fetched = age + title + duplicate + kept, per board, from the one
	pass that produced the jobs: a merge duplicate is counted where it
	was dropped, and each filter gate names itself."""
	now = datetime.now(UTC)
	install(
		monkeypatch,
		board(
			'aa',
			[
				job(title='Software Engineer', company='Acme', published=now.isoformat()),
				job(title='Sales Engineer', company='Sales', published=now.isoformat()),
				job(title='Software Engineer', company='Old', published='2020-01-01'),
			],
		),
		# the same posting, older: the in-run merge keeps aa's copy
		board(
			'bb',
			[job(title='Software Engineer', company='Acme', published='2020-06-01')],
		),
	)
	settings = config.Config(filter=config.FilterSettings(exclude_any=('sales engineer',)))
	report = pipeline.Report()

	pool = scrape_all(ws, settings, report=report)
	kept = relevance.select(
		pool,
		settings.filter,
		settings.search.max_age_days,
		on_drop=report.drop,
	)
	report.keep(kept)

	aa_tally = report.boards['aa']
	assert (aa_tally.fetched, aa_tally.age, aa_tally.title) == (3, 1, 1)
	assert (aa_tally.duplicate, aa_tally.kept) == (0, 1)
	assert aa_tally.reasons == {('exclude_any', 'sales engineer'): 1}

	# bb's whole contribution lost the merge, so its arithmetic is one
	# duplicate and nothing else
	bb_tally = report.boards['bb']
	assert (bb_tally.fetched, bb_tally.duplicate, bb_tally.kept) == (1, 1, 0)

	lines = report.explain()
	assert 'explain: aa · fetched 3 · age 1 · title 1 · duplicate 0 · kept 1' in lines
	assert '  exclude_any matched "sales engineer" ×1' in lines
	assert 'explain: bb · fetched 1 · age 0 · title 0 · duplicate 1 · kept 0' in lines


def test_a_report_caps_the_reasons_at_the_three_most_common(job, monkeypatch, ws):
	"""The explain block is the pattern, not the whole list."""
	jobs = [
		job(title='Sales Engineer 1', company='A'),
		job(title='Sales Engineer 2', company='B'),
		job(title='Sales Engineer 3', company='C'),
		job(title='Android Developer', company='D'),
		job(title='iOS Developer', company='E'),
		job(title='Security Engineer', company='F'),
	]
	install(monkeypatch, board('aa', jobs))
	settings = config.Config(
		filter=config.FilterSettings(
			require_any=('engineers?', 'developers?'),
			exclude_any=('sales engineer', 'android', 'ios', 'security'),
		)
	)
	report = pipeline.Report()
	pool = scrape_all(ws, settings, report=report)
	relevance.select(pool, settings.filter, 21, on_drop=report.drop)
	report.keep([])

	reason_lines = [line for line in report.explain() if line.startswith('  ')]
	assert len(reason_lines) == 3
	assert 'exclude_any matched "sales engineer" ×3' == reason_lines[0].strip()


def test_a_report_explains_skipped_and_failed_boards(monkeypatch, ws, capsys):
	install(
		monkeypatch,
		board('on', []),
		board('off', []),
		board('bad', error=RuntimeError('rate limited')),
	)
	report = pipeline.Report()

	scrape_all(ws, config.Config(sources=config.SourcesSettings(disabled=('off',))), report=report)
	capsys.readouterr()  # the normal skip/warning notices

	lines = report.explain()
	assert 'explain: on · fetched 0 · age 0 · title 0 · duplicate 0 · kept 0' in lines
	assert 'explain: off · skipped: disabled in config.sources.disabled' in lines
	assert 'explain: bad · failed: RuntimeError: rate limited' in lines
