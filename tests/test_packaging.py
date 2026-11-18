"""The wheel must carry the templates, not just the code.

`uv tool install` and `pipx install` unpack a wheel, and `jobtriage
init` reads `assets/` from the installed package through
`importlib.resources`. An asset missing from the wheel installs
cleanly and then cannot create a workspace — the one failure a source
checkout never shows. So this builds the real wheel with `uv build`
and reads the zip listing.

The build is skipped, not failed, when `uv` is unavailable or cannot
build in the environment (an offline CI box with a cold cache). The
test is about what the wheel contains, not about whether this machine
can produce one.
"""

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from jobtriage import __version__

ROOT = Path(__file__).parents[1]
ASSETS = ROOT / 'src' / 'jobtriage' / 'assets'

# The acceptance list names a file from each folder `init` builds the op
# folder out of. The test below also checks every other asset, so this
# tuple is the readable floor, not the whole contract.
EXPECTED = (
	'jobtriage/assets/skills/jobtriage/SKILL.md',
	'jobtriage/assets/presets/web-engineer.toml',
	'jobtriage/assets/scrapers/_example.py',
	'jobtriage/assets/AGENTS.md',
	'jobtriage/assets/jobtriage.toml',
	'jobtriage/assets/profile/DETAILS.md',
)


@pytest.fixture(scope='module')
def wheel(tmp_path_factory):
	"""One real wheel for the module: building is slow, reading is cheap."""
	uv = shutil.which('uv')
	if uv is None:
		pytest.skip('uv is not on PATH, so the wheel cannot be built here')
	folder = tmp_path_factory.mktemp('wheel')
	try:
		done = subprocess.run(
			[uv, 'build', '--wheel', '--out-dir', str(folder)],
			cwd=ROOT,
			check=False,  # a failure is a skip below, not an exception
			capture_output=True,
			text=True,
			timeout=180,
		)
	except (OSError, subprocess.TimeoutExpired) as error:
		pytest.skip(f'uv build could not run: {error}')
	if done.returncode != 0:
		pytest.skip(f'uv build failed here (offline?): {done.stderr.strip()}')
	wheels = sorted(folder.glob('*.whl'))
	if not wheels:
		pytest.skip('uv build produced no wheel')
	return wheels[0]


def names(wheel: Path) -> list[str]:
	with zipfile.ZipFile(wheel) as archive:
		return archive.namelist()


def test_the_wheel_is_the_project_version(wheel):
	"""Version drift between pyproject and __init__ would also ship a
	wheel whose metadata does not match what --version prints."""
	assert f'jobtriage-{__version__}.dist-info/METADATA' in names(wheel)


def test_the_key_assets_are_in_the_wheel(wheel):
	inside = set(names(wheel))
	missing = [path for path in EXPECTED if path not in inside]
	assert not missing, f'assets missing from the wheel: {missing}'


def test_every_shipped_asset_is_in_the_wheel(wheel):
	"""A file the checkout has but the wheel lacks is a package-data bug."""
	inside = set(names(wheel))
	missing = []
	for file in ASSETS.rglob('*'):
		if not file.is_file() or '__pycache__' in file.parts:
			continue
		relative = f'jobtriage/assets/{file.relative_to(ASSETS).as_posix()}'
		if relative not in inside:
			missing.append(relative)
	assert not missing, f'assets missing from the wheel: {sorted(missing)}'


def test_the_wheel_declares_the_command_and_the_licence(wheel):
	"""The two things a wheel has to carry besides code: the console
	script and the licence the metadata names."""
	inside = names(wheel)
	with zipfile.ZipFile(wheel) as archive:
		entry_points = archive.read(f'jobtriage-{__version__}.dist-info/entry_points.txt')
	assert 'jobtriage = jobtriage.cli.main:main' in entry_points.decode()
	assert any(name.endswith('.dist-info/licenses/LICENSE') for name in inside)
