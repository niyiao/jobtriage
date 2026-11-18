#!/usr/bin/env python3
"""Turn the nightly canary's JSON into one GitHub issue per broken board.

The canary workflow runs `jobtriage sources test --all --json` against
the live boards, writes the document to a file, and hands it to this
script. Every board whose test failed gets one issue titled
`canary: <board> broken`; an issue that is already open is updated in
place (one issue per board, not one per night), and an open issue whose
board answers again is closed with the run that saw it recover. The
issue lifecycle is the "last success" record: a board is green exactly
when its issue is closed, and the comment names the run.

Only stdlib: the workflow runs this from a checkout, so it must not need
a second environment. `GITHUB_TOKEN` and `GITHUB_REPOSITORY` are the
workflow's own environment; `--repo`/`--token` exist for a local dry run
of the decision logic, and `--dry-run` prints the actions without
calling the API.

Usage:

    python scripts/canary_report.py canary.json
    python scripts/canary_report.py canary.json --dry-run

Exit status: 0 when the report was filed (or printed), 1 when the file
cannot be read or the API refuses a call. The boards' own failures are
what the issues are for; they do not make this script fail a second
time.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = 'https://api.github.com'
LABEL = 'canary'
TITLE = 'canary: {name} broken'


class ReportError(Exception):
	"""The document or the API made filing impossible; one line for the log."""


def failures(document: dict) -> list[tuple[str, str]]:
	"""Every broken board as (name, reason), in report order.

	`broken` holds files that never imported — the registry never got a
	Board to run — and `boards` holds the ones that ran and failed.
	Both are the canary's business; a board that returned valid jobs is
	not. Contract problems make a run not-ok even when it returned, so
	the reason names the first one.
	"""
	found = []
	for name, reason in sorted(document.get('broken', {}).items()):
		found.append((name, f'plugin broken: {reason}'))
	for row in document.get('boards', []):
		if row.get('ok'):
			continue
		reason = row.get('error')
		if not reason:
			problems = row.get('problems') or []
			reason = f'{row.get("dropped", 0)} invalid row(s)'
			if problems:
				reason = f'{reason}; first: {problems[0]}'
		found.append((row.get('name', '?'), reason))
	return found


def healthy(document: dict) -> list[str]:
	"""Every board that ran and passed; these close their open issues."""
	return [row['name'] for row in document.get('boards', []) if row.get('ok') and row.get('name')]


def issue_body(name: str, reason: str, link: str) -> str:
	"""One board's issue: what failed, how to see it, and the run."""
	lines = [
		f'The nightly canary could not get jobs from **{name}**.',
		'',
		'    ' + ' '.join(str(reason).splitlines()),
		'',
		f'Run: {link}',
		'',
		(
			f'`jobtriage sources test {name}` reproduces it from a workspace. '
			'This issue closes by itself when the board answers again, so its '
			'closed state is the last successful night.'
		),
	]
	return '\n'.join(lines)


class Client:
	"""The three REST calls the report needs, nothing more."""

	def __init__(self, repo: str, token: str, *, dry_run: bool = False):
		self.repo = repo
		self.token = token
		self.dry_run = dry_run

	def request(self, method: str, path: str, payload: dict | None = None):
		if self.dry_run:
			return None
		body = json.dumps(payload).encode() if payload is not None else None
		request = urllib.request.Request(
			f'{API}/repos/{self.repo}{path}',
			data=body,
			method=method,
			headers={
				'Accept': 'application/vnd.github+json',
				'Authorization': f'Bearer {self.token}',
				'X-GitHub-Api-Version': '2022-11-28',
				'User-Agent': 'jobtriage-canary',
				'Content-Type': 'application/json',
			},
		)
		try:
			with urllib.request.urlopen(request, timeout=30) as response:
				payload = response.read().decode()
		except urllib.error.HTTPError as error:
			detail = error.read().decode('utf-8', 'replace')[:300]
			raise ReportError(f'{method} {path}: HTTP {error.code}: {detail}') from None
		except OSError as error:
			raise ReportError(f'{method} {path}: {error}') from None
		return json.loads(payload) if payload else None

	def open_issues(self) -> dict[str, dict]:
		"""Open canary issues by title, a page at a time."""
		issues: dict[str, dict] = {}
		page = 1
		while True:
			batch = self.request(
				'GET',
				f'/issues?state=open&labels={LABEL}&per_page=100&page={page}',
			)
			if not batch:
				break
			for issue in batch:
				issues[issue['title']] = issue
			if len(batch) < 100:
				break
			page += 1
		return issues

	def open(self, name: str, body: str) -> None:
		self.request(
			'POST', '/issues', {'title': TITLE.format(name=name), 'body': body, 'labels': [LABEL]}
		)

	def update(self, issue: dict, body: str) -> None:
		if issue.get('body') != body:
			self.request('PATCH', f'/issues/{issue["number"]}', {'body': body})

	def close(self, issue: dict, comment: str) -> None:
		self.request('POST', f'/issues/{issue["number"]}/comments', {'body': comment})
		self.request('PATCH', f'/issues/{issue["number"]}', {'state': 'closed'})


def run_link() -> str:
	"""The workflow run, from the environment Actions provides."""
	server = os.environ.get('GITHUB_SERVER_URL', 'https://github.com')
	repo = os.environ.get('GITHUB_REPOSITORY', 'niyiao/jobtriage')
	run = os.environ.get('GITHUB_RUN_ID', 'local')
	return f'{server}/{repo}/actions/runs/{run}'


def report(document: dict, client: Client) -> int:
	"""File one issue per broken board; close the ones that recovered."""
	broken = failures(document)
	alive = set(healthy(document))
	issues = client.open_issues()
	for name, reason in broken:
		body = issue_body(name, reason, run_link())
		issue = issues.get(TITLE.format(name=name))
		if client.dry_run:
			print(f'{"update" if issue else "create"}: {TITLE.format(name=name)} — {reason}')
			continue
		if issue is None:
			client.open(name, body)
			print(f'opened: {TITLE.format(name=name)}')
		else:
			client.update(issue, body)
			print(f'updated: {TITLE.format(name=name)}')
	for title, issue in sorted(issues.items()):
		name = title.removeprefix('canary: ').removesuffix(' broken')
		if name not in alive:
			continue
		comment = f'Green again in {run_link()}. Closing; this is the new last success.'
		if client.dry_run:
			print(f'close: {title}')
			continue
		client.close(issue, comment)
		print(f'closed: {title}')
	return 0


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(
		prog='canary_report.py',
		description='file one GitHub issue per broken board from the canary JSON',
	)
	parser.add_argument('report', help='the JSON `sources test --all --json` printed')
	parser.add_argument('--repo', default=os.environ.get('GITHUB_REPOSITORY', ''))
	parser.add_argument('--token', default=os.environ.get('GITHUB_TOKEN', ''))
	parser.add_argument('--dry-run', action='store_true', help='print the actions, call no API')
	args = parser.parse_args(argv)

	try:
		document = json.loads(Path(args.report).read_text(encoding='utf-8'))
	except (OSError, ValueError) as error:
		print(f'error: {args.report}: {error}', file=sys.stderr)
		return 1
	if not isinstance(document, dict) or 'boards' not in document:
		print(f'error: {args.report}: not a `sources test --all --json` document', file=sys.stderr)
		return 1
	if not args.dry_run and (not args.repo or not args.token):
		print('error: GITHUB_REPOSITORY and GITHUB_TOKEN are needed (or --dry-run)', file=sys.stderr)
		return 1

	client = Client(args.repo, args.token, dry_run=args.dry_run)
	try:
		return report(document, client)
	except ReportError as error:
		print(f'error: {error}', file=sys.stderr)
		return 1


if __name__ == '__main__':
	raise SystemExit(main())
