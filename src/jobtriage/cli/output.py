"""Shared output formatting for the CLI (moved from the old cli.py)."""

import json
from typing import Any


def print_json(value: Any) -> None:
	"""Print one JSON document, indented because both readers see it.

	An agent pipes `--json` into a parser, where indent is irrelevant;
	a person reads the same output in a terminal, where one long line
	is not. ensure_ascii keeps titles from boards with accents
	readable rather than as \\u escapes.
	"""
	print(json.dumps(value, indent=2, ensure_ascii=False))


def error_text(error: KeyError | ValueError) -> str:
	"""The one-line message for a failed lookup or a bad flag.

	KeyError's str() wraps the sentence in quotes, which makes
	`error: "unknown status slug 'to_aply'"` read like a bug; the
	argument is the message a caller wrote, so use it as is.
	"""
	if isinstance(error, KeyError) and error.args:
		return str(error.args[0])
	return str(error)


def print_jobs(jobs: list[dict]) -> None:
	"""Print one job per three lines: title/company, metadata, URL.

	A job with no date prints 'undated' where the date would go.
	"""
	for job in jobs:
		meta = ' · '.join(
			[(job.get('published') or 'undated')[:10]]
			+ list(job.get('locations') or [])
			+ ([job['salary']] if job.get('salary') else [])
		)
		print(f'{job["title"]} — {job["company"]}')
		print(f'  {meta}')
		print(f'  {job["url"]}')
