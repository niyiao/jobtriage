"""Tests for the full config loader: merge order, presets, validation,
and the TUI reader it grew out of.

Every test passes `env={}` so the machine's JOBTRIAGE_* variables
cannot change the answer; the `ws` fixture is a folder with only the
marker file, so a test writes exactly the config it means to exercise.
"""

import tomllib

import pytest

from jobtriage import config, workspace
from jobtriage.config import ConfigError


def preset_lists(name: str) -> dict[str, tuple[str, ...]]:
	"""A shipped preset's own lists, as config.load reads them."""
	path = workspace.presets_dir() / f'{name}.toml'
	data = tomllib.loads(path.read_text(encoding='utf-8'))
	return {key: tuple(values) for key, values in data.items()}


WEB_ENGINEER = preset_lists('web-engineer')


def load(ws, **kwargs):
	"""`config.load` with a hermetic environment by default."""
	kwargs.setdefault('env', {})
	return config.load(ws, **kwargs)


def write(ws, text):
	ws.config.write_text(text)
	return ws


def message(ws, text, **kwargs) -> str:
	"""The ConfigError a piece of config raises, as text."""
	write(ws, text)
	with pytest.raises(ConfigError) as caught:
		load(ws, **kwargs)
	return str(caught.value)


# -- defaults and merge order -----------------------------------------


def test_an_empty_file_is_the_code_defaults(ws):
	settings = load(ws)

	assert settings == config.Config()
	assert settings.search.locations == ('worldwide',)
	assert settings.search.families == ('software',)
	assert settings.search.employment == ('full-time',)
	assert settings.search.max_age_days == 21
	assert settings.search.limit == 50
	assert settings.filter.enabled is False
	assert settings.letters.paragraphs == 2
	assert settings.browser.executable == ''
	assert settings.browser.port == 9222


def test_load_accepts_the_config_path_as_well_as_a_workspace(ws):
	write(ws, '[search]\nlimit = 5\n')

	assert config.load(ws.config, env={}).search.limit == 5


def test_a_missing_file_is_still_the_defaults(tmp_path):
	assert config.load(tmp_path / 'jobtriage.toml', env={}) == config.Config()


def test_the_file_fills_every_section(ws):
	write(
		ws,
		"""
[search]
locations = ["worldwide", "emea", "NG"]
families = ["software", "data"]
employment = ["full-time", "contract"]
max_age_days = 7
limit = 10

[filter]
preset = "web-engineer"
exclude_any = ["wordpress"]

[letters]
paragraphs = 3

[sources]
disabled = ["bruntwork"]

[sources.wellfound]
max_pages = 8

[browser]
executable = "/Applications/Chrome"
port = 9223

[tui]
default_filter = "new"
page_size = 10

[tui.keys]
skipped = "x"
""",
	)

	settings = load(ws)

	assert settings.search.locations == ('worldwide', 'emea', 'NG')
	assert settings.search.families == ('software', 'data')
	assert settings.search.employment == ('full-time', 'contract')
	assert settings.search.max_age_days == 7
	assert settings.search.limit == 10
	assert settings.filter.preset == 'web-engineer'
	assert settings.filter.exclude_any[-1] == 'wordpress'
	assert settings.letters.paragraphs == 3
	assert settings.sources.disabled == ('bruntwork',)
	assert settings.sources.overrides == {'wellfound': {'max_pages': 8}}
	assert settings.browser.executable == '/Applications/Chrome'
	assert settings.browser.port == 9223
	assert settings.tui.default_filter == 'new'
	assert settings.tui.page_size == 10
	assert settings.tui.keys == {'skipped': 'x'}


def test_precedence_is_default_then_file_then_env_then_overrides(ws):
	assert load(ws).search.limit == 50

	write(ws, '[search]\nlimit = 10\n')
	assert load(ws).search.limit == 10

	assert load(ws, env={'JOBTRIAGE_SEARCH_LIMIT': '20'}).search.limit == 20
	assert (
		load(
			ws,
			env={'JOBTRIAGE_SEARCH_LIMIT': '20'},
			overrides={'search.limit': 30},
		).search.limit
		== 30
	)


def test_an_env_list_is_a_toml_array(ws):
	settings = load(ws, env={'JOBTRIAGE_SEARCH_LOCATIONS': '["emea", "NG"]'})

	assert settings.search.locations == ('emea', 'NG')


def test_a_malformed_env_value_names_the_variable(ws):
	with pytest.raises(ConfigError, match='JOBTRIAGE_SEARCH_LOCATIONS'):
		load(ws, env={'JOBTRIAGE_SEARCH_LOCATIONS': 'emea,NG'})


def test_an_env_string_is_taken_verbatim(ws):
	path = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
	settings = load(
		ws,
		env={'JOBTRIAGE_BROWSER_EXECUTABLE': path, 'JOBTRIAGE_BROWSER_PORT': '9333'},
	)

	assert settings.browser.executable == path
	assert settings.browser.port == 9333


def test_overrides_are_dotted_python_values(ws):
	settings = load(
		ws,
		overrides={
			'search.locations': ['north-america'],
			'search.limit': 5,
			'browser.port': 9222,
			'sources.wellfound.max_pages': 3,
		},
	)

	assert settings.search.locations == ('north-america',)
	assert settings.search.limit == 5
	assert settings.sources.overrides == {'wellfound': {'max_pages': 3}}


def test_an_unknown_override_suggests_the_real_key(ws):
	with pytest.raises(ConfigError) as caught:
		load(ws, overrides={'search.location': ['emea']})

	assert 'search.location' in str(caught.value)
	assert 'search.locations' in str(caught.value)


# -- provenance: what `config show` prints -----------------------------


def test_provenance_names_the_layer_for_each_value(ws):
	write(
		ws,
		'[search]\nmax_age_days = 7\n\n'
		'[filter]\npreset = "web-engineer"\nexclude_any = ["wordpress"]\n',
	)

	settings, origins = config.load_with_provenance(
		ws,
		env={'JOBTRIAGE_SEARCH_LOCATIONS': '["emea"]'},
		overrides={'search.limit': 20},
	)

	assert settings.search.limit == 20
	assert origins['search.locations'] == 'env:JOBTRIAGE_SEARCH_LOCATIONS'
	assert origins['search.limit'] == 'flag'
	assert origins['search.max_age_days'] == 'file'
	assert origins['search.families'] == 'default'
	assert origins['filter.preset'] == 'file'
	# the preset's turnaway list survives, extended by the file
	assert origins['filter.exclude_any'] == 'preset:web-engineer+file'
	assert origins['filter.require_any'] == 'preset:web-engineer'
	assert origins['filter.exclude_companies'] == 'default'
	assert origins['browser.port'] == 'default'


def test_provenance_covers_sources_and_tui_keys(ws):
	write(ws, '[sources.wellfound]\nmax_pages = 8\n\n[tui.keys]\nskipped = "x"\n')

	settings, origins = config.load_with_provenance(
		ws,
		env={},
		overrides={'sources.wellfound.max_pages': 3, 'tui.keys.skipped': 'z'},
	)

	assert settings.sources.overrides == {'wellfound': {'max_pages': 3}}
	assert settings.tui.keys == {'skipped': 'z'}
	assert origins['sources.wellfound.max_pages'] == 'flag'
	assert origins['tui.keys.skipped'] == 'flag'
	assert origins['sources.disabled'] == 'default'


def test_load_is_load_with_provenance_without_the_map(ws):
	write(ws, '[search]\nlimit = 4\n')

	assert config.load(ws, env={}) == config.load_with_provenance(ws, env={})[0]


# -- search validation -------------------------------------------------


def test_locations_are_canonicalised(ws):
	write(ws, '[search]\nlocations = ["UK", "United States", "emea"]\n')

	assert load(ws).search.locations == ('GB', 'US', 'emea')


def test_an_unknown_location_names_its_position_and_suggests(ws):
	text = message(ws, '[search]\nlocations = ["worldwide", "emea", "emae"]\n')

	assert str(ws.config) in text
	assert 'search.locations[2]' in text
	assert 'emae' in text
	assert 'emea' in text


def test_an_unknown_key_suggests_the_real_one(ws):
	text = message(ws, '[search]\nmax_age = 7\n')

	assert 'search.max_age' in text
	assert 'max_age_days' in text


def test_an_unknown_section_suggests_the_real_one(ws):
	text = message(ws, '[serch]\nlimit = 1\n')

	assert '[serch]' in text
	assert 'search' in text


@pytest.mark.parametrize(
	'text, expected',
	(
		('[search]\nlimit = "fifty"\n', 'expected an integer'),
		('[search]\nlocations = "emea"\n', 'expected an array'),
		('[sources]\ndisabled = "bruntwork"\n', 'expected an array'),
		('[browser]\nport = true\n', 'expected an integer'),
		('[search]\nlocations = ["worldwide", 3]\n', 'expected a string'),
	),
)
def test_wrong_types_are_refused(ws, text, expected):
	assert expected in message(ws, text)


def test_employment_is_checked_against_the_three_types(ws):
	write(ws, '[search]\nemployment = ["Full Time"]\n')
	assert load(ws).search.employment == ('full-time',)

	text = message(ws, '[search]\nemployment = ["freelance"]\n')
	assert 'search.employment[0]' in text
	assert 'full-time' in text


# -- [filter]: empty, presets, extension and replacement ----------------


def test_an_empty_filter_section_is_no_title_filter(ws):
	assert load(ws).filter.enabled is False

	write(ws, '[filter]\n')
	assert load(ws).filter.enabled is False
	assert load(ws).filter.preset is None


def test_an_exclude_only_filter_is_still_a_filter(ws):
	write(ws, '[filter]\nexclude_any = ["manager"]\n')

	settings = load(ws)

	assert settings.filter.enabled is True
	assert settings.filter.exclude_any == ('manager',)


def test_the_web_engineer_preset_loads_its_lists_verbatim(ws):
	write(ws, '[filter]\npreset = "web-engineer"\n')

	settings = load(ws)

	assert settings.filter.preset == 'web-engineer'
	assert settings.filter.require_any == WEB_ENGINEER['require_any']
	assert settings.filter.include_any == WEB_ENGINEER['include_any']
	assert settings.filter.exclude_any == WEB_ENGINEER['exclude_any']


@pytest.mark.parametrize(
	'name',
	('web-engineer', 'backend-engineer', 'data', 'designer', 'product-manager', 'any'),
)
def test_every_shipped_preset_loads_and_validates(ws, name):
	write(ws, f'[filter]\npreset = "{name}"\n')

	settings = load(ws)

	assert settings.filter.preset == name


def test_the_any_preset_is_empty(ws):
	write(ws, '[filter]\npreset = "any"\n')

	settings = load(ws)

	assert settings.filter.preset == 'any'
	assert settings.filter.require_any == ()
	assert settings.filter.include_any == ()
	assert settings.filter.exclude_any == ()
	assert settings.filter.enabled is False


def test_exclude_any_extends_the_preset(ws):
	write(ws, '[filter]\npreset = "web-engineer"\nexclude_any = ["wordpress"]\n')

	settings = load(ws)

	assert settings.filter.exclude_any == (*WEB_ENGINEER['exclude_any'], 'wordpress')


def test_include_any_replaces_the_preset(ws):
	write(ws, '[filter]\npreset = "web-engineer"\ninclude_any = ["rust"]\n')

	settings = load(ws)

	assert settings.filter.include_any == ('rust',)
	assert settings.filter.require_any == WEB_ENGINEER['require_any']


def test_include_any_extra_extends_the_preset(ws):
	write(ws, '[filter]\npreset = "web-engineer"\ninclude_any_extra = ["rust"]\n')

	settings = load(ws)

	assert settings.filter.include_any == (*WEB_ENGINEER['include_any'], 'rust')


def test_require_any_extra_extends_the_preset(ws):
	write(ws, '[filter]\npreset = "web-engineer"\nrequire_any_extra = ["rust"]\n')

	settings = load(ws)

	assert settings.filter.require_any == (*WEB_ENGINEER['require_any'], 'rust')


def test_env_filter_values_replace_the_file_and_extras_extend(ws):
	write(ws, '[filter]\npreset = "web-engineer"\ninclude_any = ["rust"]\n')

	replaced = load(ws, env={'JOBTRIAGE_FILTER_INCLUDE_ANY': '["go"]'})
	assert replaced.filter.include_any == ('go',)
	assert replaced.filter.require_any == WEB_ENGINEER['require_any']

	extended = load(ws, env={'JOBTRIAGE_FILTER_INCLUDE_ANY_EXTRA': '["go"]'})
	assert extended.filter.include_any == ('rust', 'go')


def test_an_override_beats_the_environment(ws):
	settings = load(
		ws,
		env={'JOBTRIAGE_FILTER_INCLUDE_ANY': '["go"]'},
		overrides={'filter.include_any': ['python']},
	)

	assert settings.filter.include_any == ('python',)


def test_an_unknown_preset_suggests_the_shipped_one(ws):
	text = message(ws, '[filter]\npreset = "web"\n')

	assert str(ws.config) in text
	assert 'filter.preset' in text
	assert 'web-engineer' in text


def test_a_preset_name_cannot_reach_outside_the_assets(ws):
	text = message(ws, '[filter]\npreset = "../web-engineer"\n')

	assert 'unknown preset' in text


def test_an_invalid_regex_names_the_key_and_position(ws):
	text = message(ws, '[filter]\ninclude_any = ["(", "react"]\n')

	assert str(ws.config) in text
	assert 'filter.include_any[0]' in text
	assert 'invalid regex' in text


def test_exclude_companies_are_names_not_regexes(ws):
	write(ws, '[filter]\nexclude_companies = ["("]\n')

	assert load(ws).filter.exclude_companies == ('(',)


def test_the_six_presets_ship_with_the_package():
	names = {path.stem for path in workspace.presets_dir().glob('*.toml')}

	assert names == {
		'web-engineer',
		'backend-engineer',
		'data',
		'designer',
		'product-manager',
		'any',
	}


def test_the_shipped_template_loads_with_euremotejobs_disabled():
	"""The template is a stranger's starting config, so its only live key
	is the one board a user must opt into: euremotejobs' terms ban
	automated collection."""
	template = workspace.assets_dir() / workspace.CONFIG_NAME

	assert config.load(template, env={}) == config.Config(
		sources=config.SourcesSettings(disabled=('euremotejobs',))
	)


def test_the_network_user_agent_defaults_to_the_browser_string(ws):
	"""Empty means "leave the scraper UA as it is": shipping a custom
	agent by default would change what every board sees."""
	assert load(ws).network.user_agent == ''


def test_the_network_user_agent_is_configurable_every_way(ws):
	agent = 'jobtriage/0.3 (+https://github.com/niyiao/jobtriage)'

	write(ws, f'[network]\nuser_agent = "{agent}"\n')
	assert load(ws).network.user_agent == agent
	assert (
		load(ws, env={'JOBTRIAGE_NETWORK_USER_AGENT': 'env-agent'}).network.user_agent == 'env-agent'
	)
	assert load(ws, overrides={'network.user_agent': 'flag-agent'}).network.user_agent == 'flag-agent'


def test_a_multiline_user_agent_is_refused(ws):
	"""A newline in a header is an injection; fail at load, not board by
	board at request time."""
	text = message(ws, '[network]\nuser_agent = "two\\nlines"\n')

	assert 'network.user_agent' in text
	assert 'one line' in text


def test_presets_are_package_data_not_op_folder_templates():
	assert not any(name.startswith('presets/') for name in workspace._shipped_assets())


# -- [sources], [browser] ----------------------------------------------


def test_sources_override_tables_are_left_for_their_board(ws):
	write(
		ws,
		'[sources]\ndisabled = ["bruntwork"]\n[sources.wellfound]\nmax_pages = 8\n',
	)

	settings = load(ws)

	assert settings.sources.disabled == ('bruntwork',)
	assert settings.sources.overrides == {'wellfound': {'max_pages': 8}}


def test_a_source_override_must_be_a_table(ws):
	text = message(ws, '[sources]\nwellfound = 8\n')

	assert 'sources.wellfound' in text
	assert 'must be a table' in text


def test_a_browser_port_must_be_in_range(ws):
	assert 'browser.port' in message(ws, '[browser]\nport = 70000\n')
	assert 'browser.port' in message(ws, '[browser]\nport = 0\n')


def test_letters_paragraphs_is_a_positive_integer(ws):
	assert load(ws, env={'JOBTRIAGE_LETTERS_PARAGRAPHS': '4'}).letters.paragraphs == 4
	assert load(ws, overrides={'letters.paragraphs': 1}).letters.paragraphs == 1
	assert 'letters.paragraphs' in message(ws, '[letters]\nparagraphs = 0\n')
	assert 'letters.paragraphs' in message(ws, '[letters]\nparagraphs = "two"\n')


# -- [tui], including the reader that predates the full loader ----------


def test_the_tui_section_keeps_its_old_semantics(ws):
	write(
		ws,
		'[tui]\ndefault_filter = "new"\npage_size = 10\n\n[tui.keys]\nskipped = "x"\n',
	)

	settings = load(ws)

	assert settings.tui == config.TuiSettings('new', {'skipped': 'x'}, 10)
	assert config.load_tui(ws.config) == settings.tui


def test_a_tui_key_can_be_rebound_by_an_override(ws):
	settings = load(ws, overrides={'tui.keys.skipped': 'x'})

	assert settings.tui.keys == {'skipped': 'x'}


def test_load_tui_still_ignores_the_other_sections(ws):
	"""The TUI opens even when [search] is broken: that is the old
	contract, and the full loader is the one that validates it."""
	write(ws, '[search]\nlimit = "bad"\n\n[tui]\ndefault_filter = "new"\n')

	assert config.load_tui(ws.config).default_filter == 'new'
	with pytest.raises(ConfigError):
		load(ws)


def test_load_tui_missing_file_is_defaults(tmp_path):
	assert config.load_tui(tmp_path / 'jobtriage.toml') == config.TuiSettings()
