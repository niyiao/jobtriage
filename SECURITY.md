# Security

jobtriage runs on your machine and talks to the job boards you enabled.
There is no account, no server of ours and no telemetry.

## The trust boundary

- **Workspace plugins are code.** Every `.py` in `<op>/scrapers/` is
  imported and runs with your user's permissions, the same as anything
  else you run. Only put files there that you trust, and read a plugin
  before adding it. Nothing is ever imported from the current
  directory: installed packages and the op folder are the only two
  sources of plugins, and `jobtriage sources` names where each board
  came from.
- **Credentials are not stored by the tool.** The op folder's `.env`
  is for values you give other tools; board requests carry no
  authentication. The apply browser uses its own profile under
  `.browser-profile/`, which contains the cookies of whatever sites you
  signed into; treat that folder like a browser profile.
- **HAR files contain auth tokens.** If you capture a board's traffic
  while researching a scraper (see `docs/scrapers.md`), the exported
  HAR holds cookies and authorization headers. Extract the API shape,
  then delete the file; never commit it or attach it to an issue.
- The only other file the tool writes outside the op folder is the
  one-line pointer in your platform config directory that says where
  the folder is — unless you run `jobtriage skills install --global`,
  which copies the skills into the agent folder you asked for. Delete
  the op folder and everything else it knows is gone.

## Reporting a vulnerability

Report privately through GitHub Security Advisories — Security →
Advisories → "Report a vulnerability" — on the repository once it is
public. Please do not open a public issue for a vulnerability.

Supported versions: the latest release. A fix ships in a patch or
minor release, with the details in `CHANGELOG.md` once the report is
resolved.
