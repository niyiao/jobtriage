"""The README's key map is generated from keys.py, not written by hand.

`keys.readme_section()` renders the shipped table as the markdown block
the README carries between two markers. These tests regenerate it and
compare verbatim, so a new row, a changed default key or a reworded
help line cannot leave the install docs describing a keyboard the TUI
no longer has. If one fails, paste the new `readme_section()` output
over the block in README.md.
"""

from pathlib import Path

from jobtriage.tui import keys

README = Path(__file__).parents[2] / 'README.md'


def test_the_readme_carries_the_generated_key_map_verbatim():
	readme = README.read_text(encoding='utf-8')

	assert keys.readme_section() in readme


def test_the_block_is_fenced_and_lists_every_row():
	block = keys.readme_section()

	assert block.startswith(keys.README_START)
	assert block.endswith(keys.README_END)
	for entry in keys.KEY_TABLE:
		assert entry.help in block, entry.name


def test_a_changed_key_would_fail_the_comparison():
	"""The verbatim comparison is only a drift test if the rendering
	follows the table: rebinding a key has to produce different
	markdown, and that markdown must not already be in the README."""
	rebound = keys.readme_section(keys.resolve({'skipped': 'x'}))

	assert rebound != keys.readme_section()
	assert rebound not in README.read_text(encoding='utf-8')
