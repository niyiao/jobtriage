"""Tests for the geography vocabulary: the vendored tables, expansion,
the board-side translation and the inverse.

No real scraper is imported: each test builds the declarations plan 03
gives the scrapers as a SimpleNamespace, which is enough to pin the
contract before step 3 points it at the real modules.
"""

import types

import pytest

from jobtriage import geo


def board(**declarations):
	"""A fake scraper module carrying only its location declarations."""
	return types.SimpleNamespace(**declarations)


NAME_BOARD = board(
	WORLDWIDE=('Worldwide',),
	REGIONS={'emea': ('EMEA', 'Europe'), 'europe': ('Europe',)},
	COUNTRY_STYLE='name',
	COUNTRY_ALIASES={'GB': 'UK', 'US': 'USA'},
)


# -- the tables --------------------------------------------------------


def test_the_country_table_is_complete_and_internally_consistent():
	assert len(geo.COUNTRIES) >= 250
	assert all(len(code) == 2 and code.isalpha() and code.isupper() for code in geo.COUNTRIES)
	assert geo.COUNTRIES['GB'] == 'United Kingdom'
	assert geo.COUNTRIES['NG'] == 'Nigeria'
	assert geo.COUNTRIES['US'] == 'United States'

	for region, members in geo.REGIONS.items():
		assert members, region
		assert all(code in geo.COUNTRIES for code in members), region
		assert len(set(members)) == len(members), region

	assert len(geo.REGIONS['eu']) == 27
	assert set(geo.REGIONS['uk-ireland']) == {'GB', 'IE'}
	assert set(geo.REGIONS['nordics']) == {'DK', 'FI', 'IS', 'NO', 'SE'}
	assert set(geo.REGIONS['benelux']) == {'BE', 'LU', 'NL'}
	assert 'NG' in geo.REGIONS['sub-saharan-africa']
	assert 'GB' in geo.REGIONS['western-europe']
	assert set(geo.REGIONS['sub-saharan-africa']) < set(geo.REGIONS['africa'])

	for alias, code in geo.COUNTRY_ALIASES.items():
		assert code in geo.COUNTRIES, alias
	for alias, region in geo.REGION_ALIASES.items():
		assert region in geo.REGIONS, alias
	assert set(geo.REGION_ALIASES) >= set(geo.REGIONS)


@pytest.mark.parametrize(
	('wider', 'narrower'),
	[
		('emea', 'europe'),
		('emea', 'middle-east'),
		('emea', 'africa'),
		('europe', 'eu'),
		('africa', 'sub-saharan-africa'),
		('europe', 'western-europe'),
		('europe', 'eastern-europe'),
		('europe', 'southern-europe'),
		('europe', 'northern-europe'),
		('europe', 'central-europe'),
	],
)
def test_region_tables_nest_the_way_containment_assumes(wider, narrower):
	"""`board_tags` walks supersets, so every one of these nestings is
	load-bearing: a tag declared under `narrower` claims area
	`narrower`, and only a real superset may be asked for when the user
	names it. A stray country in a sub-region table would make a match
	claim an area the board never offered, so the membership tables are
	pinned here rather than trusted."""
	assert set(geo.REGIONS[narrower]) <= set(geo.REGIONS[wider])


# -- expand ------------------------------------------------------------


def test_expand_flattens_regions_into_countries():
	expanded = geo.expand(['emea'])

	assert 'emea' in expanded
	assert expanded >= {'GB', 'DE', 'NG', 'ZA', 'AE'}
	assert 'US' not in expanded  # the Americas are not EMEA


def test_expand_resolves_aliases_and_the_special_value():
	assert geo.expand(['UK']) == frozenset({'GB'})
	assert geo.expand(['United Kingdom']) == frozenset({'GB'})
	assert geo.expand(['USA']) == frozenset({'US'})
	assert geo.expand(['Türkiye']) == frozenset({'TR'})
	assert geo.expand(['worldwide']) == frozenset({'worldwide'})
	assert geo.expand(['global']) == frozenset({'worldwide'})
	assert geo.expand([]) == frozenset()


def test_expand_reads_a_prefs_object_and_keeps_order_irrelevant():
	prefs = types.SimpleNamespace(locations=('emea', 'NG'))

	expanded = geo.expand(prefs)

	assert 'emea' in expanded
	assert expanded >= {'GB', 'NG', 'DE'}


def test_expand_rejects_a_typo_with_a_suggestion():
	with pytest.raises(geo.UnknownLocation) as caught:
		geo.expand(['emae'])

	assert 'emae' in str(caught.value)
	assert 'emea' in str(caught.value)


def test_canonical_is_case_and_punctuation_tolerant():
	assert geo.canonical('u.k.') == 'GB'
	assert geo.canonical('USA') == 'US'
	assert geo.canonical('czech republic') == 'CZ'
	assert geo.canonical('South Korea') == 'KR'
	assert geo.canonical('UAE') == 'AE'
	assert geo.canonical('Russian Federation') == 'RU'
	assert geo.canonical('EMEA') == 'emea'
	assert geo.canonical('Remote') is None


# -- board_tags --------------------------------------------------------


def test_board_tags_name_style_is_worldwide_superset_regions_and_wordings():
	"""Under containment a region token no longer drags in its member
	countries: `emea` asks for the board's EMEA wording, and Nigeria's
	country token adds its own wording. Germany is not in the tuple —
	the user never said they work from Germany, and a Germany-only job
	does not cover EMEA."""
	tags = geo.board_tags(NAME_BOARD, ['worldwide', 'emea', 'NG'])

	assert tags == ('Worldwide', 'EMEA', 'Europe', 'Nigeria')
	assert 'Germany' not in tags
	assert 'UK' not in tags  # no GB token was requested
	assert 'USA' not in tags  # the US is not in EMEA
	assert tags.count('Europe') == 1  # emea's wording, then deduped


def test_board_tags_a_country_pulls_upward_to_worldwide_and_containing_regions():
	"""A country means "I work from here": the job must be open to here
	or wider. Nigeria is in EMEA, so the board's Worldwide and EMEA
	tags are in scope — but not its member wordings (Germany) and not a
	region that does not contain Nigeria (the module's `europe` tag is
	only reached here because it is also the module's emea wording).
	"""
	tags = geo.board_tags(NAME_BOARD, ['NG'])

	assert tags == ('Worldwide', 'EMEA', 'Europe', 'Nigeria')
	assert 'Germany' not in tags
	assert 'USA' not in tags


def test_a_country_token_pulls_the_worldwide_tags():
	"""Even a board with no regions and no country wording for it: a
	worldwide-open job is open to Nigeria, so its worldwide tag is the
	one true match."""
	module = board(WORLDWIDE=('Anywhere',), COUNTRY_STYLE='name')

	assert geo.board_tags(module, ['NG']) == ('Anywhere', 'Nigeria')


def test_a_country_token_pulls_every_declared_containing_region():
	module = board(
		WORLDWIDE=('Worldwide',),
		REGIONS={'emea': ('EMEA',), 'africa': ('Africa',)},
		COUNTRY_STYLE='none',
	)

	# Nigeria is a member of both, and the regions are emitted in the
	# geo table's canonical order (emea before africa).
	assert geo.board_tags(module, ['NG']) == ('Worldwide', 'EMEA', 'Africa')


def test_a_country_token_only_pulls_regions_the_board_declares():
	module = board(
		WORLDWIDE=('Worldwide',),
		REGIONS={'africa': ('Africa',)},
		COUNTRY_STYLE='none',
	)

	assert geo.board_tags(module, ['NG']) == ('Worldwide', 'Africa')


def test_a_country_expands_only_to_regions_that_contain_it():
	"""GB is in EMEA, Europe and UK & Ireland; it is not in the Nordics
	or Benelux, so a declared region that does not contain it must not
	leak into the tuple. Nigeria shares only EMEA with the same module.
	"""
	module = board(
		WORLDWIDE=('Worldwide',),
		REGIONS={
			'emea': ('EMEA',),
			'europe': ('Europe',),
			'uk-ireland': ('UK & Ireland',),
			'nordics': ('Nordics',),
			'benelux': ('Benelux',),
		},
		COUNTRY_STYLE='none',
	)

	assert geo.board_tags(module, ['GB']) == ('Worldwide', 'EMEA', 'Europe', 'UK & Ireland')
	assert geo.board_tags(module, ['NG']) == ('Worldwide', 'EMEA')


def test_country_tokens_order_regions_canonically_and_are_deterministic():
	"""Same input, same tuple: the containing regions are walked in
	the geo table's order, never the module's declaration order, so two
	modules with the same declarations and a different dict order — and
	two calls with the same prefs — agree.
	"""
	forward = board(
		WORLDWIDE=('Worldwide',),
		REGIONS={'emea': ('EMEA',), 'africa': ('Africa',)},
		COUNTRY_STYLE='none',
	)
	backward = board(
		WORLDWIDE=('Worldwide',),
		REGIONS={'africa': ('Africa',), 'emea': ('EMEA',)},
		COUNTRY_STYLE='none',
	)

	assert geo.board_tags(forward, ['NG']) == geo.board_tags(backward, ['NG'])
	assert geo.board_tags(forward, ['NG']) == geo.board_tags(forward, ['NG'])


def test_multiple_countries_keep_the_worldwide_region_country_order():
	module = board(
		WORLDWIDE=('Anywhere', 'Anywhere'),
		REGIONS={'emea': ('EMEA',), 'europe': ('Europe',), 'africa': ('Africa',)},
		COUNTRY_STYLE='iso2',
		COUNTRY_ALIASES={'NG': 'Nigeria'},
	)

	# Nigeria is EMEA + Africa, GB is EMEA + Europe: the union of their
	# regions is emitted in the geo table's canonical order (emea,
	# europe, africa), before either country's wording.
	assert geo.board_tags(module, ['NG', 'GB']) == (
		'Anywhere',
		'EMEA',
		'Europe',
		'Africa',
		'Nigeria',
		'GB',
	)


def test_an_empty_alias_suppresses_the_country_not_its_regions():
	"""The empty-alias convention is about the country's own wording. A
	board that has no "Nigeria" tag can still have an EMEA bucket that
	covers it, and that bucket stays in the tuple."""
	module = board(
		WORLDWIDE=('Anywhere',),
		REGIONS={'emea': ('EMEA',), 'africa': ('Africa',)},
		COUNTRY_STYLE='name',
		COUNTRY_ALIASES={'NG': ''},
	)

	assert geo.board_tags(module, ['NG']) == ('Anywhere', 'EMEA', 'Africa')


def test_board_tags_iso2_style_emits_codes():
	module = board(
		WORLDWIDE=('Anywhere',),
		REGIONS={'emea': ('EMEA',)},
		COUNTRY_STYLE='iso2',
	)

	tags = geo.board_tags(module, ['worldwide', 'emea', 'NG', 'GB'])

	assert tags == ('Anywhere', 'EMEA', 'NG', 'GB')
	assert 'Nigeria' not in tags
	assert tags.count('EMEA') == 1  # both countries are inside it


def test_board_tags_none_style_has_no_country_tags():
	module = board(
		WORLDWIDE=('Anywhere',),
		REGIONS={'emea': ('EMEA',)},
		COUNTRY_STYLE='none',
	)

	assert geo.board_tags(module, ['worldwide', 'emea', 'NG']) == ('Anywhere', 'EMEA')


def test_a_region_token_pulls_worldwide_and_only_superset_region_tags():
	"""A region is a containment ask: the job must cover the whole
	region. `europe` asks for the board's EMEA and Europe tags (EMEA
	contains Europe); `emea` does not ask for Europe, because a
	Europe-only job does not cover EMEA. Member country wordings are
	never asked for: a Germany-only job does not cover Europe either.
	"""
	module = board(
		WORLDWIDE=('Anywhere',),
		REGIONS={
			'emea': ('EMEA',),
			'europe': ('Europe',),
			'africa': ('Africa',),
			'sub-saharan-africa': ('SSA',),
			'western-europe': ('Western Europe',),
		},
		COUNTRY_STYLE='name',
	)

	assert geo.board_tags(module, ['emea']) == ('Anywhere', 'EMEA')
	assert geo.board_tags(module, ['europe']) == ('Anywhere', 'EMEA', 'Europe')
	assert geo.board_tags(module, ['africa']) == ('Anywhere', 'EMEA', 'Africa')
	assert geo.board_tags(module, ['sub-saharan-africa']) == (
		'Anywhere',
		'EMEA',
		'Africa',
		'SSA',
	)
	assert geo.board_tags(module, ['western-europe']) == (
		'Anywhere',
		'EMEA',
		'Europe',
		'Western Europe',
	)
	assert 'Germany' not in geo.board_tags(module, ['europe'])


def test_a_board_with_no_declarations_means_no_location_filter():
	module = board(recent_jobs=list)

	assert geo.board_tags(module, ['worldwide', 'emea']) == ()


def test_an_empty_alias_suppresses_a_country_the_board_lacks():
	"""Boards carry a partial region, so a declaration needs a way to say
	"no tag for this country here". The convention is an empty alias: the
	codes a board knows get a wording, every other code in the region gets
	`''`, and board_tags invents nothing. Both country styles honour it —
	a board whose tags are ISO codes still has to leave some countries
	out. normalize ignores the empty entries, so a stray blank tag can
	never become a country.
	"""
	named = board(
		REGIONS={},
		COUNTRY_STYLE='name',
		COUNTRY_ALIASES={'GB': 'UK', 'CZ': 'Czech Republic', 'NG': '', 'DE': ''},
	)
	assert geo.board_tags(named, ['GB', 'CZ', 'NG', 'DE']) == ('UK', 'Czech Republic')

	coded = board(
		REGIONS={},
		COUNTRY_STYLE='iso2',
		COUNTRY_ALIASES={code: ('NG' if code == 'NG' else '') for code in geo.REGIONS['emea']},
	)
	assert geo.board_tags(coded, ['NG']) == ('NG',)
	assert geo.board_tags(coded, ['GB']) == ()  # GB's alias is empty: suppressed
	assert geo.normalize(coded, ['NG', '']) == ['NG']


def test_tuple_aliases_emit_every_wording_and_normalize_back():
	"""One country, two board spellings: wellfound writes CZ both ways.
	A tuple alias is the declaration for that, and the inverse resolves
	either spelling to the one code."""
	module = board(
		COUNTRY_STYLE='name',
		COUNTRY_ALIASES={'CZ': ('Czechia', 'Czech Republic'), 'DE': 'Germany', 'NG': ''},
	)

	assert geo.board_tags(module, ['CZ', 'DE', 'NG']) == (
		'Czechia',
		'Czech Republic',
		'Germany',
	)
	assert geo.normalize(module, ['Czechia', 'czech republic', 'Germany', '']) == ['CZ', 'DE']


def test_board_tags_deduplicates_case_insensitively():
	module = board(REGIONS={'emea': ('EMEA', 'emea')}, COUNTRY_STYLE='none')

	assert geo.board_tags(module, ['emea']) == ('EMEA',)


def test_board_tags_rejects_an_unknown_region_declaration():
	module = board(REGIONS={'emae': ('EMEA',)}, COUNTRY_STYLE='none')

	with pytest.raises(ValueError, match='emae'):
		geo.board_tags(module, ['emea'])


# -- normalize ---------------------------------------------------------


def test_normalize_maps_module_wording_and_global_names_back():
	tags = ['Anywhere', 'EMEA', 'Europe', 'UK', 'United States', 'Moon']

	assert geo.normalize(NAME_BOARD, tags) == ['worldwide', 'emea', 'GB', 'US', 'Moon']


def test_normalize_prefers_the_boards_own_country_wording():
	module = board(COUNTRY_STYLE='name', COUNTRY_ALIASES={'DE': 'Deutschland'})

	assert geo.normalize(module, ['Deutschland']) == ['DE']


def test_normalize_iso2_and_unmatched_tags():
	module = board(COUNTRY_STYLE='iso2')

	assert geo.normalize(module, ['ng', 'GB', 'remote']) == ['NG', 'GB', 'remote']


def test_normalize_resolves_sub_region_wordings_to_their_region():
	"""Re-keyed board tags must fold back to the canonical region, not
	be left as free text or widened to a parent."""
	module = board(COUNTRY_STYLE='none')

	assert geo.normalize(module, ['Western Europe', 'sub-saharan africa']) == [
		'western-europe',
		'sub-saharan-africa',
	]
	assert geo.canonical('Southern Europe') == 'southern-europe'
	assert geo.canonical('Central Europe') == 'central-europe'


def test_normalize_without_declarations_is_best_effort():
	assert geo.normalize(board(), ['Worldwide', 'uk', 'Mars']) == ['worldwide', 'GB', 'Mars']


# -- suggestions -------------------------------------------------------


def test_suggestions_find_the_canonical_token():
	assert geo.suggestions('emae')[0] == 'emea'
	assert geo.suggestions('Ngeria')[0] == 'NG'
	assert geo.suggestions('phillipines')[0] == 'PH'
	assert geo.suggestions('qwerty') == []
