"""The location vocabulary, re-exported for plugins.

Plan 03 gave every board one shared way to say where it serves: the
canonical tokens (`worldwide`, regions, ISO country codes), the
`COUNTRY_ALIASES` convention for a board's own wording, and
`geo.board_tags(module, prefs)` to turn the user's preferences into the
tags a board's `recent_jobs` accepts. A plugin needs all of that but
should not import the host's `jobtriage.geo` directly: the plugin
surface is what gets versioned, so it is re-exported here and stays
stable even if the host module moves.

Deliberately explicit rather than `import *`: the list below is the
public surface, and a name that is not in it is not promised.
"""

from jobtriage.geo import (
	COUNTRIES,
	COUNTRY_ALIASES,
	REGION_ALIASES,
	REGIONS,
	WORLDWIDE,
	UnknownLocation,
	board_tags,
	canonical,
	expand,
	normalize,
	suggestions,
)

__all__ = [
	'COUNTRIES',
	'COUNTRY_ALIASES',
	'REGIONS',
	'REGION_ALIASES',
	'WORLDWIDE',
	'UnknownLocation',
	'board_tags',
	'canonical',
	'expand',
	'normalize',
	'suggestions',
]
