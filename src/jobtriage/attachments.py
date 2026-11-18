"""How an attachment's path is resolved, stored and typed.

`jobtriage attach` and the MCP server's `attach` tool answer the same
three questions about a file — which path did the caller mean, what
should the database keep, and is under `letters/` — so the answers live
here once. The CLI and the server differ only in how they report the
result; a second copy of the path rules would let them attach the same
letter under two spellings.

A relative path resolves from the op folder, which is how a skill writes
it (`jobtriage attach 12 letters/acme-engineer.md`) whatever directory
the agent's shell is in. The stored form is workspace-relative when the
file lives in or under the op folder, absolute otherwise: the common
case stays portable if the folder is moved, and an external file is
recorded honestly.
"""

from pathlib import Path


def resolve_path(raw: str, root: Path) -> Path:
	"""The file PATH names, resolved from the op folder when relative.

	A missing file is an OSError with the resolved path in the message:
	`Path.resolve` is not strict, and `is_file` is the check that turns
	"nope.md" into the sentence a person can act on. Directories are
	refused too — a row pointing at a folder is not an attachment.
	"""
	path = Path(raw).expanduser()
	if not path.is_absolute():
		path = root / path
	path = path.resolve()
	if not path.is_file():
		raise OSError(f'not a file: {path} — write it first, or check the path')
	return path


def stored_path(target: Path, root: Path) -> str:
	"""The path as the database keeps it: relative inside the op folder."""
	try:
		return target.relative_to(root.resolve()).as_posix()
	except ValueError:
		return str(target)


def default_kind(target: Path, root: Path) -> str:
	"""`cover_letter` for letters/, `file` for everything else.

	The letters folder is the plan's named use; the inference saves the
	cover-letter skill from passing the same flag on every run while
	keeping other attachments honestly generic.
	"""
	letters = (root.resolve() / 'letters').resolve()
	return 'cover_letter' if target.is_relative_to(letters) else 'file'
