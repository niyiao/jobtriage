"""Parsing helpers for the SSR state boards ship in their pages.

The research playbook (docs/scrapers.md) keeps finding the same three
shapes behind a listing page:

- a Next.js `<script id="__NEXT_DATA__">` holding the page's props
  (wellfound's whole Apollo cache);
- a `window.__X__ = {...}` assignment in an inline script — Remotive's
  `__INITIAL_SEARCH_RESULTS__`, justremote's `__PRELOADED_STATE__`,
  dynamitejobs' `__PINIA_STATE__`;
- markup that just needs its tags off and its entities decoded.

Each is a dozen lines to get wrong (attribute order, `</script>` inside
a JSON string, trailing semicolons), so they live here once. A helper
that cannot find its pattern raises ValueError naming what it looked
for: a board that changed its layout should say so, not return an empty
list that looks like a quiet day.
"""

import html
import json
import re

# Next.js writes `<script id="__NEXT_DATA__" type="application/json">`;
# build-to-build the attribute order varies, so id is found anywhere in
# the tag. Next escapes `</script>` inside the JSON, so the lazy body
# stops at the real closing tag.
_NEXT_DATA_RE = re.compile(
	r"""<script[^>]*\bid=["']__NEXT_DATA__["'][^>]*>(?P<json>.*?)</script>""",
	re.DOTALL,
)

# `window.__X__ = ` or a bare `__X__ = `. The name is escaped because a
# plugin may pass any variable, including one with dots or brackets.
_ASSIGN = r'(?:window\s*\.\s*)?{name}\s*='

_TAG_RE = re.compile(r'<[^>]*>')


def next_data(html_text: str) -> object:
	"""The parsed `__NEXT_DATA__` script, as the page wrote it.

	Raises ValueError when the tag is absent or its body is not JSON —
	both mean the board changed, which a scraper should surface.
	"""
	match = _NEXT_DATA_RE.search(html_text)
	if match is None:
		raise ValueError('no <script id="__NEXT_DATA__"> on the page; layout changed?')
	return _decode(match['json'], '__NEXT_DATA__')


def json_script(html_text: str, var: str) -> object:
	"""The JSON value assigned to `var` in an inline script.

	`var` is the variable name, with or without a `window.` prefix in
	the page (`json_script(page, '__PINIA_STATE__')`). The value is
	decoded with `json.JSONDecoder.raw_decode`, so the trailing `;`,
	whitespace and `</script>` that follow it do not matter, and a
	Nuxt-style `(function(){...})()` wrapper — which is JavaScript, not
	JSON — raises a clear error instead of returning half a value.
	"""
	pattern = _ASSIGN.format(name=re.escape(var))
	match = re.search(pattern, html_text)
	if match is None:
		raise ValueError(f'no {var} assignment on the page; layout changed?')
	return _decode_at(html_text, match.end(), var)


def html_text(markup: str) -> str:
	"""Tags off, entities decoded, whitespace collapsed.

	Enough for a title or company field in a server-rendered card:
	`'<b>Senior</b>&nbsp;Engineer'` becomes `'Senior Engineer'`.
	"""
	return ' '.join(html.unescape(_TAG_RE.sub(' ', markup)).split())


def _decode(text: str, label: str) -> object:
	"""`text` as JSON, with the label in the error message."""
	try:
		return json.loads(text)
	except json.JSONDecodeError as error:
		raise ValueError(f'{label} is not JSON: {error}') from None


def _decode_at(text: str, start: int, label: str) -> object:
	"""The first JSON value at or after `start`, stopping at `</script>`.

	The assignment may be followed by a semicolon and then the closing
	tag; searching only within this script keeps a later script's JSON
	from being mistaken for this one's. Leading whitespace and comments
	between `=` and the value are skipped by looking for `{`/`[`.
	"""
	end = text.find('</script>', start)
	snippet = text[start : end if end != -1 else len(text)]
	for index, character in enumerate(snippet):
		if character not in '{[':
			continue
		try:
			value, _ = json.JSONDecoder().raw_decode(snippet, index)
		except json.JSONDecodeError as error:
			raise ValueError(f'{label} is not JSON: {error}') from None
		return value
	raise ValueError(f'{label} has no JSON object after "="; layout changed?')
