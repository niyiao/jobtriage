"""The tool's logging: one rotating file per workspace, and `-v` to see it.

Two audiences, two handlers:

* The **file** always records, at DEBUG, under the op folder's
  `.jobtriage/logs/jobtriage.log`. It rotates at about a megabyte and
  keeps three older copies, so a month of runs stays bounded and a bug
  report can attach the file without thinking about size. The path
  comes from `Workspace.log_file` — like every other path, the
  workspace module is the only place that knows where anything lives.
* **stderr** carries warnings by default, because that is where the
  CLI has always put them: the same one-line text, now through
  `logging` so the file gets a copy with the time and the logger name.
  `-v/--verbose` lowers the handler to DEBUG, which adds the request
  lines and the board-by-board detail.

The logger is `jobtriage`. Its stderr handler is installed at import,
because that is the CLI's baseline: a warning is one line where it has
always been. `setup()` is called by the CLI once the workspace is known
(a command with no workspace, like `--version`, has no file to write
to) and attaches the workspace's file beside it. The handler writes to
`sys.stderr` when it emits rather than capturing the stream at
creation: pytest's capture replaces `sys.stderr` between tests, and a
handler holding the original object would write past it. `setup()` is
idempotent within a process — the CLI test suite runs `main()` dozens
of times — and switching to another workspace's file closes the old
handler rather than leaving two files growing.

A workspace that cannot be written to (a read-only checkout, a full
disk) loses its log file and nothing else: logging is an accessory, so
the failure is swallowed and the command runs on.
"""

import logging
import logging.handlers
import sys

from jobtriage import __version__, workspace

LOGGER = 'jobtriage'
FILE_BYTES = 1_000_000
FILE_COPIES = 3

# Lowercase severity labels: the CLI's voice is lowercase throughout
# (`error:`, `warning:`, `skip:`), and an upper-case WARNING in the
# middle of it reads like a different program.
_LABELS = {
	logging.DEBUG: 'debug',
	logging.INFO: 'info',
	logging.WARNING: 'warning',
	logging.ERROR: 'error',
	logging.CRITICAL: 'critical',
}


class _StderrFormatter(logging.Formatter):
	"""`warning: <message>` — the line the CLI already printed."""

	def format(self, record: logging.LogRecord) -> str:
		return f'{_LABELS.get(record.levelno, record.levelname.lower())}: {record.getMessage()}'


class _StderrHandler(logging.Handler):
	"""A handler that looks up `sys.stderr` at emit time.

	`logging.StreamHandler` binds the stream in `__init__`; pytest's
	`capsys` swaps `sys.stderr` for its own object for the duration of
	a test, so a bound stream would be invisible to it. Reading the
	attribute per record is also what lets the CLI library be used
	under a caller's own capture without configuration.
	"""

	def __init__(self, level: int = logging.WARNING):
		super().__init__(level)
		self.setFormatter(_StderrFormatter())

	def emit(self, record: logging.LogRecord) -> None:
		try:
			print(self.format(record), file=sys.stderr)
		except Exception:  # noqa: BLE001 — the stdlib handler contract: report, never raise
			self.handleError(record)


logger = logging.getLogger(LOGGER)
logger.setLevel(logging.DEBUG)

# The one stderr handler for the process. Its level is the thing `-v`
# changes and the next `setup()` puts back.
_stderr = _StderrHandler()
logger.addHandler(_stderr)
_file: logging.handlers.RotatingFileHandler | None = None


def setup(ws: workspace.Workspace, *, verbose: bool = False) -> None:
	"""Point logging at this workspace's file and set stderr's level.

	The file records everything the package logs, whatever stderr is
	showing. `verbose` raises stderr from WARNING to DEBUG; a later
	non-verbose call (another command in the same process, a test)
	lowers it again, so the flag cannot leak forward.
	"""
	_stderr.setLevel(logging.DEBUG if verbose else logging.WARNING)
	_open_file(ws)
	logger.debug('%s %s · workspace %s', workspace.PROGRAM, __version__, ws.root)


def _open_file(ws: workspace.Workspace) -> None:
	"""Attach the workspace's rotating file, replacing another's."""
	global _file
	path = ws.log_file
	if _file is not None and _file.baseFilename == str(path):
		return
	_close_file()
	try:
		path.parent.mkdir(parents=True, exist_ok=True)
		handler = logging.handlers.RotatingFileHandler(
			path,
			maxBytes=FILE_BYTES,
			backupCount=FILE_COPIES,
			encoding='utf-8',
		)
	except OSError:
		return  # read-only workspace: no log file is not a failed command
	handler.setLevel(logging.DEBUG)
	handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
	logger.addHandler(handler)
	_file = handler


def _close_file() -> None:
	"""Detach and close the previous workspace's handler, if any."""
	global _file
	if _file is None:
		return
	logger.removeHandler(_file)
	_file.close()
	_file = None
