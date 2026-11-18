"""Tests for the public plugin API: the HTTP helpers, the SSR parsers,
the date conversions and the re-exported surface.

No test here touches a network: `http.urlopen` is a recorder, `sleep`
and `monotonic` a fake clock. Each helper is exercised on the shapes
the fifteen built-ins actually meet, so a plugin can copy the test's
input as a working example.
"""

import io
import urllib.error
from datetime import UTC, date, datetime

import pytest

from jobtriage import geo as host_geo
from jobtriage.plugin import API_VERSION, Job, dates, geo, http, parse


class Response:
	"""The slice of urllib's response the helpers read."""

	def __init__(self, body: str | bytes):
		self.body = body.encode('utf-8') if isinstance(body, str) else body

	def read(self) -> bytes:
		return self.body

	def __enter__(self):
		return self

	def __exit__(self, *exc):
		return False


class Wire:
	"""A urlopen replacement: records requests, plays canned responses.

	An Exception in `responses` is raised instead, which is how the
	retry and error paths are tested without a socket.
	"""

	def __init__(self):
		self.requests = []
		self.timeouts = []
		self.responses = []

	def __call__(self, request, timeout=None):
		self.requests.append(request)
		self.timeouts.append(timeout)
		item = self.responses.pop(0)
		if isinstance(item, Exception):
			raise item
		return Response(item)


@pytest.fixture(autouse=True)
def clean_politeness_clock():
	"""A ContextVar remembers the last request across tests in one thread;
	start each test as a new thread would, so nothing waits for a clock a
	previous test wound."""
	token = http._LAST.set(None)
	http.set_user_agent('')
	yield
	http._LAST.reset(token)
	http.set_user_agent('')


@pytest.fixture
def wire(monkeypatch):
	recorder = Wire()
	monkeypatch.setattr(http, 'urlopen', recorder)
	return recorder


@pytest.fixture
def fake_time(monkeypatch):
	"""A monotonic clock that only moves when sleep is called."""
	state = {'now': 0.0, 'sleeps': []}
	monkeypatch.setattr(http, 'monotonic', lambda: state['now'])

	def sleep(seconds):
		state['sleeps'].append(seconds)
		state['now'] += seconds

	monkeypatch.setattr(http, 'sleep', sleep)
	return state


# -- http ------------------------------------------------------------------


def test_get_text_sends_a_browser_ua_and_decodes_utf8(wire):
	wire.responses = ['h\u00e9llo']
	assert http.get_text('https://board.test/jobs') == 'h\u00e9llo'
	request = wire.requests[0]
	assert request.full_url == 'https://board.test/jobs'
	assert request.get_header('User-agent') == http.USER_AGENT
	assert wire.timeouts == [http.TIMEOUT]


def test_ua_can_be_a_literal_none_or_overridden_by_a_header(wire):
	wire.responses = ['ok', 'ok', 'ok']
	http.get_text('https://board.test/a', ua='Mozilla/5.0')
	http.get_text('https://board.test/b', ua=None)
	http.get_text('https://board.test/c', headers={'user-agent': 'custom'})
	assert wire.requests[0].get_header('User-agent') == 'Mozilla/5.0'
	assert wire.requests[1].get_header('User-agent') is None
	assert wire.requests[2].get_header('User-agent') == 'custom'


def test_a_configured_user_agent_replaces_the_browser_string(wire):
	"""`[network] user_agent`, applied by registry.load, is what
	ua='browser' resolves to (plan 08 step 14). An explicit per-call
	string still wins: some boards demand their own."""
	wire.responses = ['ok', 'ok']
	http.set_user_agent('jobtriage/1.0 (+https://example.test)')

	http.get_text('https://board.test/default')
	http.get_text('https://board.test/mozilla', ua='Mozilla/5.0')

	assert wire.requests[0].get_header('User-agent') == 'jobtriage/1.0 (+https://example.test)'
	assert wire.requests[1].get_header('User-agent') == 'Mozilla/5.0'
	assert http.user_agent() == 'jobtriage/1.0 (+https://example.test)'


def test_params_are_merged_into_the_existing_query(wire):
	wire.responses = ['ok']
	http.get_text(
		'https://board.test/jobs?page=2#top',
		params={'category': 'dev', 'tag': ['a', 'b']},
	)
	assert wire.requests[0].full_url == (
		'https://board.test/jobs?page=2&category=dev&tag=a&tag=b#top'
	)


def test_get_json_parses_and_names_the_url_on_a_bad_body(wire):
	wire.responses = ['{"jobs": [1]}', '<html>not json</html>']
	assert http.get_json('https://board.test/a') == {'jobs': [1]}
	with pytest.raises(ValueError, match='https://board.test/b: response is not JSON'):
		http.get_json('https://board.test/b')


def test_post_json_sends_json_and_returns_json(wire):
	wire.responses = ['{"hits": 2}']
	assert http.post_json('https://board.test/search', {'query': 'dev'}) == {'hits': 2}
	request = wire.requests[0]
	assert request.get_method() == 'POST'
	assert request.get_header('Content-type') == 'application/json'
	assert request.data == b'{"query": "dev"}'


def test_post_form_encodes_fields_and_returns_text(wire):
	wire.responses = ['{"success": true}']
	body = http.post_form('https://board.test/admin-ajax.php', {'action': 'search', 'page': 1})
	assert body == '{"success": true}'
	request = wire.requests[0]
	assert request.get_header('Content-type') == 'application/x-www-form-urlencoded'
	assert request.data == b'action=search&page=1'


def test_an_http_error_carries_status_url_and_body(wire):
	error = urllib.error.HTTPError(
		'https://board.test/jobs',
		403,
		'Forbidden',
		{},
		io.BytesIO(b'{"success": false, "reason": "stale nonce"}'),
	)
	wire.responses = [error]
	with pytest.raises(http.HttpError) as raised:
		http.get_text('https://board.test/jobs')
	assert raised.value.status == 403
	assert raised.value.url == 'https://board.test/jobs'
	assert 'stale nonce' in raised.value.body
	assert 'HTTP 403 for https://board.test/jobs' in str(raised.value)


def test_a_connection_reset_is_retried_once(wire):
	wire.responses = [ConnectionResetError('reset'), 'second try']
	assert http.get_text('https://board.test/jobs') == 'second try'
	assert len(wire.requests) == 2


def test_a_reset_inside_url_error_is_retried(wire):
	wire.responses = [urllib.error.URLError(ConnectionResetError('reset')), 'ok']
	assert http.get_text('https://board.test/jobs') == 'ok'
	assert len(wire.requests) == 2


def test_a_second_reset_is_a_transport_http_error(wire):
	wire.responses = [ConnectionResetError('one'), ConnectionResetError('two')]
	with pytest.raises(http.HttpError) as raised:
		http.get_text('https://board.test/jobs')
	assert raised.value.status is None
	assert 'two' in str(raised.value)
	assert len(wire.requests) == 2


def test_other_network_errors_are_not_retried(wire):
	wire.responses = [urllib.error.URLError('name resolution failed')]
	with pytest.raises(http.HttpError, match='name resolution failed'):
		http.get_text('https://board.test/jobs')
	assert len(wire.requests) == 1


def test_helpers_pause_between_consecutive_calls(wire, fake_time):
	wire.responses = ['a', 'b', 'c']
	http.get_text('https://board.test/1', delay=2)
	http.get_text('https://board.test/2', delay=2)
	http.get_text('https://board.test/3', delay=2)
	assert fake_time['sleeps'] == [2, 2]


def test_a_retry_waits_the_delay_again(wire, fake_time):
	wire.responses = [ConnectionResetError('reset'), 'ok']
	http.get_text('https://board.test/jobs', delay=1.5)
	assert fake_time['sleeps'] == [1.5]


def test_polite_sets_the_ambient_delay_and_restores_it(wire, fake_time):
	wire.responses = ['a', 'b', 'c', 'd']
	with http.polite(0.5):
		http.get_text('https://board.test/1')
		http.get_text('https://board.test/2')
	assert fake_time['sleeps'] == [0.5]
	# outside the context each call starts its own clock again
	http.get_text('https://board.test/3')
	http.get_text('https://board.test/4')
	assert fake_time['sleeps'] == [0.5]


def test_an_explicit_delay_wins_over_the_context(wire, fake_time):
	wire.responses = ['a', 'b']
	with http.polite(10):
		http.get_text('https://board.test/1', delay=0)
		http.get_text('https://board.test/2', delay=0)
	assert fake_time['sleeps'] == []


# -- parse -----------------------------------------------------------------


def test_next_data_reads_the_script_whatever_the_attribute_order():
	page = (
		'<html><script type="application/json" data-x="1" id="__NEXT_DATA__">'
		'{"props": {"page": 2}}</script></html>'
	)
	assert parse.next_data(page) == {'props': {'page': 2}}


def test_next_data_says_so_when_the_script_is_gone():
	with pytest.raises(ValueError, match='no <script id="__NEXT_DATA__">'):
		parse.next_data('<html></html>')


def test_next_data_rejects_a_non_json_body():
	with pytest.raises(ValueError, match='__NEXT_DATA__ is not JSON'):
		parse.next_data('<script id="__NEXT_DATA__">window.x = 1</script>')


def test_json_script_reads_a_window_assignment():
	page = '<script>window.__INITIAL_SEARCH_RESULTS__ = {"results": [{"hits": 3}]};</script>'
	assert parse.json_script(page, '__INITIAL_SEARCH_RESULTS__') == {'results': [{'hits': 3}]}


def test_json_script_reads_a_bare_assignment():
	page = '<script>__PINIA_STATE__ = {"jobs": []};</script>'
	assert parse.json_script(page, '__PINIA_STATE__') == {'jobs': []}


def test_json_script_stops_at_its_own_script_tag():
	page = (
		'<script>window.__A__ = {"right": true};</script>'
		'<script>window.__A__ = {"wrong": true};</script>'
	)
	assert parse.json_script(page, '__A__') == {'right': True}


def test_json_script_says_so_when_the_variable_is_gone():
	with pytest.raises(ValueError, match='no __PRELOADED_STATE__ assignment'):
		parse.json_script('<html></html>', '__PRELOADED_STATE__')


def test_json_script_rejects_a_javascript_wrapper():
	page = '<script>window.__NUXT__=(function(a){return a})(1);</script>'
	with pytest.raises(ValueError, match='__NUXT__ is not JSON'):
		parse.json_script(page, '__NUXT__')


def test_html_text_strips_tags_decodes_entities_and_collapses_space():
	assert parse.html_text('<b>Senior</b>&nbsp;Engineer &amp;<br/>Lead') == 'Senior Engineer & Lead'


# -- dates -----------------------------------------------------------------


@pytest.mark.parametrize(
	('value', 'expected'),
	[
		('2026-11-10 13:42:01', '2026-11-10T13:42:01'),
		('2026-11-10T13:42:01Z', '2026-11-10T13:42:01+00:00'),
		('2026-11-10', '2026-11-10T00:00:00'),
		('Nov 15 2026', '2026-11-15T00:00:00'),
		('10 November 2026', '2026-11-10T00:00:00'),
		('Tue, 10 Nov 2026 12:00:00 +0000', '2026-11-10T12:00:00+00:00'),
	],
)
def test_iso_converts_the_string_formats_boards_write(value, expected):
	assert dates.iso(value) == expected


def test_iso_accepts_timestamps_and_date_objects():
	assert dates.iso(datetime(2026, 11, 10, 13, 42)) == '2026-11-10T13:42:00'
	assert dates.iso(date(2026, 11, 10)) == '2026-11-10'
	epoch = datetime(2026, 11, 10, tzinfo=UTC).timestamp()
	assert dates.iso(epoch) == '2026-11-10T00:00:00+00:00'


def test_iso_says_what_it_cannot_read():
	with pytest.raises(ValueError, match='not a date'):
		dates.iso('2 days ago')
	with pytest.raises(ValueError, match='empty'):
		dates.iso('  ')
	with pytest.raises(TypeError, match='not a date'):
		dates.iso(object())


def test_relative_counts_days_weeks_and_hours():
	now = datetime(2026, 11, 18, 12, tzinfo=UTC)
	assert dates.relative('Posted 3 days ago', now=now) == '2026-11-15'
	assert dates.relative('Posted 1 day ago', now=now) == '2026-11-17'
	assert dates.relative('2 weeks ago', now=now) == '2026-11-04'
	assert dates.relative('5 hours ago', now=now) == '2026-11-18'
	assert dates.relative('a month ago', now=now) == '2026-10-19'


def test_relative_understands_today_and_yesterday():
	now = datetime(2026, 11, 18, 0, 30, tzinfo=UTC)
	assert dates.relative('Posted today', now=now) == '2026-11-18'
	assert dates.relative('Posted yesterday', now=now) == '2026-11-17'
	assert dates.relative('just now', now=now) == '2026-11-18'


def test_relative_rejects_a_phrase_it_does_not_know():
	with pytest.raises(ValueError, match='not a relative date'):
		dates.relative('sometime last spring')


def test_yearless_infers_this_year_or_the_last():
	now = datetime(2026, 11, 18, tzinfo=UTC)
	assert dates.yearless('10 Nov', now=now) == '2026-11-10'
	assert dates.yearless('20 Nov', now=now) == '2025-11-20'  # cannot be next week


def test_yearless_accepts_month_first_and_full_names():
	now = datetime(2026, 11, 18, tzinfo=UTC)
	assert dates.yearless('Nov 10', now=now) == '2026-11-10'
	assert dates.yearless('10 November', now=now) == '2026-11-10'
	assert dates.yearless('November 10', now=now) == '2026-11-10'


def test_yearless_finds_a_leap_year_for_the_29th_of_february():
	now = datetime(2026, 3, 1, tzinfo=UTC)
	assert dates.yearless('29 Feb', now=now) == '2024-02-29'


def test_yearless_rejects_nonsense():
	with pytest.raises(ValueError, match='not a year-less date'):
		dates.yearless('soon')


# -- the package surface ---------------------------------------------------


def test_the_contract_version_is_one():
	assert API_VERSION == 1


def test_job_declares_exactly_the_six_contract_keys():
	assert set(Job.__required_keys__) == {
		'title',
		'company',
		'url',
		'published',
		'locations',
		'salary',
	}
	assert Job.__optional_keys__ == frozenset()


def test_geo_is_the_host_vocabulary_reexported():
	assert geo.board_tags is host_geo.board_tags
	assert geo.WORLDWIDE == host_geo.WORLDWIDE
	assert geo.REGIONS is host_geo.REGIONS
	assert geo.COUNTRY_ALIASES is host_geo.COUNTRY_ALIASES


def test_board_tags_accepts_a_prefs_object_with_locations():
	class Prefs:
		locations = ('worldwide',)

	class Board:
		WORLDWIDE = ('Anywhere',)
		COUNTRY_STYLE = 'none'

	assert geo.board_tags(Board, Prefs()) == ('Anywhere',)
