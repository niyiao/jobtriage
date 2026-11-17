"""The relevance filter: young jobs, titles worth applying to.

Two gates, applied after the scrape:

- Age. Boards reach back months; the cutoff is the user's
  `search.max_age_days` (21 by default) and older dated jobs are
  dropped. An undated job is assumed new instead of dropped: the
  cache keys it under the current month, which is the decision to
  make about a job whose age nobody knows.
- Title. The rules are the user's `[filter]` table (plan 03):
  `require_any` and `include_any` are positive gates (both must
  match when non-empty), `exclude_any` is the turnaway gate, and
  `exclude_companies` drops a company by name. An empty gate is
  disabled, so an empty `[filter]` keeps every fresh title — the
  board's category is the only narrowing, the right default for a
  stranger. The shipped presets under `assets/presets/` hold the
  batteries this module used to hardcode; nothing personal lives
  here any more.

Fragments are regexes, matched case-insensitively and on word
boundaries, exactly as the presets spell them. `matching_role` returns
the word that matched so the store's cache can key on it (plan 02):
with no title filter every title matches with the empty string, which
is a value a caller must test with `is None`, not truthiness.

The semantics are today's: a role word AND a builder word AND no
turnaway. What changed is where the words come from, not how they
combine.

`TitleFilter.verdict` is the same decision with the *reason* attached —
which gate decided and the text it matched. `jobtriage filter test`
prints that reason for one title, and `scrape --explain` tallies the
same verdicts per board (plan 03 step 7), so a user or an agent can see
why a job disappeared instead of reverse-engineering the regexes.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True, slots=True)
class Verdict:
	"""Why one title passed or failed the title gates.

	`gate` names the decision — `'require_any'`, `'include_any'` or
	`'exclude_any'` — and `word` is the text that gate matched. A
	positive gate that failed matched nothing, so its `word` is None;
	`exclude_any` never fails that way.

	`role` is exactly what `TitleFilter.match` returns: None when the
	title is somebody else's job, '' when no title gate is configured,
	otherwise the word the store keys its cache on. Callers that only
	need the old answer keep it; callers that explain read `gate` and
	`word`.
	"""

	gate: str | None
	word: str | None
	role: str | None

	@property
	def kept(self) -> bool:
		"""True when the title passes every configured gate."""
		return self.role is not None


class TitleFilter:
	"""The `[filter]` table, compiled once into three bounded regexes.

	`require_any` and `include_any` are positive gates (both must
	match when non-empty), `exclude_any` is the turnaway gate, and
	`exclude_companies` folds to a list of company-name fragments.
	Empty everywhere means no title filter at all.

	Duck-typed on purpose: anything with those four attributes works
	(`config.FilterSettings` is the real one), so relevance never has
	to import the config module and pull Textual in behind it.
	"""

	def __init__(self, settings=None):
		self.require = _compile(getattr(settings, 'require_any', ()))
		self.include = _compile(getattr(settings, 'include_any', ()))
		self.exclude = _compile(getattr(settings, 'exclude_any', ()))
		self.companies = tuple(
			folded
			for folded in (job_key(name) for name in getattr(settings, 'exclude_companies', ()) if name)
			if folded
		)

	def match(self, title: str) -> str | None:
		"""The word that makes this title ours; '' when no gate applies.

		None means the title is somebody else's job. The returned word
		comes from `require_any` when that gate is set (today's role
		word), otherwise from `include_any`, so the cache still gets a
		stable value to key on when the user only wrote a builder list.
		"""
		return self.verdict(title).role

	def verdict(self, title: str) -> Verdict:
		"""The same decision as `match`, with the gate and word named.

		The order is `match`'s and the pipeline's: the positive gates
		first, then the turnaway gate. A dropped verdict therefore
		names the *first* reason the title failed, which is the one a
		user should fix; an age drop is a different gate entirely and
		is not part of this title-only decision.
		"""
		if not (self.require or self.include or self.exclude):
			return Verdict(None, None, '')

		role = self.require.search(title) if self.require else None
		if self.require and role is None:
			return Verdict('require_any', None, None)

		include = self.include.search(title) if self.include else None
		if self.include and include is None:
			return Verdict('include_any', None, None)

		turnaway = self.exclude.search(title) if self.exclude else None
		if turnaway is not None:
			return Verdict('exclude_any', turnaway.group(0), None)

		word = role or include
		if role is not None:
			gate = 'require_any'
		elif include is not None:
			gate = 'include_any'
		else:
			# Only exclude_any is configured, and the title survived it:
			# there is no positive gate to thank, but the title is kept.
			gate = None
		return Verdict(gate, word.group(0) if word else None, word.group(0).casefold() if word else '')

	def turns_away(self, company: str | None) -> bool:
		"""True when the company matches an `exclude_companies` entry.

		Fold-tolerant containment: "Acme" excludes "Acme Inc." and
		"ACME, Ltd." without the user having to spell every legal
		suffix.
		"""
		folded = job_key(company or '')
		return any(name in folded for name in self.companies)


def _compile(fragments) -> re.Pattern[str] | None:
	"""One bounded alternation; None when the gate is empty (disabled).

	The word-boundary wrapper is what makes 'go' match "Go Engineer"
	and not "Django"; a fragment that writes its own boundaries nests
	harmlessly inside.
	"""
	if not fragments:
		return None
	return re.compile(r'\b(?:' + '|'.join(fragments) + r')\b', re.IGNORECASE)


def matching_role(title: str, settings=None) -> str | None:
	"""Return the role word that makes this title ours, or None.

	`settings` is the user's FilterSettings (or the same four list
	attributes); None is no filter, so every title is kept with the
	empty string as its role. The company exclusion is not a title
	gate and is applied by `select`, which has the company.
	"""
	return TitleFilter(settings).match(title)


def parse_published(value: str | None) -> datetime | None:
	"""Parse a job's ISO published string into an aware UTC datetime.

	Every board emits ISO 8601, at mixed precision — date-only here,
	milliseconds with a Z there. Date-only values are taken as UTC
	midnight. Returns None for anything unparseable, including the
	empty string (dynamitejobs can serve jobs with no date at all).
	"""
	if not value:
		return None
	try:
		parsed = datetime.fromisoformat(value)
	except (TypeError, ValueError):
		return None
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=UTC)
	return parsed.astimezone(UTC)


def is_fresh(
	published: str | None,
	now: datetime | None = None,
	*,
	max_age_days: int,
) -> bool:
	"""True if the job is at most `max_age_days` old — or undated.

	The cutoff is the user's `search.max_age_days`; there is no code
	default here, so a caller cannot forget and quietly get somebody
	else's three weeks. An undated job is assumed new: it flows on, and
	the store keys it under the current month.
	"""
	posted = parse_published(published)
	if posted is None:
		return True
	return posted >= (now or datetime.now(UTC)) - timedelta(days=max_age_days)


def job_key(text: str) -> str:
	"""A fold-tolerant identity for a company or title: casefolded,
	everything that isn't a letter or digit stripped. The same posting
	rides several boards worded slightly differently — "Front-End
	Engineer" against "Frontend Engineer", hyphen against en dash —
	and those are one job, not two.
	"""
	return re.sub(r'[\W_]+', '', text.casefold())


def select(
	jobs: list[dict],
	settings,
	max_age_days: int,
	now: datetime | None = None,
	on_drop=None,
) -> list[dict]:
	"""Keep the young, relevant jobs, newest first.

	`settings` is `config.filter` and `max_age_days` is
	`config.search.max_age_days`; both are required so a caller
	cannot fall back to a code default the user never chose. Jobs are
	deduped by (company, title) the fold-tolerant way, keeping the
	newest — the same convention scrape_all uses, minus the
	punctuation sensitivity that let cross-post variants through. A
	dated view of a job wins over an undated one in the dedup; the
	undated survivor sorts last. Each survivor gains a 'role' key —
	the word that matched, '' when the filter is off — for the cache
	to key on.

	`on_drop` is an observer for `scrape --explain`: it is called
	`on_drop(job, gate, word)` once for every job this function does
	not return, with the gate that removed it — a title gate,
	`'exclude_companies'`, `'age'`, or `'duplicate'` for a losing
	cross-post. Every input job is therefore either returned or
	reported exactly once, which is what makes the per-board tally
	add up. The default None keeps the filter exactly as it was.
	"""
	title_filter = TitleFilter(settings)
	newest: dict[tuple[str, str], dict] = {}
	for job in jobs:
		decision = title_filter.verdict(job.get('title', ''))
		if decision.role is None:
			_drop(on_drop, job, decision.gate, decision.word)
			continue
		company = job.get('company')
		if title_filter.turns_away(company):
			_drop(on_drop, job, 'exclude_companies', company)
			continue
		if not is_fresh(job.get('published'), now, max_age_days=max_age_days):
			_drop(on_drop, job, 'age', None)
			continue
		job['role'] = decision.role
		key = (job_key(job['company']), job_key(job['title']))
		previous = newest.get(key)
		if previous is None:
			newest[key] = job
		elif (job['published'] or '') > (previous['published'] or ''):
			_drop(on_drop, previous, 'duplicate', None)
			newest[key] = job
		else:
			_drop(on_drop, job, 'duplicate', None)
	return sorted(newest.values(), key=lambda job: job['published'] or '', reverse=True)


def _drop(on_drop, job: dict, gate: str | None, word: str | None) -> None:
	"""Tell an observer about one dropped job; a no-op without one."""
	if on_drop is not None:
		on_drop(job, gate, word)
