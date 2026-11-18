"""The canonical geography vocabulary, and the translation every board needs.

A person has one location preference ("worldwide, EMEA, Nigeria") but
each board spells the places differently: `'UK'` on remotive,
`'United Kingdom'` on wellfound, `'NG'` on dynamitejobs, slugs like
`remote-jobs-emea` on euremotejobs. This module owns the one vocabulary
in the middle:

* `worldwide` — open to anyone, anywhere;
* regions (`emea`, `europe`, `eu`, `uk-ireland`, `nordics`, `benelux`,
  `western-europe`, `eastern-europe`, `southern-europe`,
  `northern-europe`, `central-europe`, `middle-east`, `africa`,
  `sub-saharan-africa`, `americas`, `north-america`, `latam`,
  `caribbean`, `apac`, `asia`, `oceania`);
* countries as ISO 3166-1 alpha-2 codes, with display names and the
  aliases people and boards actually type ("UK", "USA", "Türkiye",
  "Viet Nam").

The tables are vendored rather than a dependency: about 250 countries
and 21 regions fit in this one file and never break, while a package
whose vocabulary can change under us would be a new failure mode in
the middle of a scrape.

Three sides of the translation:

* `expand(prefs)` resolves a preference list to canonical tokens and
  flattens regions into their member countries. It is what the central
  filter and the "does this board cover anything I want?" check read.
* `board_tags(module, prefs)` feeds a board: a scraper declares its
  wording as data (`WORLDWIDE`, `REGIONS`, `COUNTRY_STYLE`,
  `COUNTRY_ALIASES`) and gets back the exact tuple its `recent_jobs`
  already accepts. The model is containment: a job matches when its
  stated area *covers* one of the user's locations — "a job must be
  open to where I am, or wider". A region token asks for everything
  that covers the whole region, a country token for everything that
  covers the country. A board that declares nothing returns `()`: the
  pipeline reads that as `locations=None`, meaning the board fetches
  everything and the central filter does the narrowing.
* `normalize(module, tags)` is the inverse, best effort: raw board
  tags become canonical codes/regions so the store can keep canonical
  locations beside the original tags. A tag we cannot place is kept
  as written rather than dropped — losing it would lose the only clue
  about where the job is.

Everything is case-insensitive and forgiving about punctuation
(`_fold`), because "UK", "uk" and "U.K." are one place and no board
has ever promised to be consistent.
"""

import difflib
from collections.abc import Mapping

WORLDWIDE = 'worldwide'

# ---------------------------------------------------------------------------
# Countries: ISO 3166-1 alpha-2 -> the name the user sees and the tables
# store. The list is the full standard (249 codes, plus XK for Kosovo,
# which is not ISO but is what boards and people write).
# ---------------------------------------------------------------------------

COUNTRIES: dict[str, str] = {
	'AD': 'Andorra',
	'AE': 'United Arab Emirates',
	'AF': 'Afghanistan',
	'AG': 'Antigua and Barbuda',
	'AI': 'Anguilla',
	'AL': 'Albania',
	'AM': 'Armenia',
	'AO': 'Angola',
	'AQ': 'Antarctica',
	'AR': 'Argentina',
	'AS': 'American Samoa',
	'AT': 'Austria',
	'AU': 'Australia',
	'AW': 'Aruba',
	'AX': 'Åland Islands',
	'AZ': 'Azerbaijan',
	'BA': 'Bosnia and Herzegovina',
	'BB': 'Barbados',
	'BD': 'Bangladesh',
	'BE': 'Belgium',
	'BF': 'Burkina Faso',
	'BG': 'Bulgaria',
	'BH': 'Bahrain',
	'BI': 'Burundi',
	'BJ': 'Benin',
	'BL': 'Saint Barthélemy',
	'BM': 'Bermuda',
	'BN': 'Brunei',
	'BO': 'Bolivia',
	'BQ': 'Caribbean Netherlands',
	'BR': 'Brazil',
	'BS': 'Bahamas',
	'BT': 'Bhutan',
	'BV': 'Bouvet Island',
	'BW': 'Botswana',
	'BY': 'Belarus',
	'BZ': 'Belize',
	'CA': 'Canada',
	'CC': 'Cocos (Keeling) Islands',
	'CD': 'DR Congo',
	'CF': 'Central African Republic',
	'CG': 'Republic of the Congo',
	'CH': 'Switzerland',
	'CI': "Côte d'Ivoire",
	'CK': 'Cook Islands',
	'CL': 'Chile',
	'CM': 'Cameroon',
	'CN': 'China',
	'CO': 'Colombia',
	'CR': 'Costa Rica',
	'CU': 'Cuba',
	'CV': 'Cabo Verde',
	'CW': 'Curaçao',
	'CX': 'Christmas Island',
	'CY': 'Cyprus',
	'CZ': 'Czechia',
	'DE': 'Germany',
	'DJ': 'Djibouti',
	'DK': 'Denmark',
	'DM': 'Dominica',
	'DO': 'Dominican Republic',
	'DZ': 'Algeria',
	'EC': 'Ecuador',
	'EE': 'Estonia',
	'EG': 'Egypt',
	'EH': 'Western Sahara',
	'ER': 'Eritrea',
	'ES': 'Spain',
	'ET': 'Ethiopia',
	'FI': 'Finland',
	'FJ': 'Fiji',
	'FK': 'Falkland Islands',
	'FM': 'Micronesia',
	'FO': 'Faroe Islands',
	'FR': 'France',
	'GA': 'Gabon',
	'GB': 'United Kingdom',
	'GD': 'Grenada',
	'GE': 'Georgia',
	'GF': 'French Guiana',
	'GG': 'Guernsey',
	'GH': 'Ghana',
	'GI': 'Gibraltar',
	'GL': 'Greenland',
	'GM': 'Gambia',
	'GN': 'Guinea',
	'GP': 'Guadeloupe',
	'GQ': 'Equatorial Guinea',
	'GR': 'Greece',
	'GS': 'South Georgia and the South Sandwich Islands',
	'GT': 'Guatemala',
	'GU': 'Guam',
	'GW': 'Guinea-Bissau',
	'GY': 'Guyana',
	'HK': 'Hong Kong',
	'HM': 'Heard Island and McDonald Islands',
	'HN': 'Honduras',
	'HR': 'Croatia',
	'HT': 'Haiti',
	'HU': 'Hungary',
	'ID': 'Indonesia',
	'IE': 'Ireland',
	'IL': 'Israel',
	'IM': 'Isle of Man',
	'IN': 'India',
	'IO': 'British Indian Ocean Territory',
	'IQ': 'Iraq',
	'IR': 'Iran',
	'IS': 'Iceland',
	'IT': 'Italy',
	'JE': 'Jersey',
	'JM': 'Jamaica',
	'JO': 'Jordan',
	'JP': 'Japan',
	'KE': 'Kenya',
	'KG': 'Kyrgyzstan',
	'KH': 'Cambodia',
	'KI': 'Kiribati',
	'KM': 'Comoros',
	'KN': 'Saint Kitts and Nevis',
	'KP': 'North Korea',
	'KR': 'South Korea',
	'KW': 'Kuwait',
	'KY': 'Cayman Islands',
	'KZ': 'Kazakhstan',
	'LA': 'Laos',
	'LB': 'Lebanon',
	'LC': 'Saint Lucia',
	'LI': 'Liechtenstein',
	'LK': 'Sri Lanka',
	'LR': 'Liberia',
	'LS': 'Lesotho',
	'LT': 'Lithuania',
	'LU': 'Luxembourg',
	'LV': 'Latvia',
	'LY': 'Libya',
	'MA': 'Morocco',
	'MC': 'Monaco',
	'MD': 'Moldova',
	'ME': 'Montenegro',
	'MF': 'Saint Martin',
	'MG': 'Madagascar',
	'MH': 'Marshall Islands',
	'MK': 'North Macedonia',
	'ML': 'Mali',
	'MM': 'Myanmar',
	'MN': 'Mongolia',
	'MO': 'Macao',
	'MP': 'Northern Mariana Islands',
	'MQ': 'Martinique',
	'MR': 'Mauritania',
	'MS': 'Montserrat',
	'MT': 'Malta',
	'MU': 'Mauritius',
	'MV': 'Maldives',
	'MW': 'Malawi',
	'MX': 'Mexico',
	'MY': 'Malaysia',
	'MZ': 'Mozambique',
	'NA': 'Namibia',
	'NC': 'New Caledonia',
	'NE': 'Niger',
	'NF': 'Norfolk Island',
	'NG': 'Nigeria',
	'NI': 'Nicaragua',
	'NL': 'Netherlands',
	'NO': 'Norway',
	'NP': 'Nepal',
	'NR': 'Nauru',
	'NU': 'Niue',
	'NZ': 'New Zealand',
	'OM': 'Oman',
	'PA': 'Panama',
	'PE': 'Peru',
	'PF': 'French Polynesia',
	'PG': 'Papua New Guinea',
	'PH': 'Philippines',
	'PK': 'Pakistan',
	'PL': 'Poland',
	'PM': 'Saint Pierre and Miquelon',
	'PN': 'Pitcairn Islands',
	'PR': 'Puerto Rico',
	'PS': 'Palestine',
	'PT': 'Portugal',
	'PW': 'Palau',
	'PY': 'Paraguay',
	'QA': 'Qatar',
	'RE': 'Réunion',
	'RO': 'Romania',
	'RS': 'Serbia',
	'RU': 'Russia',
	'RW': 'Rwanda',
	'SA': 'Saudi Arabia',
	'SB': 'Solomon Islands',
	'SC': 'Seychelles',
	'SD': 'Sudan',
	'SE': 'Sweden',
	'SG': 'Singapore',
	'SH': 'Saint Helena',
	'SI': 'Slovenia',
	'SJ': 'Svalbard and Jan Mayen',
	'SK': 'Slovakia',
	'SL': 'Sierra Leone',
	'SM': 'San Marino',
	'SN': 'Senegal',
	'SO': 'Somalia',
	'SR': 'Suriname',
	'SS': 'South Sudan',
	'ST': 'São Tomé and Príncipe',
	'SV': 'El Salvador',
	'SX': 'Sint Maarten',
	'SY': 'Syria',
	'SZ': 'Eswatini',
	'TC': 'Turks and Caicos Islands',
	'TD': 'Chad',
	'TF': 'French Southern Territories',
	'TG': 'Togo',
	'TH': 'Thailand',
	'TJ': 'Tajikistan',
	'TK': 'Tokelau',
	'TL': 'Timor-Leste',
	'TM': 'Turkmenistan',
	'TN': 'Tunisia',
	'TO': 'Tonga',
	'TR': 'Türkiye',
	'TT': 'Trinidad and Tobago',
	'TV': 'Tuvalu',
	'TW': 'Taiwan',
	'TZ': 'Tanzania',
	'UA': 'Ukraine',
	'UG': 'Uganda',
	'UM': 'United States Minor Outlying Islands',
	'US': 'United States',
	'UY': 'Uruguay',
	'UZ': 'Uzbekistan',
	'VA': 'Vatican City',
	'VC': 'Saint Vincent and the Grenadines',
	'VE': 'Venezuela',
	'VG': 'British Virgin Islands',
	'VI': 'U.S. Virgin Islands',
	'VN': 'Vietnam',
	'VU': 'Vanuatu',
	'WF': 'Wallis and Futuna',
	'WS': 'Samoa',
	'YE': 'Yemen',
	'YT': 'Mayotte',
	'ZA': 'South Africa',
	'ZM': 'Zambia',
	'ZW': 'Zimbabwe',
	'XK': 'Kosovo',
}

# The names people and boards use that are not the display name. Keys are
# written naturally and folded at import; the obvious variants (the ISO
# code itself, the display name) are built in and need no entry here.
#
# This is the reading direction: spelling -> ISO code. A scraper's
# `COUNTRY_ALIASES` declaration is the other direction, ISO code ->
# board wording, and an empty wording there means "this board has no tag
# for that country" — boards cover partial regions, so an empty alias is
# how a declaration stays precise instead of widening to every country.
# See `_declarations` for the convention.
COUNTRY_ALIASES: dict[str, str] = {
	'united states of america': 'US',
	'usa': 'US',
	'u s a': 'US',
	'u s': 'US',
	'u s of a': 'US',
	'great britain': 'GB',
	'britain': 'GB',
	'uk': 'GB',
	'u k': 'GB',
	'england': 'GB',
	'scotland': 'GB',
	'wales': 'GB',
	'northern ireland': 'GB',
	'united kingdom of great britain and northern ireland': 'GB',
	'czech republic': 'CZ',
	'czech rep': 'CZ',
	'turkey': 'TR',
	'turkiye': 'TR',
	'south korea': 'KR',
	'korea': 'KR',
	'korea south': 'KR',
	'republic of korea': 'KR',
	'korea republic of': 'KR',
	'north korea': 'KP',
	'korea democratic peoples republic of': 'KP',
	'dprk': 'KP',
	'uae': 'AE',
	'u a e': 'AE',
	'emirates': 'AE',
	'russia': 'RU',
	'russian federation': 'RU',
	'ivory coast': 'CI',
	"cote d'ivoire": 'CI',
	'cote d ivoire': 'CI',
	'cote divoire': 'CI',
	'holland': 'NL',
	'the netherlands': 'NL',
	'republic of ireland': 'IE',
	'eire': 'IE',
	'viet nam': 'VN',
	'burma': 'MM',
	'swaziland': 'SZ',
	'macedonia': 'MK',
	'cape verde': 'CV',
	'east timor': 'TL',
	'macau': 'MO',
	'macao sar': 'MO',
	'macau sar': 'MO',
	'hong kong sar': 'HK',
	'hong kong sar china': 'HK',
	'taiwan province of china': 'TW',
	'lao': 'LA',
	'lao pdr': 'LA',
	'brunei darussalam': 'BN',
	'syrian arab republic': 'SY',
	'iran islamic republic of': 'IR',
	'tanzania united republic of': 'TZ',
	'bolivia plurinational state of': 'BO',
	'venezuela bolivarian republic of': 'VE',
	'moldova republic of': 'MD',
	'micronesia federated states of': 'FM',
	'palestine state of': 'PS',
	'palestinian territories': 'PS',
	'vatican': 'VA',
	'holy see': 'VA',
	'democratic republic of the congo': 'CD',
	'democratic republic of congo': 'CD',
	'dr congo': 'CD',
	'drc': 'CD',
	'congo democratic republic of the': 'CD',
	'congo the democratic republic of the': 'CD',
	'republic of the congo': 'CG',
	'congo republic of the': 'CG',
	'congo brazzaville': 'CG',
	'central african rep': 'CF',
	'dominican rep': 'DO',
	'st lucia': 'LC',
	'st kitts and nevis': 'KN',
	'st vincent and the grenadines': 'VC',
	'st vincent': 'VC',
	'st barthelemy': 'BL',
	'st barthélemy': 'BL',
	'st martin': 'MF',
	'saint martin french part': 'MF',
	'st maarten': 'SX',
	'sint maarten dutch part': 'SX',
	'curacao': 'CW',
	'the bahamas': 'BS',
	'the gambia': 'GM',
	'st pierre and miquelon': 'PM',
	'st helena': 'SH',
	'falklands': 'FK',
	'reunion': 'RE',
	'aland': 'AX',
	'aland islands': 'AX',
	'svalbard': 'SJ',
	'jan mayen': 'SJ',
	'kosovo': 'XK',
	'us minor outlying islands': 'UM',
	'u s virgin islands': 'VI',
	'us virgin islands': 'VI',
	'british virgin islands': 'VG',
	'bvi': 'VG',
	'cocos islands': 'CC',
	'keeling islands': 'CC',
	'cabo verde': 'CV',
	'sao tome and principe': 'ST',
	'timor leste': 'TL',
	'south georgia': 'GS',
	'heard island': 'HM',
	'pitcairn islands': 'PN',
	'wallis and futuna': 'WF',
	'french southern territories': 'TF',
	'bouvet island': 'BV',
	'netherlands antilles': 'BQ',
}

# ---------------------------------------------------------------------------
# Regions and their members. Groups overlap deliberately (Cyprus and
# Türkiye are Europe and the Middle East; Mexico is North America and
# LatAm); a place can belong to several ways of grouping the world.
# ---------------------------------------------------------------------------

_EUROPE = (
	'AD',
	'AL',
	'AM',
	'AT',
	'AX',
	'AZ',
	'BA',
	'BE',
	'BG',
	'BY',
	'CH',
	'CY',
	'CZ',
	'DE',
	'DK',
	'EE',
	'ES',
	'FI',
	'FO',
	'FR',
	'GB',
	'GE',
	'GG',
	'GI',
	'GR',
	'HR',
	'HU',
	'IE',
	'IM',
	'IS',
	'IT',
	'JE',
	'LI',
	'LT',
	'LU',
	'LV',
	'MC',
	'MD',
	'ME',
	'MK',
	'MT',
	'NL',
	'NO',
	'PL',
	'PT',
	'RO',
	'RS',
	'RU',
	'SE',
	'SI',
	'SJ',
	'SK',
	'SM',
	'TR',
	'UA',
	'VA',
	'XK',
)

_EU = (
	'AT',
	'BE',
	'BG',
	'CY',
	'CZ',
	'DE',
	'DK',
	'EE',
	'ES',
	'FI',
	'FR',
	'GR',
	'HR',
	'HU',
	'IE',
	'IT',
	'LT',
	'LU',
	'LV',
	'MT',
	'NL',
	'PL',
	'PT',
	'RO',
	'SE',
	'SI',
	'SK',
)

_UK_IRELAND = ('GB', 'IE')
_NORDICS = ('DK', 'FI', 'IS', 'NO', 'SE')
_BENELUX = ('BE', 'LU', 'NL')

# Europe's sub-regions, following the UN's M49 grouping with a
# `central-europe` bucket added because boards use that word. They
# overlap each other on purpose — Czechia is central *and* eastern,
# Britain is western *and* northern — and they all sit inside `_EUROPE`:
# the containment model walks *supersets*, so a table that leaked a
# country outside Europe would make a region tag claim an area it does
# not have. The test suite pins every nesting pair.
_WESTERN_EUROPE = ('AT', 'BE', 'CH', 'DE', 'FR', 'GB', 'IE', 'LI', 'LU', 'MC', 'NL')
_EASTERN_EUROPE = ('BG', 'BY', 'CZ', 'HU', 'MD', 'PL', 'RO', 'RU', 'SK', 'UA')
_SOUTHERN_EUROPE = (
	'AD',
	'AL',
	'BA',
	'CY',
	'ES',
	'GI',
	'GR',
	'HR',
	'IT',
	'ME',
	'MK',
	'MT',
	'PT',
	'RS',
	'SI',
	'SM',
	'VA',
	'XK',
)
_NORTHERN_EUROPE = (
	'AX',
	'DK',
	'EE',
	'FI',
	'FO',
	'GB',
	'GG',
	'IE',
	'IM',
	'IS',
	'JE',
	'LT',
	'LV',
	'NO',
	'SE',
	'SJ',
)
_CENTRAL_EUROPE = ('AT', 'CH', 'CZ', 'DE', 'HU', 'PL', 'SI', 'SK')

_MIDDLE_EAST = (
	'AE',
	'BH',
	'CY',
	'EG',
	'IL',
	'IQ',
	'IR',
	'JO',
	'KW',
	'LB',
	'OM',
	'PS',
	'QA',
	'SA',
	'SY',
	'TR',
	'YE',
)

_AFRICA = (
	'AO',
	'BF',
	'BI',
	'BJ',
	'BW',
	'CD',
	'CF',
	'CG',
	'CI',
	'CM',
	'CV',
	'DJ',
	'DZ',
	'EG',
	'EH',
	'ER',
	'ET',
	'GA',
	'GH',
	'GM',
	'GN',
	'GQ',
	'GW',
	'KE',
	'KM',
	'LR',
	'LS',
	'LY',
	'MA',
	'MG',
	'ML',
	'MR',
	'MU',
	'MW',
	'MZ',
	'NA',
	'NE',
	'NG',
	'RE',
	'RW',
	'SC',
	'SD',
	'SH',
	'SL',
	'SN',
	'SO',
	'SS',
	'ST',
	'SZ',
	'TD',
	'TG',
	'TN',
	'TZ',
	'UG',
	'YT',
	'ZA',
	'ZM',
	'ZW',
)

# Africa below the Sahara, UN style: the continent minus Northern Africa
# (Algeria, Egypt, Libya, Morocco, Sudan, Tunisia, Western Sahara). NG
# is the country this table exists for; it must be a strict subset of
# `_AFRICA` or an "Africa" tag would fail to cover a sub-Saharan user.
_NORTH_AFRICA = ('DZ', 'EG', 'EH', 'LY', 'MA', 'SD', 'TN')
_SUB_SAHARAN_AFRICA = tuple(code for code in _AFRICA if code not in _NORTH_AFRICA)

_NORTH_AMERICA = ('CA', 'MX', 'US')

_CENTRAL_AMERICA = ('BZ', 'CR', 'GT', 'HN', 'NI', 'PA', 'SV')
_CARIBBEAN = (
	'AG',
	'AI',
	'AW',
	'BB',
	'BL',
	'BQ',
	'BS',
	'CU',
	'CW',
	'DM',
	'DO',
	'GD',
	'GP',
	'HT',
	'JM',
	'KN',
	'KY',
	'LC',
	'MF',
	'MQ',
	'MS',
	'PR',
	'SX',
	'TC',
	'TT',
	'VC',
	'VG',
	'VI',
)
_SOUTH_AMERICA = (
	'AR',
	'BO',
	'BR',
	'CL',
	'CO',
	'EC',
	'FK',
	'GF',
	'GY',
	'PE',
	'PY',
	'SR',
	'UY',
	'VE',
)
_LATAM = ('MX', *_CENTRAL_AMERICA, *_SOUTH_AMERICA, *_CARIBBEAN)
_AMERICAS = (*_NORTH_AMERICA, *_LATAM, 'BM', 'GL', 'PM')

# Asia here is Asia outside the Middle East, because `middle-east` is a
# sibling region and a job board that says "Asia" almost never means
# Dubai. APAC is that Asia plus Oceania.
_ASIA = (
	'AF',
	'AM',
	'AZ',
	'BD',
	'BN',
	'BT',
	'CN',
	'GE',
	'HK',
	'ID',
	'IN',
	'JP',
	'KG',
	'KH',
	'KP',
	'KR',
	'KZ',
	'LA',
	'LK',
	'MM',
	'MN',
	'MO',
	'MV',
	'MY',
	'NP',
	'PH',
	'PK',
	'SG',
	'TH',
	'TJ',
	'TL',
	'TM',
	'TW',
	'UZ',
	'VN',
)

_OCEANIA = (
	'AS',
	'AU',
	'CC',
	'CK',
	'CX',
	'FJ',
	'FM',
	'GU',
	'KI',
	'MH',
	'MP',
	'NC',
	'NF',
	'NR',
	'NU',
	'NZ',
	'PF',
	'PG',
	'PN',
	'PW',
	'SB',
	'TK',
	'TO',
	'TV',
	'VU',
	'WF',
	'WS',
)


def _merged(*groups: tuple[str, ...]) -> tuple[str, ...]:
	"""The union of country groups, sorted, one entry per country."""
	return tuple(sorted(set().union(*groups)))


REGIONS: dict[str, tuple[str, ...]] = {
	'emea': _merged(_EUROPE, _MIDDLE_EAST, _AFRICA),
	'europe': _merged(_EUROPE),
	'eu': _merged(_EU),
	'uk-ireland': _merged(_UK_IRELAND),
	'nordics': _merged(_NORDICS),
	'benelux': _merged(_BENELUX),
	'western-europe': _merged(_WESTERN_EUROPE),
	'eastern-europe': _merged(_EASTERN_EUROPE),
	'southern-europe': _merged(_SOUTHERN_EUROPE),
	'northern-europe': _merged(_NORTHERN_EUROPE),
	'central-europe': _merged(_CENTRAL_EUROPE),
	'middle-east': _merged(_MIDDLE_EAST),
	'africa': _merged(_AFRICA),
	'sub-saharan-africa': _merged(_SUB_SAHARAN_AFRICA),
	'americas': _merged(_AMERICAS),
	'north-america': _merged(_NORTH_AMERICA),
	'latam': _merged(_LATAM),
	'caribbean': _merged(_CARIBBEAN),
	'apac': _merged(_ASIA, _OCEANIA),
	'asia': _merged(_ASIA),
	'oceania': _merged(_OCEANIA),
}

# The same members as sets, built once: `board_tags` asks "does this
# declared region contain the requested one?" on every call, and 21
# frozenset comparisons are cheaper than rebuilding tuples each time.
_MEMBERS: dict[str, frozenset[str]] = {
	region: frozenset(members) for region, members in REGIONS.items()
}

# What people and boards call the regions. The canonical names are keys
# here too, so `_LOOKUP` is built one way for both.
REGION_ALIASES: dict[str, str] = {
	'emea': 'emea',
	'europe': 'europe',
	'eu': 'eu',
	'european union': 'eu',
	'uk-ireland': 'uk-ireland',
	'uk & ireland': 'uk-ireland',
	'uk and ireland': 'uk-ireland',
	'uki': 'uk-ireland',
	'nordics': 'nordics',
	'nordic': 'nordics',
	'benelux': 'benelux',
	'western-europe': 'western-europe',
	'western europe': 'western-europe',
	'eastern-europe': 'eastern-europe',
	'eastern europe': 'eastern-europe',
	'southern-europe': 'southern-europe',
	'southern europe': 'southern-europe',
	'northern-europe': 'northern-europe',
	'northern europe': 'northern-europe',
	'central-europe': 'central-europe',
	'central europe': 'central-europe',
	'middle-east': 'middle-east',
	'middle east': 'middle-east',
	'mena': 'middle-east',  # Middle East + North Africa, approximated
	'africa': 'africa',
	'sub-saharan-africa': 'sub-saharan-africa',
	'sub saharan africa': 'sub-saharan-africa',
	'subsaharan africa': 'sub-saharan-africa',
	'americas': 'americas',
	'north-america': 'north-america',
	'north america': 'north-america',
	'latam': 'latam',
	'latin america': 'latam',
	'south america': 'latam',  # the boards that say this mean the LatAm bucket
	'caribbean': 'caribbean',
	'apac': 'apac',
	'asia pacific': 'apac',
	'asia-pacific': 'apac',
	'asia': 'asia',
	'oceania': 'oceania',
	'anz': 'oceania',
}

_WORLDWIDE_ALIASES = ('worldwide', 'world wide', 'anywhere', 'global', 'any location')


def _fold(value: str) -> str:
	"""The one spelling used for matching: casefolded, punctuation and
	runs of whitespace normalised. "U.K.", "uk" and "UK" are one place;
	"Middle-East" and "middle east" are one region.

	Apostrophes are kept because they are part of names
	("Côte d'Ivoire"); hyphens, periods, commas and slashes become
	spaces because no two sources agree on them.
	"""
	text = value.casefold()
	text = text.replace('’', "'").replace('–', '-').replace('—', '-')
	text = text.replace('&', ' and ')
	for punctuation in ('.', ',', '-', '/', '(', ')', ';', ':'):
		text = text.replace(punctuation, ' ')
	return ' '.join(text.split())


# Every folded spelling -> the canonical token it means. Insertion order
# matters only for suggestions, which prefer the earlier entry when two
# candidates are equally close.
_LOOKUP: dict[str, str] = {}
for _alias in _WORLDWIDE_ALIASES:
	_LOOKUP[_fold(_alias)] = WORLDWIDE
for _alias, _region in REGION_ALIASES.items():
	_LOOKUP[_fold(_alias)] = _region
for _code, _name in COUNTRIES.items():
	_LOOKUP[_fold(_code)] = _code
	_LOOKUP[_fold(_name)] = _code
for _alias, _code in COUNTRY_ALIASES.items():
	_LOOKUP[_fold(_alias)] = _code


class UnknownLocation(ValueError):
	"""A location preference this vocabulary cannot place.

	Its own class so the config loader can catch it, name the key and
	the position, and add the same difflib suggestion the message
	already carries. str() is the full human sentence.
	"""

	def __init__(self, value: object):
		if isinstance(value, str):
			matches = suggestions(value)
			hint = f' (did you mean {matches[0]!r}?)' if matches else ''
			super().__init__(f'unknown location {value!r}{hint}')
		else:
			super().__init__(f'unknown location {value!r}: expected a string')


def canonical(value: str) -> str | None:
	"""One location token as the canonical code or region, or None.

	Accepts the canonical spellings and every alias in the tables:
	"uk" -> "GB", "United Kingdom" -> "GB", "EMEA" -> "emea",
	"global" -> "worldwide". Returns None (rather than raising) so the
	config loader can build a message with its own file/key prefix.
	"""
	if not isinstance(value, str):
		return None
	return _LOOKUP.get(_fold(value))


def suggestions(value: str, limit: int = 3) -> list[str]:
	"""The canonical tokens closest to a misspelling, best first.

	Difflib over every code, display name, alias and region — the same
	table `canonical` reads, so a suggestion is always a value that
	will actually resolve. Region typos come back as the region
	("emae" -> "emea"); country typos come back as the code
	("Ngeria" -> "NG"), which is what the config file wants written.
	"""
	if not isinstance(value, str):
		return []
	text = _fold(value)
	matches = difflib.get_close_matches(text, list(_LOOKUP), n=max(limit * 3, 1), cutoff=0.7)
	found: list[str] = []
	for match in matches:
		token = _LOOKUP[match]
		if token not in found:
			found.append(token)
		if len(found) == limit:
			break
	return found


def _prefs_values(prefs: object) -> list[object]:
	"""The location list from a prefs object, a sequence or one string.

	`SearchSettings` is duck-typed (`.locations`) so geo never has to
	import config; that keeps the dependency one-way.
	"""
	if prefs is None:
		return []
	if isinstance(prefs, str):
		return [prefs]
	locations = getattr(prefs, 'locations', None)
	if locations is not None:
		return list(locations)
	return list(prefs)  # a plain sequence; a non-iterable raises TypeError


def _tokens(prefs: object) -> list[str]:
	"""The preference list as canonical tokens, in the user's order,
	deduplicated. Raises UnknownLocation on the first value that does
	not resolve."""
	tokens: list[str] = []
	for value in _prefs_values(prefs):
		token = canonical(value) if isinstance(value, str) else None
		if token is None:
			raise UnknownLocation(value)
		if token not in tokens:
			tokens.append(token)
	return tokens


def expand(prefs: object) -> frozenset[str]:
	"""Every canonical place a preference list covers.

	Regions are flattened: `['emea']` contains `'emea'` and every EMEA
	country code, so central code can answer "is this job in one of my
	countries?" without knowing the membership tables. `'worldwide'`
	adds no countries — it means "anywhere", not "every country listed
	one by one".
	"""
	tokens = _tokens(prefs)
	expanded = set(tokens)
	for token in tokens:
		if token in REGIONS:
			expanded.update(REGIONS[token])
	return frozenset(expanded)


def _tags(value: object) -> tuple[str, ...]:
	"""A declaration as a tuple of tags; a bare string is one tag."""
	if value is None:
		return ()
	if isinstance(value, str):
		return (value,)
	return tuple(str(tag) for tag in value)


def _declarations(
	module: object,
) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]], str, dict[str, tuple[str, ...]]] | None:
	"""A scraper's location declarations, normalised.

	None means the module declared nothing at all: the pipeline reads
	that as "this board cannot filter by location", passes `None`, and
	the central filter applies. A module that declares only some of
	the four attributes gets defaults for the rest (`WORLDWIDE=()`,
	`REGIONS={}`, `COUNTRY_STYLE='none'`, `COUNTRY_ALIASES={}`).

	`COUNTRY_ALIASES` is ISO code -> the board's wording, as one
	string or a tuple of strings (a board can have two words for one
	country — wellfound writes Czechia *and* Czech Republic). Every
	wording is emitted by `board_tags` and each folds back to the code
	in `normalize`. An empty value (or empty tuple) suppresses that
	country: `add()` skips falsy tags, so the board carries no tag for
	it. That is the convention that keeps a partial board exact — most
	boards serve a subset of EMEA and need `COUNTRY_STYLE='name'`/
	`'iso2'` only for the countries they list, not for the whole
	region. A typical declaration is

	    _WORDING = {'GB': 'UK', 'DE': 'Germany'}       # countries it has
	    COUNTRY_ALIASES = {code: _WORDING.get(code, '') for code in geo.REGIONS['emea']}

	which tags the two countries it knows and nothing else, in either
	country style. A code left out of `_WORDING` is suppressed, not
	filled in with its display name: "no wording = no tag for this
	board".
	"""
	if not any(
		hasattr(module, name) for name in ('WORLDWIDE', 'REGIONS', 'COUNTRY_STYLE', 'COUNTRY_ALIASES')
	):
		return None

	worldwide = _tags(getattr(module, 'WORLDWIDE', ()))

	raw_regions = getattr(module, 'REGIONS', {}) or {}
	if not isinstance(raw_regions, Mapping):
		raise TypeError(f'{module!r}: REGIONS must be a table of region -> tags')
	regions: dict[str, tuple[str, ...]] = {}
	for region, tags in raw_regions.items():
		key = canonical(str(region))
		if key is None or key not in REGIONS:
			raise ValueError(f'{module!r}: REGIONS has unknown region {region!r}')
		regions[key] = _tags(tags)

	style = getattr(module, 'COUNTRY_STYLE', 'none')
	if style not in ('name', 'iso2', 'none'):
		raise ValueError(f'{module!r}: COUNTRY_STYLE = {style!r} is not name, iso2 or none')

	raw_aliases = getattr(module, 'COUNTRY_ALIASES', {}) or {}
	if not isinstance(raw_aliases, Mapping):
		raise TypeError(f'{module!r}: COUNTRY_ALIASES must be a table of ISO code -> wording')
	aliases = {str(code).upper(): _tags(wording) for code, wording in raw_aliases.items()}
	return worldwide, regions, style, aliases


def _country_tags(code: str, style: str, aliases: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
	"""The board's own wordings for one country, or none.

	An explicit alias wins, including an empty one: that is the "this
	board has no tag for this country" convention, and it must not
	fall back to an invented display name or code. Without an alias the
	country style decides: display name, ISO code, or nothing.
	"""
	if code in aliases:
		return aliases[code]
	if style == 'name':
		return (COUNTRIES[code],)
	if style == 'iso2':
		return (code,)
	return ()


def board_tags(module: object, prefs: object) -> tuple[str, ...]:
	"""The exact `locations` tuple a board's scraper accepts.

	The model is containment: a job matches when its stated area covers
	one of the user's locations — "a job must be open to where I am, or
	wider". Three kinds of token, three rules:

	1. `worldwide` — the module's `WORLDWIDE` tags. A region or country
	   preference includes them too: a job open to anyone is open to
	   that region or country, and the worldwide tag is the board's only
	   word for "anywhere". Every non-empty preference therefore starts
	   with the worldwide tags.
	2. a region — the board's tags for every declared region whose
	   member set *contains* the requested region's member set. A tag
	   declared under `REGIONS[R]` claims area R, so it covers a
	   requested region only when it covers all of it: `europe` also
	   asks for the board's `EMEA` tag (EMEA ⊇ Europe), but `emea` does
	   *not* ask for its `Europe` tag (Europe ⊉ EMEA). Member countries'
	   wordings are deliberately not asked for: a job open only in
	   Germany does not cover someone who can work from all of Europe.
	3. a country — the board's tags for every declared region whose
	   membership table contains the country, then the country's own
	   wording. Nigeria is in EMEA, Africa and Sub-Saharan Africa, so a
	   job open across any of them is open to Nigeria; a region that
	   contains none of the requested countries contributes nothing.

	The order is fixed and documented so the tuple is stable for a
	given set of declarations: worldwide tags, then the superset-region
	tags for requested region tokens, then the containing-region tags
	for requested country tokens, then the country wordings. Both
	region walks follow the `REGIONS` table's own (canonical) order —
	never the module's declaration order or the user's token order —
	and only the country wordings keep the user's order.

	A country's own tags are its `COUNTRY_ALIASES` entry if it has one
	(a tuple emits every wording), otherwise the display name
	(`COUNTRY_STYLE='name'`) or the ISO code (`COUNTRY_STYLE='iso2'`);
	`'none'` adds nothing. An empty `COUNTRY_ALIASES` entry suppresses
	that country's wording — the board has no word for it, so no tag is
	invented — but not the region tags that contain it.

	Duplicate tags are removed case-insensitively, first spelling wins.
	An empty tuple means the caller passes `locations=None` and the
	central filter narrows: either the module declared nothing, or it
	has no tag for anything the user asked for.
	"""
	tokens = _tokens(prefs)
	declarations = _declarations(module)
	if declarations is None:
		return ()
	worldwide, regions, style, aliases = declarations

	tags: list[str] = []
	seen: set[str] = set()

	def add(tag: str | None) -> None:
		if not tag:
			return
		text = tag.strip()
		key = text.casefold()
		if not text or key in seen:
			return
		seen.add(key)
		tags.append(text)

	requested_regions = [token for token in tokens if token in REGIONS]
	requested_countries = [token for token in tokens if token in COUNTRIES]

	# Worldwide tags come first for every non-empty preference: whatever
	# the user named, a job open anywhere is open to it.
	if tokens:
		for tag in worldwide:
			add(tag)

	# A requested region is covered by every declared region at least as
	# wide, and only by those. Canonical order makes the tuple stable.
	for region, members in _MEMBERS.items():
		if any(_MEMBERS[token] <= members for token in requested_regions):
			for tag in regions.get(region, ()):
				add(tag)

	# A country is contained in several regions (Nigeria is EMEA, Africa
	# and Sub-Saharan Africa), and a job open across any of them is open
	# to the country. Walked once, in canonical order, for the same
	# stability reason.
	for region, members in _MEMBERS.items():
		if any(token in members for token in requested_countries):
			for tag in regions.get(region, ()):
				add(tag)

	if style in ('name', 'iso2'):
		for token in requested_countries:
			for tag in _country_tags(token, style, aliases):
				add(tag)

	return tuple(tags)


def normalize(module: object, tags: object) -> list[str]:
	"""Raw board tags as canonical codes/regions, best effort.

	The inverse of `board_tags`, used at ingest so a job keeps
	canonical locations beside the board's wording. Matching is
	case-insensitive and tries, in order: the module's own `WORLDWIDE`
	and `REGIONS` tags, then every wording of its `COUNTRY_ALIASES`
	entries, then the global names and aliases — so a re-keyed
	sub-region tag like "Western Europe" resolves to `western-europe`,
	and either spelling of a tuple alias resolves to its country.

	A tag that still does not resolve is kept as written rather than
	dropped: it is the only evidence the job ever carried a location,
	and the raw tags are what the database stores anyway. The result is
	deduplicated case-insensitively, first spelling wins.
	"""
	declarations = _declarations(module)
	if declarations is None:
		worldwide, regions, aliases = (), {}, {}
	else:
		worldwide, regions, _style, aliases = declarations

	module_worldwide = {_fold(tag): WORLDWIDE for tag in worldwide}
	module_regions: dict[str, str] = {}
	for region, region_tags in regions.items():
		for tag in region_tags:
			# One tag belongs under one true area; if a declaration
			# reuses a wording across two regions, the first one wins
			# rather than the wording being dropped.
			module_regions.setdefault(_fold(tag), region)
	# Empty aliases mark a country the board has no tag for, so they
	# must not turn an empty/unplaceable tag into that country. A tuple
	# alias contributes one entry per wording, all folding back to the
	# same code ("Czechia" and "Czech Republic" both -> CZ).
	module_aliases = {
		_fold(tag): code for code, wordings in aliases.items() for tag in wordings if tag.strip()
	}

	resolved: list[str] = []
	seen: set[str] = set()
	for raw in _tags(tags):
		if not raw.strip():
			continue
		text = _fold(raw)
		token = (
			module_worldwide.get(text)
			or module_regions.get(text)
			or module_aliases.get(text)
			or canonical(raw)
		)
		if token is None:
			token = raw.strip()
		if not token or token.casefold() in seen:
			continue
		seen.add(token.casefold())
		resolved.append(token)
	return resolved
