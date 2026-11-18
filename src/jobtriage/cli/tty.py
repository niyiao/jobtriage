"""TTY checks: ask a question only when a person can answer it.

Plan 05's agent rule: no prompts when stdin isn't a TTY. An agent, a
pipe or a test that reaches `input()` either hangs until its timeout or
writes the prompt into a log and reads an empty line — neither is a
workspace decision. The two prompt sites (main's missing-workspace
offer and init's setup questions) call this module, so the rule has one
spelling instead of two subtly different `isatty()` calls.

`interactive` asks about both ends because a prompt printed into a pipe
is invisible to the person typing the answer; init's questions only
need stdin to be a terminal — its setup output is prose on stdout by
design — so it uses the narrower check.
"""

import sys


def stdin_is_tty() -> bool:
	"""Is the input side a terminal?"""
	return sys.stdin.isatty()


def interactive() -> bool:
	"""Can a prompt be shown and answered: both ends are the terminal."""
	return sys.stdin.isatty() and sys.stdout.isatty()
