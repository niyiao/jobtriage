"""Characterisation tests for the relevance filter: the title battery,
the age gate, and the fold-tolerant dedup — the behaviour plans 01–04
must not change.

The word lists now come from the shipped `web-engineer` preset (plan
03 moved them out of this module); the kept and turned-away titles are
the real ones those lists were built on. Everything here passes the
config shape in, so the tests pin the new seam as well as the old
answers.
"""

import tomllib
from datetime import UTC, datetime, timedelta

from jobtriage import workspace
from jobtriage.config import FilterSettings
from jobtriage.relevance import is_fresh, job_key, matching_role, select

# Real titles the filter kept (role NOT NULL in the cache).
KEPT = (
	'Software Engineer, AI Integrations - Remote',
	'Senior FullStack (Go/TypeScript) Engineer',
	'Senior Frontend Developer (React/Next.js)',
	'Senior Full-stack developer (.Net & Angular)',
	'Senior Full Stack Engineer',
	'Senior Full-Stack Software Engineer',
	'Software Engineer - PlanetScale Postgres',
	'Lead Engineer – AI Platform',
	'Staff AI Product Engineer | REMOTE',
	'Full Stack Developer',
	'Senior Software Engineer (FE Focus)',
	'Senior AI & Full-Stack Engineer - DevOps & Technical Leadership',
	'Feltsense AI Engineer',
	'C++/Rust Graphics and Windowing System Software Engineer - Mir',
	'Python Software Engineer - Commercial Systems',
	'Software Engineer II, AI',
	'Senior Full Stack Engineer, AI-First (Go / React)',
)

# Titles that pass or fail the gates while being somebody else's job.
TURNED_AWAY = (
	'Sales Engineer',  # the sales-engineering family
	'Senior Sales Engineer (SaaS)',
	'Solutions Engineer',
	'Support Engineer',
	'Implementation Engineer',
	'Product Manager, Developer Tools',  # role word + builder word, still a PM job
	'Engineering Manager',
	'Developer Relations Advocate',  # DevRel
	'Community Relations Manager',
	'Security Engineer',
	'Network Engineer',
	'Senior Android Developer',  # the mobile family
	'React Native Engineer',
	'iOS Developer',
	'Flutter Developer (Mobile)',
	'Federal Software Engineer',
	'Senior Technical Interviewer',
	'Account Executive',  # no role word at all
	'Project Manager',
)


def web_engineer() -> FilterSettings:
	"""The shipped preset as FilterSettings, the way config.load builds it."""
	path = workspace.presets_dir() / 'web-engineer.toml'
	data = tomllib.loads(path.read_text(encoding='utf-8'))
	return FilterSettings(**{key: tuple(values) for key, values in data.items()})


WEB = web_engineer()

# Today's cutoff, now carried by config.SearchSettings.max_age_days.
CUTOFF_DAYS = 21


def test_matching_role_keeps_real_titles():
	for title in KEPT:
		assert matching_role(title, WEB) is not None, title


def test_matching_role_turns_away_the_documented_families():
	for title in TURNED_AWAY:
		assert matching_role(title, WEB) is None, title


def test_matching_role_returns_the_word_that_matched():
	assert matching_role('Senior AI Product Engineer', WEB) == 'ai'
	assert matching_role('Front-End Developer', WEB) == 'front-end'


def test_a_filter_with_no_lists_keeps_every_title():
	"""[filter] empty is the stranger's default: the category narrows, not
	the title — and the role is '' so the cache still has a value."""
	settings = FilterSettings()
	assert settings.enabled is False
	assert matching_role('Account Executive', settings) == ''
	assert matching_role('Product Manager, Developer Tools', settings) == ''


def test_the_title_filter_comes_from_the_settings_not_a_module_constant():
	settings = FilterSettings(require_any=('rust',), include_any=('engineers?',))
	assert matching_role('Senior Rust Engineer', settings) == 'rust'
	assert matching_role('Senior React Engineer', settings) is None


def test_an_empty_gate_is_disabled():
	"""Only include_any set: any title with a builder word passes, and the
	role comes from the include word because there is no require gate."""
	settings = FilterSettings(include_any=('developers?',))
	assert matching_role('Backend Developer', settings) == 'developer'
	assert matching_role('Account Executive', settings) is None


def test_is_fresh_cutoff():
	now = datetime(2026, 11, 17, tzinfo=UTC)
	assert is_fresh(None, now, max_age_days=CUTOFF_DAYS)  # undated: assumed new
	assert is_fresh('2026-11-17', now, max_age_days=CUTOFF_DAYS)
	assert is_fresh(
		(now - timedelta(days=21)).isoformat(), now, max_age_days=CUTOFF_DAYS
	)  # exactly 21: kept
	assert not is_fresh(
		(now - timedelta(days=22)).isoformat(), now, max_age_days=CUTOFF_DAYS
	)  # 22: dropped


def test_is_fresh_uses_the_configured_cutoff():
	now = datetime(2026, 11, 17, tzinfo=UTC)
	assert is_fresh((now - timedelta(days=7)).isoformat(), now, max_age_days=7)
	assert not is_fresh((now - timedelta(days=8)).isoformat(), now, max_age_days=7)


def test_is_fresh_mixed_precision():
	now = datetime(2026, 11, 17, tzinfo=UTC)
	assert is_fresh('2026-11-10', now, max_age_days=CUTOFF_DAYS)  # date-only counts as UTC midnight
	assert is_fresh(
		'2026-11-10T00:00:00.000Z', now, max_age_days=CUTOFF_DAYS
	)  # milliseconds with a Z
	assert is_fresh(
		'not-a-date', now, max_age_days=CUTOFF_DAYS
	)  # unparseable reads as undated: assumed new


def test_job_key_folds_punctuation_and_case():
	assert job_key('Front-End Engineer') == job_key('Frontend Engineer')
	assert job_key('Front–End Engineer') == job_key('Frontend Engineer')  # en dash
	assert job_key('ACME Inc.') == job_key('acme inc')


def test_select_dedupes_fold_tolerant_keeping_newest():
	older = make(title='Frontend Engineer', company='Acme', published='2026-11-01')
	newer = make(title='Front-End Engineer', company='ACME', published='2026-11-05')
	assert len(select([older, newer], WEB, CUTOFF_DAYS)) == 1
	assert select([older, newer], WEB, CUTOFF_DAYS)[0]['published'] == '2026-11-05'


def test_select_prefers_dated_over_undated_in_dedup():
	dated = make(title='Frontend Engineer', company='Acme', published='2026-11-05')
	undated = make(title='Frontend Engineer', company='Acme', published=None)
	assert len(select([undated, dated], WEB, CUTOFF_DAYS)) == 1
	assert select([undated, dated], WEB, CUTOFF_DAYS)[0]['published'] == '2026-11-05'


def test_select_sorts_newest_first_undated_last():
	jobs = [
		make(title='Software Engineer A', company='A', published='2026-11-02'),
		make(title='Software Engineer B', company='B', published='2026-11-09'),
		make(title='Software Engineer C', company='C', published=None),
	]
	titles = [job['title'] for job in select(jobs, WEB, CUTOFF_DAYS)]
	assert titles == ['Software Engineer B', 'Software Engineer A', 'Software Engineer C']


def test_select_drops_stale_and_irrelevant_and_attaches_role():
	now = datetime(2026, 11, 17, tzinfo=UTC)
	jobs = [
		make(title='Software Engineer', company='Fresh', published='2026-11-16'),
		make(title='Software Engineer', company='Old', published='2026-10-01'),
		make(title='Sales Engineer', company='Nope', published='2026-11-16'),
	]
	kept = select(jobs, WEB, CUTOFF_DAYS, now=now)
	assert [job['company'] for job in kept] == ['Fresh']
	assert kept[0]['role'] == 'software'


def test_select_with_no_filter_keeps_every_fresh_title():
	now = datetime(2026, 11, 17, tzinfo=UTC)
	jobs = [
		make(title='Account Executive', company='Sales', published='2026-11-16'),
		make(title='Product Manager, Developer Tools', company='PM', published='2026-11-16'),
	]
	kept = select(jobs, FilterSettings(), CUTOFF_DAYS, now=now)
	assert {job['title'] for job in kept} == {
		'Account Executive',
		'Product Manager, Developer Tools',
	}
	assert {job['role'] for job in kept} == {''}


def test_select_honours_the_configured_max_age():
	now = datetime(2026, 11, 17, tzinfo=UTC)
	jobs = [make(title='Software Engineer', company='Acme', published='2026-11-09')]  # 8 days
	assert select(jobs, WEB, 7, now=now) == []
	assert len(select(jobs, WEB, 8, now=now)) == 1


def test_select_drops_excluded_companies_case_and_punctuation_folded():
	settings = FilterSettings(exclude_companies=('Acme',))
	jobs = [
		make(title='Software Engineer', company='ACME, Ltd.', published='2026-11-16'),
		make(title='Software Engineer', company='Beta', published='2026-11-16'),
	]
	assert [job['company'] for job in select(jobs, settings, CUTOFF_DAYS)] == ['Beta']


def make(**overrides):
	"""The local job shape for select(), which needs no url."""
	base = {'title': '', 'company': '', 'url': '', 'published': None}
	base.update(overrides)
	return base
