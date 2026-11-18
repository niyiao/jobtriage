"""The generated skill references must match the code they describe.

Plan 05 step 2's acceptance is "the generated-reference test passes",
and its reason is drift: an agent reading `cli.md` must see the flags
the parser actually has, and an agent reading `schema.md` must see the
columns the migrations actually build. So the test renders the files
fresh into a temporary directory and compares them byte for byte with
the committed copies — a flag, a column or a comment changed without
regenerating fails here, instead of confusing a skill.

The generator is a repo script, not a package module (users install the
CLI, contributors run the script), so it is loaded from its path.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).parents[1]
GENERATOR = ROOT / 'scripts' / 'gen_skill_refs.py'
SKILLS = ROOT / 'src' / 'jobtriage' / 'assets' / 'skills'


def render_into(folder: Path):
	"""Run the generator's own render-and-write path against `folder`."""
	spec = importlib.util.spec_from_file_location('gen_skill_refs', GENERATOR)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	module.write(folder)
	return module


def test_the_committed_skill_references_are_current(tmp_path):
	module = render_into(tmp_path)
	relative_paths = module.render()

	for relative in relative_paths:
		generated = (tmp_path / relative).read_text(encoding='utf-8')
		committed = (SKILLS / relative).read_text(encoding='utf-8')
		assert generated == committed, (
			f'{relative} is out of date — run `uv run python scripts/gen_skill_refs.py`'
		)


def test_every_generated_file_lands_under_the_skills_root(tmp_path):
	"""The paths in the render map are the reference locations step 3 reads."""
	module = render_into(tmp_path)
	assert set(module.render()) == {
		'jobtriage/references/cli.md',
		'jobtriage-db/references/schema.md',
	}
	for relative in module.render():
		assert (tmp_path / relative).is_file()
