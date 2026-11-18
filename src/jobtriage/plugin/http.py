"""HTTP for plugins: urllib plus the four things every board needs.

Each of the built-ins used to carry the same boilerplate: a
`urllib.request` call, a browser User-Agent (several boards 403
urllib's default), a 30-second timeout, a UTF-8 decode with 'replace',
and — for the rate-limited ones — a pause. Keeping that here makes a
plugin a parser plus a URL, and a behaviour change (a longer timeout,
a second retry) reaches every board at once. urllib only, so this adds
no dependency.

The User-Agent `ua='browser'` sends is `user_agent()`: the desktop-
browser string by default, or `[network] user_agent` from the config
when the user set one. The registry applies it once per board set, so a
plugin calls `get_json(url)` and gets the right agent for free; a
board that must send its own string passes `ua=` explicitly.

Politeness is ambient, because a plugin should not have to thread a
delay through every call it makes. `polite(delay)` sets a context-local
delay — the registry opens it around a board's `recent_jobs`, taking
the value from the module's `POLITE_DELAY` — and each helper sleeps
enough before a request that two consecutive requests are at least
that far apart. It is context-local rather than a module global so two
boards scraping in parallel threads each keep their own clock, and so
the value cannot leak from one board's run into the next. A one-off
call can pass `delay=` explicitly; it wins over the ambient value.

The `sleep` and `monotonic` module attributes are the test seam: a test
replaces them to assert the delay without waiting for it, and replaces
`urlopen` to avoid the network.
"""

import contextlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from contextvars import ContextVar
from typing import Any

log = logging.getLogger('jobtriage.http')

# A desktop browser UA. Boards that block the default Python-urllib agent
# accept any browser string; euremotejobs additionally rejects
# parentheses, which is why callers can pass their own verbatim.
USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
BROWSER = 'browser'
TIMEOUT = 30

# `[network] user_agent` when the user set one (plan 08 step 14). It
# replaces the browser string for every `ua='browser'` call; a board
# passing its own string (euremotejobs' bare `Mozilla/5.0`) still wins.
# Set by `registry.load`, which is the one place every board-running
# path goes through with the config in hand; empty means the default.
_configured = ''


def set_user_agent(value: str) -> None:
	"""Use `value` for `ua='browser'` calls; empty restores the default."""
	global _configured
	_configured = (value or '').strip()


def user_agent() -> str:
	"""The User-Agent `ua='browser'` resolves to right now."""
	return _configured or USER_AGENT


# Replaced in tests. Module attributes rather than default arguments so
# a test can patch one name and every helper sees it.
sleep = time.sleep
monotonic = time.monotonic
urlopen = urllib.request.urlopen

# The ambient politeness, set by `polite()`. A ContextVar (not a module
# global) gives each scraping thread its own value.
_DELAY: ContextVar[float] = ContextVar('jobtriage_http_delay', default=0.0)
_LAST: ContextVar[float | None] = ContextVar('jobtriage_http_last', default=None)


class HttpError(Exception):
	"""A request failed. Tests and plugins read `status`, `url`, `body`.

	`status` is the HTTP status, or None for a transport failure (DNS,
	timeout, a reset that survived the retry). `body` is the response
	body of an HTTP error, because at least one board answers a stale
	nonce with a 403 whose JSON body is the real explanation.
	"""

	def __init__(self, url: str, *, status: int | None = None, detail: str = '', body: str = ''):
		self.url = url
		self.status = status
		self.detail = detail
		self.body = body
		if status is None:
			message = f'{url}: {detail}' if detail else url
		else:
			message = f'HTTP {status} for {url}'
			if detail:
				message = f'{message}: {detail}'
		super().__init__(message)


@contextlib.contextmanager
def polite(delay: float):
	"""Make helpers in this context pause between requests.

	The registry wraps a board's `recent_jobs` in this with the module's
	`POLITE_DELAY`, so a plugin calls `get_json(url)` and gets the pause
	for free. The value is restored on exit, including after an error.
	"""

	token = _DELAY.set(max(0.0, float(delay)))
	last = _LAST.set(None)
	try:
		yield
	finally:
		_LAST.reset(last)
		_DELAY.reset(token)


def get_text(
	url: str,
	*,
	params: dict[str, object] | None = None,
	headers: dict[str, str] | None = None,
	ua: str | None = BROWSER,
	delay: float | None = None,
) -> str:
	"""GET `url`, returning the decoded body.

	`params` is appended to the query string (lists repeat the key,
	which is how WordPress-style filters and jobgether's API want
	them). `ua='browser'` uses `USER_AGENT`; any other string is sent
	verbatim; None leaves urllib's own default (some boards prefer it).
	`delay` overrides the ambient `polite()` delay for this call.
	"""

	return _request('GET', url, params=params, headers=headers, ua=ua, delay=delay)


def get_json(
	url: str,
	*,
	params: dict[str, object] | None = None,
	headers: dict[str, str] | None = None,
	ua: str | None = BROWSER,
	delay: float | None = None,
) -> Any:
	"""GET `url` and parse the body as JSON.

	A body that is not JSON raises ValueError naming the URL, rather
	than a bare JSONDecodeError with a character offset and no context.
	"""

	text = get_text(url, params=params, headers=headers, ua=ua, delay=delay)
	return _loads(text, url)


def post_json(
	url: str,
	payload: object,
	*,
	headers: dict[str, str] | None = None,
	ua: str | None = BROWSER,
	delay: float | None = None,
) -> Any:
	"""POST `payload` as JSON and parse the JSON response.

	The common API round trip (workingnomads' Elasticsearch query,
	Firestore's runQuery): the body is JSON and the answer is JSON.
	"""

	data = json.dumps(payload).encode('utf-8')
	text = _request(
		'POST',
		url,
		data=data,
		content_type='application/json',
		headers=headers,
		ua=ua,
		delay=delay,
	)
	return _loads(text, url)


def post_form(
	url: str,
	fields: dict[str, object],
	*,
	headers: dict[str, str] | None = None,
	ua: str | None = BROWSER,
	delay: float | None = None,
) -> str:
	"""POST `fields` form-encoded, returning the decoded body.

	An HTML or AJAX endpoint (euremotejobs' admin-ajax.php) is not
	necessarily JSON, so this returns text; a caller that knows the
	answer is JSON calls `json.loads` on it.
	"""

	data = urllib.parse.urlencode(fields, doseq=True).encode('utf-8')
	return _request(
		'POST',
		url,
		data=data,
		content_type='application/x-www-form-urlencoded',
		headers=headers,
		ua=ua,
		delay=delay,
	)


def _request(
	method: str,
	url: str,
	*,
	data: bytes | None = None,
	content_type: str | None = None,
	params: dict[str, object] | None = None,
	headers: dict[str, str] | None = None,
	ua: str | None = BROWSER,
	delay: float | None = None,
) -> str:
	"""One request, one retry when the connection was reset.

	A reset is the rate-limit signal remotive and euremotejobs give; a
	404, a timeout or a DNS failure is not retried, so a broken board
	fails fast. The retry waits the polite delay again, which is what
	makes it a back-off rather than a hammer.
	"""

	url = _with_params(url, params)
	request_headers = _headers(headers, ua)
	if content_type is not None:
		request_headers.setdefault('Content-Type', content_type)
	log.debug('%s %s', method, url)

	for attempt in range(2):
		_pause(delay)
		request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
		try:
			with urlopen(request, timeout=TIMEOUT) as response:
				return response.read().decode('utf-8', 'replace')
		except urllib.error.HTTPError as error:
			raise _status_error(error, url) from None
		except OSError as error:
			if attempt == 0 and _is_reset(error):
				continue
			raise _network_error(error, url) from None
	raise HttpError(url, detail='request failed')  # unreachable; for type checkers


def _pause(delay: float | None) -> None:
	"""Wait until `delay` seconds have passed since the last request.

	The first request in a context never waits. The timestamp is
	recorded after the wait — when this request actually goes out — so
	the next call measures a full gap even if the sleep overshot.
	"""

	seconds = _DELAY.get() if delay is None else max(0.0, float(delay))
	now = monotonic()
	last = _LAST.get()
	if seconds > 0 and last is not None:
		remaining = seconds - (now - last)
		if remaining > 0:
			sleep(remaining)
			now = monotonic()
	_LAST.set(now)


def _with_params(url: str, params: dict[str, object] | None) -> str:
	"""`url` with `params` merged into its query string, fragment intact."""
	if not params:
		return url
	parts = urllib.parse.urlsplit(url)
	query = urllib.parse.urlencode(params, doseq=True)
	merged = f'{parts.query}&{query}' if parts.query else query
	return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, merged, parts.fragment))


def _headers(headers: dict[str, str] | None, ua: str | None) -> dict[str, str]:
	"""The caller's headers plus a UA unless they set one themselves."""
	resolved = dict(headers or {})
	if ua is None:
		return resolved
	if any(key.casefold() == 'user-agent' for key in resolved):
		return resolved
	resolved['User-Agent'] = user_agent() if ua == BROWSER else ua
	return resolved


def _is_reset(error: OSError) -> bool:
	"""Was this failure a connection reset (directly or inside URLError)?"""
	if isinstance(error, ConnectionResetError):
		return True
	return isinstance(getattr(error, 'reason', None), ConnectionResetError)


def _status_error(error: urllib.error.HTTPError, url: str) -> HttpError:
	"""Turn urllib's HTTPError into ours, keeping the body when readable."""
	try:
		body = error.read().decode('utf-8', 'replace')
	except (OSError, AttributeError):  # no readable body (fp is None, refused)
		body = ''
	return HttpError(url, status=error.code, detail=str(error.reason), body=body)


def _network_error(error: OSError, url: str) -> HttpError:
	"""A transport failure, with the underlying reason in the message."""
	reason = getattr(error, 'reason', error)
	return HttpError(url, detail=f'{type(reason).__name__}: {reason}')


def _loads(text: str, url: str) -> Any:
	"""JSON with the URL in the error message."""
	try:
		return json.loads(text)
	except json.JSONDecodeError as error:
		raise ValueError(f'{url}: response is not JSON: {error}') from None
