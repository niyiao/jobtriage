"""Tests for the status table (plan 02, step 2).

The point of status.py is that the TUI, the CLI and the sheet import
all see the same four statuses. These tests pin that contract: the
keys are unique, every key has a way back to its label and its old
sheet spelling, and the import's parser is strict where a guess would
change a job's triage.
"""

import pytest

from jobtriage import status

# The sheet's four cell values, in the plan's table order, with the
# slugs they must become. Written out rather than derived from the
# table: this is the import's contract from the old spreadsheet.
SHEET_MAP = (
	('none', 'new'),
	('skip', 'skipped'),
	('to apply', 'to_apply'),
	('applied', 'applied'),
)


def test_slugs_are_unique():
	assert len({entry.slug for entry in status.STATUSES}) == len(status.STATUSES)


def test_labels_are_unique():
	"""The TUI and by_label address rows by label; two the same would
	make one unreachable."""
	assert len({entry.label for entry in status.STATUSES}) == len(status.STATUSES)


@pytest.mark.parametrize('entry', status.STATUSES, ids=lambda entry: entry.slug)
def test_each_slug_finds_its_own_row(entry):
	assert status.by_slug(entry.slug) is entry


@pytest.mark.parametrize('entry', status.STATUSES, ids=lambda entry: entry.slug)
def test_each_label_finds_its_own_row(entry):
	assert status.by_label(entry.label) is entry
	# ...and the labels carry the slugs they name, in both directions.
	assert status.by_slug(status.by_label(entry.label).slug) is entry


@pytest.mark.parametrize(('cell', 'slug'), SHEET_MAP)
def test_each_sheet_value_round_trips(cell, slug):
	assert status.from_sheet(cell) == slug
	# The table carries the same spelling, so the import can read the
	# mapping off the rows rather than a duplicate literal.
	assert status.by_slug(slug).sheet == cell


@pytest.mark.parametrize(
	('cell', 'slug'),
	[
		('NONE', 'new'),
		('None', 'new'),
		('SKIP', 'skipped'),
		('Skip', 'skipped'),
		('TO APPLY', 'to_apply'),
		('To Apply', 'to_apply'),
		('APPLIED', 'applied'),
		('Applied', 'applied'),
	],
)
def test_from_sheet_ignores_case(cell, slug):
	assert status.from_sheet(cell) == slug


def test_from_sheet_ignores_surrounding_whitespace():
	"""Cells exported from the sheet and pasted by hand pick up spaces;
	a trailing space is not a different status."""
	assert status.from_sheet('  to apply\t') == 'to_apply'
	assert status.from_sheet(' applied\n') == 'applied'


@pytest.mark.parametrize('cell', [None, '', '   ', '\t\n'])
def test_an_empty_sheet_cell_is_new(cell):
	"""The sheet's `none` was the absence of a status: untriaged, which
	is what `new` means."""
	assert status.from_sheet(cell) == status.DEFAULT


def test_an_unknown_sheet_value_raises_and_names_it():
	"""The import catches this per row and lists the offender. A silent
	fallback to `new` would put an applied-to job back in the queue."""
	with pytest.raises(status.UnknownSheetStatus, match='pending'):
		status.from_sheet('pending')

	# It is a ValueError too, so ordinary error handling still works.
	with pytest.raises(ValueError):
		status.from_sheet('widrawn')


def test_unknown_slugs_and_labels_raise_key_error():
	with pytest.raises(KeyError, match='nope'):
		status.by_slug('nope')
	with pytest.raises(KeyError, match='Nope'):
		status.by_label('Nope')


def test_defaults_are_the_first_status_and_the_to_apply_filter():
	assert status.DEFAULT == 'new'
	assert status.by_slug(status.DEFAULT).slug == status.DEFAULT
	assert status.DEFAULT_FILTER == 'to_apply'
	assert status.by_slug(status.DEFAULT_FILTER).default_filter


def test_exactly_one_status_is_the_default_filter():
	assert sum(entry.default_filter for entry in status.STATUSES) == 1


def test_every_status_has_a_colour():
	"""The TUI paints every row it renders; an empty colour would fall
	back to white and lose the distinction the sheet had."""
	assert all(entry.colour for entry in status.STATUSES)
