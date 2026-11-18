"""The date formats boards use, converted to the one the contract wants.

Every board invents its own `published` spelling — "2026-11-10
13:42:01", "Nov 15 2026", a unix timestamp, RFC 822 from an RSS feed,
"Posted 3 days ago", a year-less "10 Nov" — and the pipeline compares
ISO strings. These three functions are the conversions the built-ins
already wrote one by one (remotive, bruntwork, wellfound, eurotechjobs,
justremote); moving them here means the next plugin does not have to
rediscover each format, and a fix reaches every board.

`now=` exists on the two relative helpers so a test (or a historical
import) can pin the clock without freezing the process. The default is
UTC, because the boards' stamps are UTC and a run at midnight local
time should not date a job a day off.
"""

import re
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime

# Formats `datetime.fromisoformat` does not already take — the shapes
# the built-ins have solved: bruntwork's "Nov 15 2026", remotive's
# "2026-11-10 13:42:01", a slash date, and comma/full-month variants.
# RFC 822 (RSS `pubDate`) is tried after these, with `email.utils`.
_FORMATS = (
	'%Y-%m-%d %H:%M:%S',
	'%Y-%m-%d %H:%M',
	'%Y/%m/%d',
	'%d %b %Y',
	'%d %B %Y',
	'%b %d %Y',
	'%B %d %Y',
	'%b %d, %Y',
	'%B %d, %Y',
)

_RELATIVE_RE = re.compile(
	r'(?P<count>\d+|an?|one)\s*'
	r'(?P<unit>second|minute|hour|day|week|month|year)s?\s+ago',
	re.IGNORECASE,
)
_TODAY_RE = re.compile(r'\b(today|now|just now|just posted)\b', re.IGNORECASE)
_YESTERDAY_RE = re.compile(r'\byesterday\b', re.IGNORECASE)

_YEARLESS_FORMATS = ('%d %b', '%d %B', '%b %d', '%B %d')

_UNITS = {
	'second': 'seconds',
	'minute': 'minutes',
	'hour': 'hours',
	'day': 'days',
	'week': 'weeks',
}


def iso(value: str | float | date | datetime) -> str:
	"""Convert a board's timestamp to an ISO 8601 string.

	Accepts an ISO string (a trailing `Z` becomes `+00:00`), the
	non-ISO date formats the boards write, RFC 822 (RSS `pubDate`), a
	unix timestamp in seconds, and a datetime/date object. Raises
	ValueError for a string it cannot place — a scrape should report a
	board's new date format, not store a string that sorts wrongly —
	and TypeError for a value that is not a date at all.
	"""
	if isinstance(value, datetime):
		return value.isoformat()
	if isinstance(value, date):
		return value.isoformat()
	if isinstance(value, bool):  # bool is an int; a flag is not a date
		raise TypeError(f'not a date: {value!r}')
	if isinstance(value, (int, float)):
		return datetime.fromtimestamp(value, UTC).isoformat()
	if not isinstance(value, str):
		raise TypeError(f'not a date: {value!r}')

	text = value.strip()
	if not text:
		raise ValueError('not a date: empty string')

	normalised = text[:-1] + '+00:00' if text[-1:] in ('Z', 'z') else text
	try:
		return datetime.fromisoformat(normalised).isoformat()
	except ValueError:
		pass
	for pattern in _FORMATS:
		try:
			return datetime.strptime(text, pattern).isoformat()
		except ValueError:
			continue
	try:
		return parsedate_to_datetime(text).isoformat()
	except (TypeError, ValueError):
		raise ValueError(f'not a date: {value!r}') from None


def relative(text: str, *, now: datetime | None = None) -> str:
	"""Convert "Posted 3 days ago" (or today/yesterday) to an ISO date.

	Relative stamps are date precision on every board that uses one,
	so the result is `YYYY-MM-DD`: "2 hours ago" is today's date, which
	is the best the board said. A month is 30 days and a year 365, the
	conventional approximations for a stamp that is already coarse.
	"""
	base = now or datetime.now(UTC)
	lowered = text.casefold()

	match = _RELATIVE_RE.search(text)
	if match is not None:
		word = match['count'].casefold()
		count = 1 if word in ('a', 'an', 'one') else int(word)
		unit = match['unit'].casefold()
		if unit in _UNITS:
			delta = timedelta(**{_UNITS[unit]: count})
		elif unit == 'month':
			delta = timedelta(days=30 * count)
		else:  # year
			delta = timedelta(days=365 * count)
		return (base - delta).date().isoformat()

	if _YESTERDAY_RE.search(lowered):
		return (base - timedelta(days=1)).date().isoformat()
	if _TODAY_RE.search(lowered):
		return base.date().isoformat()
	raise ValueError(f'not a relative date: {text!r}')


def yearless(text: str, *, now: datetime | None = None) -> str:
	"""Convert a year-less "10 Nov" to an ISO date, inferring the year.

	The most recent year that does not put the date in the future: a
	board that keeps a month of jobs cannot mean next November, so
	"20 Nov" seen on 18 Nov is last year's posting. Full month names
	and the "Nov 10" order are accepted too.
	"""
	today = (now or datetime.now(UTC)).date()
	month, day = _month_day(text)
	# A four-year window covers 29 Feb: the nearest year where the date
	# exists is at most three years back from a non-leap one.
	for year in range(today.year, today.year - 5, -1):
		try:
			candidate = date(year, month, day)
		except ValueError:
			continue
		if candidate <= today:
			return candidate.isoformat()
	raise ValueError(f'not a year-less date: {text!r}')  # unreachable; month is real


def _month_day(text: str) -> tuple[int, int]:
	"""The (month, day) in a year-less date, whatever its order."""
	value = text.strip()
	for pattern in _YEARLESS_FORMATS:
		try:
			# strptime needs a year; 2000 is a leap year, so "29 Feb"
			# parses too. Only the month and day are used.
			parsed = datetime.strptime(f'{value} 2000', f'{pattern} %Y')
		except ValueError:
			continue
		return parsed.month, parsed.day
	raise ValueError(f'not a year-less date: {text!r}')
