"""The op folder's config: jobtriage.toml, every section, one loader.

The config started as the TUI's two settings — startup tab and key
rebindings — and that narrow reader survives as `load_tui`, because the
TUI must not care whether a `[search]` typo exists. The full loader is
`load(ws)`, which resolves every section the tool reads:

    search   where the user can work, which families, age, limit
    filter   the title filter: a preset plus regex fragments
    letters  how long a cover letter should be
    sources  disabled boards and per-board overrides
    browser  the executable ('' = auto-detect) and CDP port
    network  the User-Agent the scrapers send ('' = the browser string)
    tui      default tab, page size, key rebindings

Precedence, lowest first:

    code default < preset < jobtriage.toml < JOBTRIAGE_* env < overrides

`overrides` is what the CLI passes for flags: a mapping of dotted keys
(`'search.limit': 20`) to already-typed Python values. The file and the
environment are validated at load and fail fast: a config the tool
cannot honour should stop the run with the file, the key and — where a
typo has an obvious fix — a difflib suggestion, not silently do
something else.

`load_with_provenance` is the same resolution and also says which layer
set each leaf — `config show` prints that, so a user can tell a default
from their own file from a `JOBTRIAGE_*` variable. `load` stays the
short signature every other caller uses; it is the same code with the
map dropped.

Environment variables follow the section/key names:

    JOBTRIAGE_SEARCH_LOCATIONS='["emea", "NG"]'   TOML array
    JOBTRIAGE_SEARCH_FAMILIES='["software"]'
    JOBTRIAGE_SEARCH_EMPLOYMENT='["full-time"]'
    JOBTRIAGE_SEARCH_MAX_AGE_DAYS=14              TOML integer
    JOBTRIAGE_SEARCH_LIMIT=100
    JOBTRIAGE_FILTER_PRESET=web-engineer          string, taken verbatim
    JOBTRIAGE_FILTER_REQUIRE_ANY='["engineers?"]'
    JOBTRIAGE_FILTER_INCLUDE_ANY='["react"]'
    JOBTRIAGE_FILTER_INCLUDE_ANY_EXTRA='["svelte"]'
    JOBTRIAGE_FILTER_EXCLUDE_ANY='["manager"]'
    JOBTRIAGE_FILTER_EXCLUDE_COMPANIES='["Acme"]'
    JOBTRIAGE_LETTERS_PARAGRAPHS=3
    JOBTRIAGE_SOURCES_DISABLED='["bruntwork"]'
    JOBTRIAGE_BROWSER_EXECUTABLE=/path/to/chrome  string, taken verbatim
    JOBTRIAGE_BROWSER_PORT=9223
    JOBTRIAGE_NETWORK_USER_AGENT='my-jobtriage/1.0'  string, taken verbatim
    JOBTRIAGE_TUI_DEFAULT_FILTER=new
    JOBTRIAGE_TUI_PAGE_SIZE=50

Strings are taken as written (`JOBTRIAGE_BROWSER_EXECUTABLE` can
contain spaces and backslashes); arrays and integers are TOML literals,
which is the syntax the file already uses. Per-board override tables
and key rebindings have no environment spelling. Unrelated
`JOBTRIAGE_*` variables (`JOBTRIAGE_HOME` is the workspace, not
config) are ignored.

`[filter]` empty — or `preset = "any"` — means no title filter at all:
the board's category is the only narrowing, which is the right default
for a stranger. Presets ship in the package's assets and are read here,
never copied into the op folder.
"""

import difflib
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from textual.keys import Keys as TextualKeys

from jobtriage import geo, status, workspace
from jobtriage.tui import keys


class ConfigError(ValueError):
	"""The config exists but says something this build cannot use.

	Its own class (rather than bare ValueError) so the CLI can print it
	as a clean `error:` line instead of a traceback, the way plan 03's
	`config validate` will. Every message names the file (or the
	environment variable / override), the section.key and position,
	and a suggestion when one is close.
	"""


@dataclass(frozen=True, slots=True)
class SearchSettings:
	"""The `[search]` table, resolved against the defaults.

	`locations` holds canonical `geo` tokens — regions stay regions
	(`'emea'`) and countries are ISO codes (`'NG'`), aliases already
	resolved. The member countries of a region are *not* flattened
	here: `geo.expand(config.search)` does that when a caller needs the
	set, and `geo.board_tags(module, config.search)` translates the
	tokens into the board's covering tags (worldwide, any region at
	least as wide as the ask, and the country's own wording).
	"""

	locations: tuple[str, ...] = ('worldwide',)
	families: tuple[str, ...] = ('software',)
	employment: tuple[str, ...] = ('full-time',)
	max_age_days: int = 21
	limit: int = 50


@dataclass(frozen=True, slots=True)
class FilterSettings:
	"""The `[filter]` table: regex fragments and an optional preset.

	`require_any` and `include_any` are two positive gates (both must
	pass; an empty one is disabled), and `exclude_any` is the turnaway
	gate — today's relevance.py semantics. Empty lists everywhere mean
	no title filter, which is `preset = "any"`.

	`preset` records which shipped preset the lists started from; the
	lists are the merged result, not the preset's own values.
	"""

	preset: str | None = None
	require_any: tuple[str, ...] = ()
	include_any: tuple[str, ...] = ()
	exclude_any: tuple[str, ...] = ()
	exclude_companies: tuple[str, ...] = ()

	@property
	def enabled(self) -> bool:
		"""True when any gate could drop a title."""
		return bool(self.require_any or self.include_any or self.exclude_any or self.exclude_companies)


@dataclass(frozen=True, slots=True)
class LettersSettings:
	"""The `[letters]` table: how long a cover letter should be.

	One key today, and the skill reads it before writing: a letter is
	`paragraphs` body paragraphs, default two. It lives in the config
	so the person can change the length once instead of editing a
	skill or repeating the request every time.
	"""

	paragraphs: int = 2


@dataclass(frozen=True, slots=True)
class SourcesSettings:
	"""The `[sources]` table: disabled boards and per-board overrides.

	`overrides` is `{board: {keyword: value}}` from `[sources.wellfound]`
	`max_pages = 8`; the pipeline passes each table to that board's
	`recent_jobs` as keyword arguments. Values are left as TOML read
	them — the board owns their meaning.
	"""

	disabled: tuple[str, ...] = ()
	overrides: dict[str, dict[str, object]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BrowserSettings:
	"""The `[browser]` table; an empty executable means auto-detect."""

	executable: str = ''
	port: int = 9222


@dataclass(frozen=True, slots=True)
class NetworkSettings:
	"""The `[network]` table: the User-Agent every board request sends.

	Empty is the shipped default and means the browser UA the boards
	have always been sent (several 403 the default Python one). A value
	is for a person who wants the traffic honest about the tool, or for
	a board that wants a token in the agent; it replaces the browser
	string on every request unless a board passes its own (`ua=`), which
	euremotejobs does because parentheses are refused.
	"""

	user_agent: str = ''


# Resolved before the dataclass body, where the `keys` field would
# otherwise shadow the imported module of the same name.
_TUI_PAGE = keys.PAGE


@dataclass(frozen=True, slots=True)
class TuiSettings:
	"""The `[tui]` table, resolved against the defaults.

	`keys` holds only the actions the user rebound, keyed by canonical
	action name (`skipped = "x"`); keys.key_map applies them, so a
	default change in keys.py reaches every op folder that did not
	override it.
	"""

	default_filter: str = status.DEFAULT_FILTER
	keys: dict[str, str] = field(default_factory=dict)
	page_size: int = _TUI_PAGE


@dataclass(frozen=True, slots=True)
class Config:
	"""Every section, at the values the tool will actually use."""

	search: SearchSettings = field(default_factory=SearchSettings)
	filter: FilterSettings = field(default_factory=FilterSettings)
	letters: LettersSettings = field(default_factory=LettersSettings)
	sources: SourcesSettings = field(default_factory=SourcesSettings)
	browser: BrowserSettings = field(default_factory=BrowserSettings)
	network: NetworkSettings = field(default_factory=NetworkSettings)
	tui: TuiSettings = field(default_factory=TuiSettings)


# The employment values boards actually expose; best effort, as plan 03
# says. A missing value here is a config mistake, not a board feature.
EMPLOYMENT = ('full-time', 'contract', 'part-time')

_SEARCH_KEYS = ('locations', 'families', 'employment', 'max_age_days', 'limit')
_LETTERS_KEYS = ('paragraphs',)
_BROWSER_KEYS = ('executable', 'port')
_NETWORK_KEYS = ('user_agent',)
_TUI_KEYS = ('default_filter', 'page_size', 'keys')

_FILTER_LISTS = ('require_any', 'include_any', 'exclude_any', 'exclude_companies')
_FILTER_EXTRAS = ('require_any_extra', 'include_any_extra', 'exclude_any_extra')
_FILTER_ALL = _FILTER_LISTS + _FILTER_EXTRAS
_FILTER_KEYS = ('preset', *_FILTER_LISTS)
_REGEX_LISTS = frozenset({'require_any', 'include_any', 'exclude_any'})

_SECTIONS = ('search', 'filter', 'letters', 'sources', 'browser', 'network', 'tui')

# Preset names become file names under assets/presets; this keeps a
# config value from reaching outside that folder.
_PRESET_NAME = re.compile(r'^[a-z0-9][a-z0-9-]*$')

_MISSING = object()

# Every dotted key a CLI override may use. `sources.<board>.<key>` and
# `tui.keys.<action>` are handled structurally, since their tails are
# data-dependent.
_KNOWN_OVERRIDES = frozenset(
	[
		*(f'search.{key}' for key in _SEARCH_KEYS),
		*(f'filter.{key}' for key in _FILTER_KEYS + _FILTER_EXTRAS),
		*(f'letters.{key}' for key in _LETTERS_KEYS),
		'sources.disabled',
		*(f'browser.{key}' for key in _BROWSER_KEYS),
		*(f'network.{key}' for key in _NETWORK_KEYS),
		'tui.default_filter',
		'tui.page_size',
	]
)

# Modifier names Textual accepts in front of a key.
_MODIFIERS = frozenset({'ctrl', 'shift', 'alt', 'meta', 'super', 'hyper'})

# Named keys Textual understands: its Keys enum plus the punctuation
# names it maps single characters to before events reach us.
_NAMED_KEYS = frozenset(str(entry.value) for entry in TextualKeys) | {'question_mark'}


def _valid_base(base: str) -> bool:
	"""A key's final part: one printable character or a named key."""
	if len(base) == 1:
		return base.isprintable() and not base.isspace()
	return base in _NAMED_KEYS


def valid_key(key: str) -> bool:
	"""Could Textual ever match this binding key?

	Textual accepts any string in a Binding and simply never fires on a
	typo, which is why the config validates instead of trusting it. A
	key is modifiers (`ctrl+`, `shift+`, …) followed by one printable
	character or a named key; `ctrl+wat` and `x y` are refused.
	"""
	if not key or key != key.strip():
		return False
	parts = key.split('+')
	if len(parts) == 1:
		return _valid_base(parts[0])
	if any(part not in _MODIFIERS for part in parts[:-1]):
		return False
	return _valid_base(parts[-1])


def load(
	ws: workspace.Workspace | str | Path,
	*,
	env: Mapping[str, str] | None = None,
	overrides: Mapping[str, object] | None = None,
) -> Config:
	"""Resolve every section: defaults → preset → file → env → overrides.

	`ws` is the workspace (the real caller) or its config path (tests and
	`load_tui`'s old shape). A missing file is the documented state of a
	fresh op folder and yields the code defaults, merged with any env
	and overrides. A file that exists but cannot be honoured raises
	ConfigError naming the file and key.

	`env` defaults to os.environ; tests pass `{}` so the machine's
	variables cannot change their answer. `overrides` are dotted keys
	with Python values: `{'search.limit': 20, 'filter.preset': 'any'}`.

	The same resolution with each value's origin is
	`load_with_provenance`; this signature stays as the rest of the
	tool knows it.
	"""
	return load_with_provenance(ws, env=env, overrides=overrides)[0]


def load_with_provenance(
	ws: workspace.Workspace | str | Path,
	*,
	env: Mapping[str, str] | None = None,
	overrides: Mapping[str, object] | None = None,
) -> tuple[Config, dict[str, str]]:
	"""`load`, plus which layer set each leaf value.

	The second value maps a dotted key (`'search.limit'`) to its origin:

	    default            the dataclass default, untouched
	    file               jobtriage.toml
	    env:NAME           that JOBTRIAGE_* variable
	    flag               a CLI override
	    preset:NAME        a shipped title-filter preset

	A filter list built from several layers names each contributor in
	precedence order joined by '+' (`'preset:web-engineer+file'`),
	because `exclude_any` in the file extends the preset rather than
	replacing it. Per-board `sources.<board>.<key>` and rebound
	`tui.keys.<action>` get an entry each.

	`config show` is the only caller today; keeping the map out of
	`Config` means every other command reads the same dataclasses and
	no one pays for provenance they do not print.
	"""
	path = ws.config if isinstance(ws, workspace.Workspace) else Path(ws)
	env_values = dict(os.environ if env is None else env)
	flags = dict(overrides or {})
	_check_overrides(flags)

	data = _read(path)
	_check_sections(path, data)
	origins: dict[str, str] = {}
	settings = Config(
		search=_search(path, _table(path, data, 'search'), env_values, flags, origins),
		filter=_filter(path, _table(path, data, 'filter'), env_values, flags, origins),
		letters=_letters(path, _table(path, data, 'letters'), env_values, flags, origins),
		sources=_sources(path, _table(path, data, 'sources'), env_values, flags, origins),
		browser=_browser(path, _table(path, data, 'browser'), env_values, flags, origins),
		network=_network(path, _table(path, data, 'network'), env_values, flags, origins),
		tui=_tui(path, _table(path, data, 'tui'), env_values, flags, origins),
	)
	return settings, origins


def load_tui(path: str | Path) -> TuiSettings:
	"""Read `[tui]` from the workspace config, defaults when absent.

	`path` is `ws.config` — the workspace module is the only place that
	knows where it is. Raises ConfigError for a broken TOML file, an
	unknown action, an invalid key, a key used by two actions, or a
	default_filter that is not a tab.

	This is deliberately the narrow reader the TUI had before the full
	loader existed: it ignores `[search]`, `[filter]` and the rest, so a
	typo there cannot stop the TUI from opening. `load(ws).tui` gives
	the same answer when the whole file is valid.
	"""
	path = Path(path)
	data = _read(path)
	return _tui(path, _table(path, data, 'tui'), {}, {}, {})


# ---------------------------------------------------------------------------
# File reading and the shape of a section
# ---------------------------------------------------------------------------


def _read(path: Path) -> dict:
	"""The TOML file as a dict; {} when it is absent."""
	if not path.is_file():
		return {}
	try:
		data = tomllib.loads(path.read_text(encoding='utf-8'))
	except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
		raise ConfigError(f'{path}: {error}') from None
	if not isinstance(data, dict):  # TOML's root is always a table; kept for type checkers
		raise ConfigError(f'{path}: the config must be a TOML table')
	return data


def _check_sections(path: Path, data: dict) -> None:
	"""Only the documented sections are accepted."""
	for name in data:
		if name not in _SECTIONS:
			hint = _hint(name, _SECTIONS)
			raise ConfigError(f'{path}: [{name}] is not a config section{hint}')


def _table(path: Path, data: dict, section: str) -> dict:
	value = data.get(section, {})
	if not isinstance(value, dict):
		raise ConfigError(f'{path}: [{section}] must be a table')
	return value


def _check_keys(
	path: Path, section: str, table: dict, known: tuple[str, ...] | frozenset[str]
) -> None:
	"""Reject a key this section does not have, suggesting the near miss.

	Silently ignoring `max_age = 21` would leave the user with a setting
	that never fires; the error is the only way they learn.
	"""
	for key in table:
		if key not in known:
			hint = _hint(key, known)
			raise ConfigError(f'{path}: {section}.{key}: unknown key{hint}')


def _hint(value: object, candidates) -> str:
	"""` (did you mean "x"?)` for the closest candidate, or ''.

	Difflib handles typos; a unique prefix handles the abbreviation
	people actually type for a preset ("web" for "web-engineer"),
	where the ratio is too low for difflib to call it close.
	"""
	text = str(value)
	match = difflib.get_close_matches(text, [str(candidate) for candidate in candidates], n=1)
	if not match:
		prefixed = [str(candidate) for candidate in candidates if str(candidate).startswith(text)]
		if len(prefixed) == 1:
			match = prefixed
	return f' (did you mean {match[0]!r}?)' if match else ''


_TYPE_NAMES = {
	str: 'a string',
	int: 'an integer',
	bool: 'a boolean',
	float: 'a number',
	list: 'an array',
	tuple: 'an array',
	dict: 'a table',
}


def _typename(value: object) -> str:
	return _TYPE_NAMES.get(type(value), type(value).__name__)


def _as_str(value: object, where: str, key: str) -> str:
	if not isinstance(value, str):
		raise ConfigError(f'{where}: {key}: expected a string, got {_typename(value)}')
	return value


def _as_values(value: object, at: str) -> tuple[str, ...]:
	"""An array of strings, with the failing index in the message."""
	if not isinstance(value, (list, tuple)):
		raise ConfigError(f'{at}: expected an array of strings, got {_typename(value)}')
	values = []
	for index, item in enumerate(value):
		if not isinstance(item, str):
			raise ConfigError(f'{at}[{index}]: expected a string, got {_typename(item)}')
		values.append(item)
	return tuple(values)


def _as_int(
	value: object,
	where: str,
	key: str,
	*,
	minimum: int | None = None,
	maximum: int | None = None,
) -> int:
	if isinstance(value, bool) or not isinstance(value, int):
		raise ConfigError(f'{where}: {key}: expected an integer, got {_typename(value)}')
	if minimum is not None and value < minimum:
		raise ConfigError(f'{where}: {key}: must be at least {minimum}, got {value}')
	if maximum is not None and value > maximum:
		raise ConfigError(f'{where}: {key}: must be at most {maximum}, got {value}')
	return value


# ---------------------------------------------------------------------------
# Environment and overrides
# ---------------------------------------------------------------------------


def _env_name(section: str, key: str) -> str:
	return f'JOBTRIAGE_{section.upper()}_{key.upper()}'


def _env_str(env: Mapping[str, str], name: str) -> str:
	"""A string variable is taken verbatim: paths need no TOML quotes."""
	return env[name]


def _env_list(env: Mapping[str, str], name: str) -> tuple[str, ...]:
	"""A list variable is a TOML array, the syntax the file already uses."""
	raw = env[name]
	try:
		value = tomllib.loads(f'value = {raw}')['value']
	except tomllib.TOMLDecodeError:
		raise ConfigError(f'{name}: expected a TOML array, got {raw!r}') from None
	return _as_values(value, name)


def _env_int(env: Mapping[str, str], name: str) -> int:
	raw = env[name]
	try:
		value = tomllib.loads(f'value = {raw}')['value']
	except tomllib.TOMLDecodeError:
		raise ConfigError(f'{name}: expected an integer, got {raw!r}') from None
	return _as_int(value, name, 'value')


def _layer(
	path: Path,
	section: str,
	key: str,
	table: dict,
	env: Mapping[str, str],
	overrides: Mapping[str, object],
	reader,
) -> tuple[object, str]:
	"""One value from the highest layer that sets it, plus its origin.

	Overrides win over the environment, which wins over the file; a
	value absent everywhere is `_MISSING`. `reader` is how that
	section's field comes out of the environment (strings verbatim,
	arrays and integers as TOML literals).
	"""
	override_key = f'{section}.{key}'
	if override_key in overrides:
		return overrides[override_key], f'override {override_key}'
	name = _env_name(section, key)
	if name in env:
		return reader(env, name), name
	if key in table:
		return table[key], str(path)
	return _MISSING, ''


def _origin(where: str) -> str:
	"""The provenance label for one `_layer` origin string.

	`_layer` already computes where a value came from but discards the
	answer: it returns '' (nothing set it), 'override <key>', an env
	variable name, or the config path. `config show` only needs the
	layer, so the path collapses to 'file' and the others keep their
	detail.
	"""
	if not where:
		return 'default'
	if where.startswith('override '):
		return 'flag'
	if where.startswith('JOBTRIAGE_'):
		return f'env:{where}'
	return 'file'


def _leaf_paths(prefix: str, value: object):
	"""Yield dotted leaves for a TOML table: `sources.board.max_pages`.

	Boards receive scalars today, but a nested table is still TOML a
	user can write; flattening it keeps `show` from losing the origin
	of the inner keys.
	"""
	if isinstance(value, dict):
		for key, item in value.items():
			yield from _leaf_paths(f'{prefix}.{key}', item)
	else:
		yield prefix


def _check_overrides(overrides: Mapping[str, object]) -> None:
	"""Reject a dotted key no section knows, before any section reads it."""
	for key in overrides:
		if key in _KNOWN_OVERRIDES:
			continue
		parts = key.split('.')
		if len(parts) in (2, 3) and parts[0] == 'sources' and all(parts[1:]):
			continue  # sources.<board> or sources.<board>.<keyword>
		if len(parts) == 3 and parts[0] == 'tui' and parts[1] == 'keys' and parts[2]:
			continue  # tui.keys.<action>
		hint = _hint(key, sorted(_KNOWN_OVERRIDES))
		raise ConfigError(f'override {key}: unknown config key{hint}')


# ---------------------------------------------------------------------------
# [search]
# ---------------------------------------------------------------------------


def _search(
	path: Path,
	table: dict,
	env: Mapping[str, str],
	overrides: Mapping[str, object],
	origins: dict[str, str],
) -> SearchSettings:
	_check_keys(path, 'search', table, _SEARCH_KEYS)
	defaults = SearchSettings()

	raw, where = _layer(path, 'search', 'locations', table, env, overrides, _env_list)
	if raw is _MISSING:
		locations = defaults.locations
	else:
		locations = _locations(_as_values(raw, f'{where}: search.locations'), where)
	origins['search.locations'] = _origin(where)

	raw, where = _layer(path, 'search', 'families', table, env, overrides, _env_list)
	if raw is _MISSING:
		families = defaults.families
	else:
		families = _slugs(_as_values(raw, f'{where}: search.families'), where, 'search.families')
	origins['search.families'] = _origin(where)

	raw, where = _layer(path, 'search', 'employment', table, env, overrides, _env_list)
	if raw is _MISSING:
		employment = defaults.employment
	else:
		employment = _employment(
			_as_values(raw, f'{where}: search.employment'), where, 'search.employment'
		)
	origins['search.employment'] = _origin(where)

	raw, where = _layer(path, 'search', 'max_age_days', table, env, overrides, _env_int)
	if raw is _MISSING:
		max_age_days = defaults.max_age_days
	else:
		max_age_days = _as_int(raw, where, 'search.max_age_days', minimum=0)
	origins['search.max_age_days'] = _origin(where)

	raw, where = _layer(path, 'search', 'limit', table, env, overrides, _env_int)
	if raw is _MISSING:
		limit = defaults.limit
	else:
		limit = _as_int(raw, where, 'search.limit', minimum=1)
	origins['search.limit'] = _origin(where)

	return SearchSettings(
		locations=locations,
		families=families,
		employment=employment,
		max_age_days=max_age_days,
		limit=limit,
	)


def _locations(values: tuple[str, ...], where: str) -> tuple[str, ...]:
	"""Resolve every location to its canonical token, fail fast on a typo."""
	resolved: list[str] = []
	for index, value in enumerate(values):
		token = geo.canonical(value)
		if token is None:
			matches = geo.suggestions(value)
			hint = f' (did you mean {matches[0]!r}?)' if matches else ''
			raise ConfigError(
				f'{where}: search.locations[{index}]: unknown region or country {value!r}{hint}'
			)
		if token not in resolved:
			resolved.append(token)
	return tuple(resolved)


def _slugs(values: tuple[str, ...], where: str, key: str) -> tuple[str, ...]:
	"""Lowercase, hyphen-joined identifiers; order kept, duplicates gone."""
	resolved: list[str] = []
	for index, value in enumerate(values):
		slug = '-'.join(value.strip().casefold().replace('_', '-').split())
		if not slug:
			raise ConfigError(f'{where}: {key}[{index}]: must not be empty')
		if slug not in resolved:
			resolved.append(slug)
	return tuple(resolved)


def _employment(values: tuple[str, ...], where: str, key: str) -> tuple[str, ...]:
	"""The plan's three employment types, with 'full time' read as full-time."""
	resolved: list[str] = []
	for index, value in enumerate(values):
		slug = '-'.join(value.strip().casefold().replace('_', '-').split())
		if slug not in EMPLOYMENT:
			allowed = ', '.join(EMPLOYMENT)
			hint = _hint(slug, EMPLOYMENT)
			raise ConfigError(
				f'{where}: {key}[{index}]: unknown employment type {value!r} '
				f'(expected one of {allowed}){hint}'
			)
		if slug not in resolved:
			resolved.append(slug)
	return tuple(resolved)


# ---------------------------------------------------------------------------
# [filter]
# ---------------------------------------------------------------------------


def _filter(
	path: Path,
	table: dict,
	env: Mapping[str, str],
	overrides: Mapping[str, object],
	origins: dict[str, str],
) -> FilterSettings:
	"""Preset first, then file/env/overrides on top.

	`exclude_any` in the file *extends* the preset (today's turnaway
	list is a starting point, not a ceiling); the positive gates
	replace. `*_extra` always extends. Environment and override values
	replace what came below them, because that is what higher
	precedence means; their `_extra` keys extend instead.
	"""
	_check_keys(path, 'filter', table, _FILTER_KEYS + _FILTER_EXTRAS)
	preset = _preset_choice(path, table, env, overrides, origins)
	preset_lists = _preset_lists(preset)
	merged = {key: list(values) for key, values in preset_lists.items()}
	for key in _FILTER_LISTS:
		merged.setdefault(key, [])

	file_layer = _filter_file_layer(path, table)
	env_layer = _filter_env_layer(env)
	override_layer = _filter_override_layer(overrides)

	for layer_name, layer in (('file', file_layer), ('env', env_layer), ('override', override_layer)):
		for key in _FILTER_LISTS:
			if key in layer:
				values = list(layer[key])
				if layer_name == 'file' and key == 'exclude_any':
					merged[key].extend(values)  # the preset's turnaways stay
				else:
					merged[key] = values
			extra = f'{key}_extra'
			if extra in layer:
				merged[key].extend(layer[extra])

	_filter_origins(origins, preset, preset_lists, file_layer, env_layer, override_layer)

	return FilterSettings(
		preset=preset,
		require_any=_dedupe(merged['require_any']),
		include_any=_dedupe(merged['include_any']),
		exclude_any=_dedupe(merged['exclude_any']),
		exclude_companies=_dedupe(merged['exclude_companies']),
	)


def _preset_choice(
	path: Path,
	table: dict,
	env: Mapping[str, str],
	overrides: Mapping[str, object],
	origins: dict[str, str],
) -> str | None:
	raw, where = _layer(path, 'filter', 'preset', table, env, overrides, _env_str)
	origins['filter.preset'] = _origin(where)
	if raw is _MISSING:
		return None
	name = _as_str(raw, where, 'filter.preset').strip()
	if not name:
		return None
	available = _preset_names()
	if not _PRESET_NAME.match(name) or name not in available:
		hint = _hint(name, available)
		raise ConfigError(f'{where}: filter.preset: unknown preset {name!r}{hint}')
	return name


def _preset_names() -> list[str]:
	folder = workspace.presets_dir()
	if not folder.is_dir():
		return []
	return sorted(path.stem for path in folder.glob('*.toml'))


def _preset_lists(name: str | None) -> dict[str, tuple[str, ...]]:
	"""One shipped preset's lists, validated like the user's own."""
	if name is None:
		return {}
	path = workspace.presets_dir() / f'{name}.toml'
	data = _read(path)
	if isinstance(data.get('filter'), dict):  # accept a preset written like a config
		data = data['filter']
	_check_keys(path, 'preset', data, _FILTER_LISTS)
	lists: dict[str, tuple[str, ...]] = {}
	for key in _FILTER_LISTS:
		if key not in data:
			continue
		at = f'{path}: {key}'
		values = _as_values(data[key], at)
		_check_filter_values(key, values, at)
		lists[key] = values
	return lists


def _filter_file_layer(path: Path, table: dict) -> dict[str, tuple[str, ...]]:
	layer: dict[str, tuple[str, ...]] = {}
	for key in _FILTER_ALL:
		if key not in table:
			continue
		at = f'{path}: filter.{key}'
		values = _as_values(table[key], at)
		_check_filter_values(key, values, at)
		layer[key] = values
	return layer


def _filter_env_layer(env: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
	layer: dict[str, tuple[str, ...]] = {}
	for key in _FILTER_ALL:
		name = _env_name('filter', key)
		if name not in env:
			continue
		values = _env_list(env, name)
		_check_filter_values(key, values, name)
		layer[key] = values
	return layer


def _filter_override_layer(overrides: Mapping[str, object]) -> dict[str, tuple[str, ...]]:
	layer: dict[str, tuple[str, ...]] = {}
	for key in _FILTER_ALL:
		name = f'filter.{key}'
		if name not in overrides:
			continue
		at = f'override {name}'
		values = _as_values(overrides[name], at)
		_check_filter_values(key, values, at)
		layer[key] = values
	return layer


def _filter_origins(
	origins: dict[str, str],
	preset: str | None,
	preset_lists: dict[str, tuple[str, ...]],
	file_layer: dict[str, tuple[str, ...]],
	env_layer: dict[str, tuple[str, ...]],
	override_layer: dict[str, tuple[str, ...]],
) -> None:
	"""Say which layers actually contributed to each merged list.

	Unlike a scalar, a filter list can be built from more than one
	layer: the preset's turnaway list extended by the file. Naming
	each contributor in precedence order is more honest than picking
	the last one, and makes "why is this title dropped?" answerable
	without guessing where the values came from.
	"""
	for key in _FILTER_LISTS:
		contributors: list[str] = []
		extra = f'{key}_extra'
		if preset is not None and key in preset_lists:
			contributors.append(f'preset:{preset}')
		if key in file_layer:
			if key == 'exclude_any' and contributors:
				contributors.append('file')
			else:
				contributors = ['file']
		if extra in file_layer and 'file' not in contributors:
			contributors.append('file')
		if key in env_layer:
			contributors = [f'env:{_env_name("filter", key)}']
		if extra in env_layer:
			label = f'env:{_env_name("filter", extra)}'
			if label not in contributors:
				contributors.append(label)
		if key in override_layer:
			contributors = ['flag']
		if extra in override_layer and 'flag' not in contributors:
			contributors.append('flag')
		origins[f'filter.{key}'] = '+'.join(contributors) or 'default'


def _check_filter_values(key: str, values: tuple[str, ...], at: str) -> None:
	"""A fragment must be a usable regex; an empty company would match all."""
	base = key.removesuffix('_extra')
	for index, value in enumerate(values):
		if not value.strip():
			raise ConfigError(f'{at}[{index}]: must not be empty')
		if base not in _REGEX_LISTS:
			continue
		try:
			re.compile(value)
		except re.error as error:
			raise ConfigError(f'{at}[{index}]: invalid regex {value!r}: {error}') from None


def _dedupe(values: list[str]) -> tuple[str, ...]:
	seen: list[str] = []
	for value in values:
		if value not in seen:
			seen.append(value)
	return tuple(seen)


# ---------------------------------------------------------------------------
# [letters]
# ---------------------------------------------------------------------------


def _letters(
	path: Path,
	table: dict,
	env: Mapping[str, str],
	overrides: Mapping[str, object],
	origins: dict[str, str],
) -> LettersSettings:
	"""The length of a cover letter; one paragraph is the floor.

	Two is the default because a letter is a note beside the resume,
	not a second essay. The loader refuses zero rather than letting a
	skill write a letter with no body; an integer this small does not
	need an upper bound.
	"""
	_check_keys(path, 'letters', table, _LETTERS_KEYS)
	defaults = LettersSettings()

	raw, where = _layer(path, 'letters', 'paragraphs', table, env, overrides, _env_int)
	paragraphs = (
		defaults.paragraphs if raw is _MISSING else _as_int(raw, where, 'letters.paragraphs', minimum=1)
	)
	origins['letters.paragraphs'] = _origin(where)

	return LettersSettings(paragraphs=paragraphs)


# ---------------------------------------------------------------------------
# [sources]
# ---------------------------------------------------------------------------


def _sources(
	path: Path,
	table: dict,
	env: Mapping[str, str],
	overrides: Mapping[str, object],
	origins: dict[str, str],
) -> SourcesSettings:
	"""`disabled` plus one arbitrary table per board.

	Board names cannot be checked against a vocabulary here — plugins
	(plan 04) can add any name after this module is loaded — so an
	unknown table is the pipeline's notice to give, not the loader's.
	"""
	overrides_by_board: dict[str, dict[str, object]] = {}
	for key, value in table.items():
		if key == 'disabled':
			continue
		if not isinstance(value, dict):
			raise ConfigError(
				f'{path}: sources.{key}: per-source overrides must be a table, got {_typename(value)}'
			)
		overrides_by_board[key] = dict(value)
		for leaf in _leaf_paths(f'sources.{key}', value):
			origins[leaf] = 'file'

	raw, where = _layer(path, 'sources', 'disabled', table, env, overrides, _env_list)
	if raw is _MISSING:
		disabled = SourcesSettings().disabled
	else:
		disabled = _slugs(_as_values(raw, f'{where}: sources.disabled'), where, 'sources.disabled')
	origins['sources.disabled'] = _origin(where)

	for key, value in overrides.items():
		parts = key.split('.')
		if parts[0] != 'sources' or parts[1] == 'disabled':
			continue
		if len(parts) == 2:
			if not isinstance(value, dict):
				raise ConfigError(
					f'override {key}: per-source overrides must be a table, got {_typename(value)}'
				)
			overrides_by_board.setdefault(parts[1], {}).update(value)
			for leaf in _leaf_paths(f'sources.{parts[1]}', value):
				origins[leaf] = 'flag'
		else:  # sources.<board>.<keyword>; three parts is enforced above
			overrides_by_board.setdefault(parts[1], {})[parts[2]] = value
			origins[f'sources.{parts[1]}.{parts[2]}'] = 'flag'

	return SourcesSettings(disabled=disabled, overrides=overrides_by_board)


# ---------------------------------------------------------------------------
# [browser]
# ---------------------------------------------------------------------------


def _browser(
	path: Path,
	table: dict,
	env: Mapping[str, str],
	overrides: Mapping[str, object],
	origins: dict[str, str],
) -> BrowserSettings:
	_check_keys(path, 'browser', table, _BROWSER_KEYS)
	defaults = BrowserSettings()

	raw, where = _layer(path, 'browser', 'executable', table, env, overrides, _env_str)
	executable = defaults.executable if raw is _MISSING else _as_str(raw, where, 'browser.executable')
	origins['browser.executable'] = _origin(where)

	raw, where = _layer(path, 'browser', 'port', table, env, overrides, _env_int)
	port = (
		defaults.port
		if raw is _MISSING
		else _as_int(raw, where, 'browser.port', minimum=1, maximum=65535)
	)
	origins['browser.port'] = _origin(where)

	return BrowserSettings(executable=executable, port=port)


# ---------------------------------------------------------------------------
# [network]
# ---------------------------------------------------------------------------


def _network(
	path: Path,
	table: dict,
	env: Mapping[str, str],
	overrides: Mapping[str, object],
	origins: dict[str, str],
) -> NetworkSettings:
	"""The scraper User-Agent; empty keeps the browser string.

	A control character is refused rather than passed to urllib: a
	newline in a header is an injection, and a value that silently makes
	every board fail is the worst way to learn it.
	"""
	_check_keys(path, 'network', table, _NETWORK_KEYS)
	defaults = NetworkSettings()

	raw, where = _layer(path, 'network', 'user_agent', table, env, overrides, _env_str)
	user_agent = (
		defaults.user_agent if raw is _MISSING else _as_str(raw, where, 'network.user_agent').strip()
	)
	if any(character in user_agent for character in ('\n', '\r')):
		raise ConfigError(f'{where}: network.user_agent: must be one line')
	origins['network.user_agent'] = _origin(where)

	return NetworkSettings(user_agent=user_agent)


# ---------------------------------------------------------------------------
# [tui]
# ---------------------------------------------------------------------------


def _tui(
	path: Path,
	table: dict,
	env: Mapping[str, str],
	overrides: Mapping[str, object],
	origins: dict[str, str],
) -> TuiSettings:
	_check_keys(path, 'tui', table, _TUI_KEYS)
	defaults = TuiSettings()

	raw, where = _layer(path, 'tui', 'default_filter', table, env, overrides, _env_str)
	default_filter = (
		defaults.default_filter if raw is _MISSING else _as_str(raw, where, 'default_filter')
	)
	origins['tui.default_filter'] = _origin(where)
	if default_filter not in keys.FILTERS:
		tabs = ', '.join(keys.FILTERS)
		if where == str(path):
			raise ConfigError(f'{path}: [tui] default_filter = {default_filter!r} is not a tab ({tabs})')
		raise ConfigError(f'{where}: default_filter = {default_filter!r} is not a tab ({tabs})')

	raw, where = _layer(path, 'tui', 'page_size', table, env, overrides, _env_int)
	page_size = (
		defaults.page_size if raw is _MISSING else _as_int(raw, where, 'tui.page_size', minimum=1)
	)
	origins['tui.page_size'] = _origin(where)

	bindings = table.get('keys', {})
	if not isinstance(bindings, dict):
		raise ConfigError(f'{path}: [tui.keys] must be a table')

	resolved: dict[str, str] = {}
	for name, key in bindings.items():
		if name not in keys.NAMES:
			raise ConfigError(f'{path}: [tui.keys] has unknown action {name!r}')
		if not isinstance(key, str) or not valid_key(key):
			raise ConfigError(f'{path}: [tui.keys] {name} = {key!r} is not a valid key')
		resolved[name] = key
		origins[f'tui.keys.{name}'] = 'file'

	# A flag can rebind too (plan 03's config set spells it
	# tui.keys.<action>); the same validation, with the flag as origin.
	for key, value in overrides.items():
		parts = key.split('.')
		if len(parts) != 3 or parts[0] != 'tui' or parts[1] != 'keys':
			continue
		name = parts[2]
		if name not in keys.NAMES:
			raise ConfigError(f'override {key}: unknown action {name!r}')
		if not isinstance(value, str) or not valid_key(value):
			raise ConfigError(f'override {key}: {value!r} is not a valid key')
		resolved[name] = value
		origins[f'tui.keys.{name}'] = 'flag'

	# The defaults and the overrides together must still be one key per
	# action: a duplicate would make one of the two actions unreachable
	# with no hint that the other won.
	seen: dict[str, str] = {}
	for entry in keys.resolve(resolved):
		for key in entry.keys:
			other = seen.setdefault(key, entry.name)
			if other != entry.name:
				raise ConfigError(f'{path}: [tui.keys] {entry.name} = {key!r} collides with {other!r}')

	return TuiSettings(default_filter=default_filter, keys=resolved, page_size=page_size)
