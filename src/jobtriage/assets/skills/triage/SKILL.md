---
name: triage
description: Use when the user wants to go through new jobs, clear the triage queue, or ask "what should I apply to?" in a jobtriage workspace. Reads the new jobs and the profile, suggests to apply or skip with a reason each, and writes statuses only after the user agrees.
---

# Triaging new jobs

The queue is every job on the `new` status: ingested by a scrape,
nobody has decided about it. The job here is to turn that queue into
`to_apply` and `skipped` decisions the user owns.

Do not open with a status change. The user decides; you prepare the
decision and make the write afterwards.

## 1. Read the queue and the person

    jobtriage list --status new --json

The default is 50 jobs, newest first. If the answer is empty, say so and
offer `jobtriage scrape` — an empty queue usually means no scrape has
run.

Read `profile/RESUME.md` and `profile/DETAILS.md` before judging fit.
These are the only source of facts about the user: skills, seniority,
locations, right to work, notice period, salary expectations. If the
profile is empty, stop and point at the `setup` skill.

## 2. Judge each job

The JSON has `id`, `title`, `company`, `role`, `published`, `locations`,
`salary` and `url`. That is often enough: a title that names the stack
and a location the user can work from.

When it is not enough — a vague title, a company the profile does not
explain, a location that might mean "remote in one country" — fetch the
posting:

    jobtriage open ID        # the op browser, for logins or JavaScript
    curl -sL "$URL"          # a public page; read the text

With `open`, use the agent-browser skill over the op browser:
`jobtriage config get browser.port` (default 9222), then attach with
`--cdp PORT`. Read the responsibilities, the requirements, the location
line and the employment type.

Classify with a one-line reason, in the user's terms:

- **to_apply** — matches the profile's role and level, a location the
  user can work from, and no requirement the profile contradicts.
- **skipped** — a mismatch the profile can name: wrong discipline,
  seniority far off, onsite in a country the user cannot work in,
  a stack the profile does not have, a contract type they excluded.
- **ask** — genuinely uncertain (an unusual title, a location phrased
  ambiguously, a requirement that might be met). Put it in a short list
  and ask; do not guess.

Never skip a job because the *market* is bad, the company is unfamiliar,
or the posting is old. Those are not facts from the profile. Age already
has its own gate at scrape time.

## 3. Present before writing

Give the user a compact list:

    12  Senior Frontend Engineer — Acme (Remote, EU)   to_apply  React/TS match, EU remote
    13  Staff Engineer — Globex (Remote, US only)      skipped   US-only, profile needs EU
    14  Product Engineer — Initech (Hybrid, Berlin)    ask       hybrid; is Berlin reachable?

Then ask them to confirm, change, or drop entries. Wait for the answer.
Do not write statuses while presenting, and do not treat silence or a
general "looks good" about the list as approval for entries they did
not mention.

## 4. Write what was agreed

    jobtriage status 12 to_apply
    jobtriage status 13 skipped

The command prints the transition (`12: new → to_apply`) and records it
in the job's history. If the user changes their mind later, run the
command again with the other status; setting the same status twice is a
no-op.

After a batch, `jobtriage list --status new --json` shows what is left.
Report the counts, not the whole list again.

## 5. When the queue keeps disagreeing

If most jobs in a batch are skipped for the same reason — the board
sends US-only roles, the titles are all management, the stack is wrong
— say so once, with the count, and offer the `tune-filters` skill. Do
not edit `jobtriage.toml` from here: the user should see the proposed
filter change before it is made.

## Rules

- Write with `jobtriage status`; never with SQL.
- Facts about the user come from `profile/` only. Ask for anything
  missing; never fill a gap with a plausible guess.
- Do not apply, email or create accounts in this workflow. Triage ends
  at `to_apply`; applying is the `fill-application` skill.
