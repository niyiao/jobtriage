"""`jobtriage doctor` — one command that checks the whole setup and says
how to fix what is wrong.

It exists because "it doesn't work" arrives without the handful of
facts that decide what could be wrong: whether the op folder is the one
the user thinks, whether the config is valid, whether the database is
the schema this build expects, whether a browser was found and its port
is free, whether every plugin loads, whether the shipped skills are
current, whether the profile files are there, and whether the boards
answer. Every check prints one line — `ok`, `note`, `problem` or
`skip` — and a problem carries the command or edit that fixes it.
Skills and support requests start here.

Exit codes: 0 when nothing is broken, 1 when at least one check is a
`problem`. `note` is deliberately not a failure: a fresh workspace has
no database and nobody has added a resume yet, and both are the normal
state before the first scrape and the first application.

The network is used only for the last check, one HEAD request per
enabled board to its homepage. `--offline` is for CI and for a machine
with no display: it skips every check that depends on the environment
around the folder — the browser, its CDP port and the boards — and runs
the local-only checks (workspace, config, database, plugins, skills,
profile), so "the installation is sound" can be answered on a runner
with no Chrome installed and no network. Everything local is the config,
the database header and the filesystem.
"""

import argparse
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from jobtriage import __version__, config, db, registry, workspace
from jobtriage.browser.find import find_browser
from jobtriage.plugin import http as plugin_http

HOST = '127.0.0.1'

# Short: a closed local port refuses immediately, and something that
# does not answer a DevTools request in a second is not our Chrome.
PORT_TIMEOUT = 1

# A homepage that cannot answer HEAD in ten seconds is a problem worth
# reporting, not a reason to leave doctor hanging.
BOARD_TIMEOUT = 10

# The three files a working op folder needs. The first two are what an
# agent reads; the PDF is what an application upload wants. Missing
# ones are a note — init ships DETAILS.md, the person adds the rest.
PROFILE_FILES = ('RESUME.md', 'DETAILS.md', 'resume.pdf')

# The test seam: a test replaces this to answer without the network.
urlopen = urllib.request.urlopen


@dataclass(frozen=True, slots=True)
class Check:
	"""One line of the report: what was checked, and what to do."""

	state: str  # ok | note | problem | skip
	label: str
	detail: str
	remedy: str = ''


@dataclass
class Report:
	"""The checks in the order they ran, and the summary they make."""

	checks: list[Check] = field(default_factory=list)

	def ok(self, label: str, detail: str) -> None:
		self.checks.append(Check('ok', label, detail))

	def note(self, label: str, detail: str) -> None:
		self.checks.append(Check('note', label, detail))

	def problem(self, label: str, detail: str, remedy: str = '') -> None:
		self.checks.append(Check('problem', label, detail, remedy))

	def skip(self, label: str, detail: str) -> None:
		self.checks.append(Check('skip', label, detail))

	@property
	def problems(self) -> int:
		return sum(1 for check in self.checks if check.state == 'problem')

	def print(self) -> None:
		"""The aligned rows, a blank line, then the counts."""
		width = max((len(check.label) for check in self.checks), default=0)
		print(f'{workspace.PROGRAM} doctor — {__version__}')
		for check in self.checks:
			detail = f'{check.detail} — {check.remedy}' if check.remedy else check.detail
			print(f'  {check.state:<8} {check.label:<{width}}  {detail}')
		print()
		print(f'  {_summary(self.checks)}')


def _summary(checks: list[Check]) -> str:
	"""`8 checks: 6 ok, 1 note, 1 problem` — only the states that occur."""
	counts = []
	for state in ('ok', 'note', 'problem', 'skip'):
		count = sum(1 for check in checks if check.state == state)
		if count:
			counts.append(f'{count} {state}')
	total = len(checks)
	noun = 'check' if total == 1 else 'checks'
	return f'{total} {noun}: ' + ', '.join(counts)


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
	parser = sub.add_parser(
		'doctor',
		help='check the workspace, config, database, browser, plugins, skills, '
		'profile and boards; say how to fix what is wrong',
	)
	parser.add_argument(
		'--offline',
		action='store_true',
		help='skip the checks that depend on this machine and the network: '
		'the browser, its CDP port and the per-board homepage request (local checks only)',
	)
	parser.set_defaults(run=run)
	return parser


def run(args, ws) -> int:
	report = Report()
	report.ok('workspace', str(ws.root))

	settings = _check_config(report, ws)
	_check_database(report, ws)
	if args.offline:
		# Environment, not installation: a CI runner has no Chrome and a
		# headless machine may have no display at all, and neither says
		# anything about this build or this folder.
		report.skip('browser', 'not checked (--offline)')
		report.skip('port', 'not checked (--offline)')
	else:
		_check_browser(report, ws, settings)
	reg = _check_plugins(report, ws, settings)
	_check_skills(report, ws)
	_check_profile(report, ws)

	if args.offline:
		report.skip('boards', 'not checked (--offline)')
	elif settings is None:
		report.skip('boards', 'not checked (the config could not be read)')
	elif reg is None:
		report.skip('boards', 'not checked (the plugin list could not be loaded)')
	else:
		_check_boards(report, reg, settings)

	report.print()
	return 1 if report.problems else 0


def _check_config(report: Report, ws) -> config.Config | None:
	"""Load the whole config; a bad key is a problem with its message.

	The loader already names the file, the section.key and a suggestion,
	so the check does not paraphrase it — a second wording would only be
	a second thing to keep true.
	"""
	try:
		settings = config.load(ws)
	except config.ConfigError as error:
		report.problem(
			'config',
			_one_line(error),
			f'fix that key, or run `{workspace.PROGRAM} config edit`',
		)
		return None
	report.ok('config', f'{ws.config.name} valid')
	return settings


def _check_database(report: Report, ws) -> None:
	"""Compare the header's user_version with this build's LATEST.

	A missing database is not a fault: the first scrape (or any command
	that opens it) creates and migrates it. A version behind is a
	problem because a command that opens it will migrate it under the
	user's nose, and a version ahead means this build is too old.
	"""
	if not ws.db.is_file():
		report.ok('database', 'no database yet — the first scrape creates it')
		return
	try:
		version = db.schema_version(ws.db)
	except Exception as error:  # noqa: BLE001 — an unreadable file is the finding
		report.problem(
			'database',
			f'{ws.db.name} cannot be read: {_reason(error)}',
			f'move it aside and run `{workspace.PROGRAM} db schema` to start a fresh one',
		)
		return

	if version > db.LATEST:
		report.problem(
			'database',
			f'schema {version}, this build understands up to {db.LATEST}',
			f'upgrade {workspace.PROGRAM}',
		)
		return
	if version < db.LATEST:
		report.problem(
			'database',
			f'schema {version}, this build expects {db.LATEST}',
			f'run `{workspace.PROGRAM} init --upgrade`',
		)
		return
	report.ok('database', f'schema {version}{_backups(ws)}')


def _backups(ws) -> str:
	"""`, 2 backups` when a migration has taken any — just a report."""
	if not ws.backups.is_dir():
		return ''
	count = sum(1 for _ in ws.backups.glob('*.db'))
	if count == 0:
		return ''
	return f' · {count} backup{"s" if count != 1 else ""} in .jobtriage/backups'


def _check_browser(report: Report, ws, settings: config.Config | None) -> None:
	"""Which browser the finder returned, and whether its port is free.

	The configured executable is reported separately from the platform
	search: a wrong path in jobtriage.toml is a fixable typo, not "no
	browser installed", and the remedy differs.
	"""
	executable = settings.browser.executable if settings is not None else ''
	port = settings.browser.port if settings is not None else config.BrowserSettings().port
	path = find_browser(executable)
	if path is None:
		if executable.strip():
			report.problem(
				'browser',
				f'browser.executable = {executable!r} does not exist',
				'fix the path in jobtriage.toml, or clear it to auto-detect',
			)
		else:
			report.problem(
				'browser',
				'no Chrome, Chromium, Brave or Edge found',
				'install one, or set browser.executable in jobtriage.toml',
			)
	else:
		report.ok('browser', str(path))

	state = port_state(port)
	if state == 'free':
		report.ok('port', f'{port} free')
	elif state == 'devtools':
		report.ok('port', f'{port} answers DevTools — a browser is already attached')
	else:
		report.problem(
			'port',
			f'{port} is in use by something that is not a DevTools endpoint',
			'quit it, or set browser.port in jobtriage.toml',
		)


def port_state(port: int) -> str:
	"""`free`, `devtools` or `busy` for the local CDP port.

	A free port refuses the connection; the TUI's own Chrome answers
	`/json/version` with a JSON object naming its Browser; anything
	else (an unrelated server, a timeout, a page that is not JSON) is
	`busy`. The distinction matters because only the middle case is
	safe to attach to.
	"""
	try:
		with urlopen(f'http://{HOST}:{port}/json/version', timeout=PORT_TIMEOUT) as response:
			payload = json.loads(response.read().decode('utf-8', 'replace'))
	except urllib.error.URLError as error:
		if isinstance(error.reason, ConnectionRefusedError):
			return 'free'
		return 'busy'
	except (OSError, ValueError):
		return 'busy'
	return 'devtools' if isinstance(payload, dict) and payload.get('Browser') else 'busy'


def _check_plugins(report: Report, ws, settings: config.Config | None) -> registry.Registry | None:
	"""Load every board; a broken file or package is one line each.

	A broken plugin is never fatal to a scrape, which is exactly why it
	needs a surface like this: the board silently disappears otherwise.
	"""
	if settings is None:
		report.skip('plugins', 'not checked (the config could not be read)')
		return None
	try:
		reg = registry.load(ws, settings)
	except Exception as error:  # noqa: BLE001 — a scan failure is the finding
		report.problem(
			'plugins',
			f'could not load the registry: {_reason(error)}',
			f'run `{workspace.PROGRAM} sources` for the board list',
		)
		return None

	if reg.broken:
		reasons = '; '.join(f'{name}: {reason}' for name, reason in sorted(reg.broken.items()))
		report.problem(
			'plugins',
			f'{len(reg.broken)} broken — {reasons}',
			f'fix or remove the file, or reinstall the package; '
			f'`{workspace.PROGRAM} sources` lists every board',
		)
	else:
		report.ok('plugins', f'{len(reg)} boards load')
	return reg


def _check_skills(report: Report, ws) -> None:
	"""Compare every shipped skill file with the workspace copy.

	Byte comparison, not the manifest: the manifest records what init
	shipped and is the right thing for `init --upgrade` to consult (it
	distinguishes the user's edits from ours), while the question here
	is simply "does the installed copy say what this build ships?".
	Edits are reported, not repaired: upgrade writes the new version
	beside the edited file and leaves the user's copy alone.
	"""
	shipped = _shipped_skills()
	if not shipped:
		report.skip('skills', 'the package ships no skills')
		return

	missing, edited = [], []
	for relative, data in shipped.items():
		target = ws.skills / relative
		if not target.is_file():
			missing.append(relative)
		elif target.read_bytes() != data:
			edited.append(relative)
	if not missing and not edited:
		count = sum(1 for name in shipped if name.endswith('/SKILL.md'))
		report.ok('skills', f'{count} skills current')
		return

	parts = []
	if missing:
		parts.append(f'{len(missing)} missing ({_few(missing)})')
	if edited:
		parts.append(f'{len(edited)} edited ({_few(edited)})')
	report.problem(
		'skills',
		'; '.join(parts),
		f'run `{workspace.PROGRAM} init --upgrade` — edited files are kept, '
		'the new version lands beside them as <file>.new',
	)


def _shipped_skills() -> dict[str, bytes]:
	"""Every file under the package's skills folder, keyed by relative path."""
	root = workspace.assets_dir() / 'skills'
	if not root.is_dir():
		return {}
	files = {}
	for file in sorted(root.rglob('*')):
		if not file.is_file():
			continue
		relative = file.relative_to(root)
		if '__pycache__' in relative.parts:
			continue
		files[relative.as_posix()] = file.read_bytes()
	return files


def _few(names: list[str]) -> str:
	"""The first three paths, then `+N more` — a line, not a dump."""
	shown = ', '.join(names[:3])
	if len(names) > 3:
		return f'{shown}, +{len(names) - 3} more'
	return shown


def _check_profile(report: Report, ws) -> None:
	"""The three personal files; missing ones are a note, never fatal.

	`init` ships a blank DETAILS.md and deliberately does not create a
	resume — asking for one at install time is a bad first run. Doctor
	says which are absent and leaves them to the person.
	"""
	missing = [f'profile/{name}' for name in PROFILE_FILES if not (ws.profile / name).is_file()]
	if not missing:
		report.ok('profile', ', '.join(PROFILE_FILES))
		return
	report.note(
		'profile',
		f'missing {", ".join(missing)} — add them (not fatal; an agent reads them)',
	)


def _check_boards(report: Report, reg: registry.Registry, settings: config.Config) -> None:
	"""One HEAD request per enabled board, to the board's homepage.

	The homepage, not the API: the question is "does this site answer
	at all from here?", and a board whose API changed still has a
	reachable site. A board that declares no HOMEPAGE is skipped by
	name, because there is nothing to request and no fault to report.
	"""
	enabled = reg.enabled(settings)
	if not enabled:
		report.note('boards', 'no boards are enabled — check [sources].disabled in the config')
		return

	failed = 0
	for board in enabled:
		label = f'board {board.name}'
		if not board.homepage:
			report.note(label, 'no HOMEPAGE declared — not checked')
			continue
		ok, detail = _reachable(board.homepage, settings.network.user_agent)
		if ok:
			report.ok(label, f'{board.homepage} ({detail})')
		else:
			failed += 1
			report.problem(
				label,
				f'{board.homepage} — {detail}',
				f'the site may be down or refuse HEAD; '
				f'`{workspace.PROGRAM} sources test {board.name}` tries the real request',
			)
	if failed == 0:
		report.ok('boards', f'{len(enabled)} enabled, every homepage answered')


def _reachable(url: str, user_agent: str = '') -> tuple[bool, str]:
	"""One HEAD request: did it answer, and with what status.

	Redirects are followed by urllib, so a board that moved still
	counts as reachable; a 4xx/5xx is the board's answer and is
	reported as the failure it is. The User-Agent is the configured
	`[network] user_agent`, or the same browser string the scrapers
	send by default, because several boards 403 the default Python one
	even for a homepage.
	"""
	request = urllib.request.Request(
		url,
		method='HEAD',
		headers={'User-Agent': user_agent or plugin_http.USER_AGENT},
	)
	try:
		with urlopen(request, timeout=BOARD_TIMEOUT) as response:
			return True, f'HTTP {response.status}'
	except urllib.error.HTTPError as error:
		return False, f'HTTP {error.code}'
	except (OSError, ValueError) as error:
		return False, _reason(error)


def _reason(error: BaseException) -> str:
	"""One line for a failure: its type and message, whitespace folded."""
	text = _one_line(error)
	return f'{type(error).__name__}: {text}' if text else type(error).__name__


def _one_line(error: object) -> str:
	"""A multi-line message (a config error names a path, a key, a fix)
	folded to one line, because each check is one line of the report."""
	return ' '.join(str(error).split())
