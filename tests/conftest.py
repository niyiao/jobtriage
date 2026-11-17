"""Shared fixtures: a job factory, a throwaway database path, and a
throwaway workspace."""

import pytest

from jobtriage import workspace


def make_job(
	title='Software Engineer',
	company='Acme',
	url='https://example.com/job',
	published='2026-11-10',
	role='software',
	locations=('Worldwide',),
	salary=None,
	source='test',
):
	"""One job dict in the pipeline's house shape."""
	return {
		'title': title,
		'company': company,
		'url': url,
		'published': published,
		'role': role,
		'locations': list(locations),
		'salary': salary,
		'source': source,
	}


@pytest.fixture
def job():
	"""The factory, so a test can vary one field at a time."""
	return make_job


@pytest.fixture
def db_path(tmp_path):
	"""A database path inside a tmp dir; the file itself is created on
	first use, the way store does it."""
	return tmp_path / 'jobs.db'


@pytest.fixture
def ws(tmp_path):
	"""A minimal workspace: a folder with the marker file and nothing
	else. Tests that need the full tree call workspace.create."""
	root = tmp_path / 'op'
	root.mkdir()
	(root / workspace.CONFIG_NAME).write_text('# test workspace\n')
	return workspace.Workspace(root)
