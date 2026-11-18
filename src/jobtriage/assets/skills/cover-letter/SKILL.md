---
name: cover-letter
description: Use when the user asks for a cover letter for a job, gives a job id or a posting URL, or wants a letter written or rewritten in a jobtriage workspace. Resolves the job, reads the profile and posting, writes the letter in the user's style, saves it under letters/ and attaches it.
---

# Cover letters

One letter for one application. It is a short note beside the resume,
not a second resume and not an essay.

## 1. Resolve the job

    jobtriage show ID --json

The JSON has `id`, `title`, `company`, `role`, `url`, `locations`,
`published` and `attachments`. If the user gave a company or a title
instead of an id:

    jobtriage list --search "Acme" --json

and pick the job with them rather than guessing between rows. If
`attachments` already has a `cover_letter`, say so and ask whether to
replace it before writing anything.

## 2. Read the posting

Fetch `url`. A public page is fine with `curl -sL`; if it needs a login
or JavaScript, use the op browser and the agent-browser skill:

    jobtriage open ID
    jobtriage config get browser.port      # default 9222
    agent-browser --cdp 9222 --pin-tab --session letter-<id> open <url>

You need three things from it: what the company builds, what the role
owns, and the language the posting is written in. Those decide the
letter's content and its language. Do not use the job board's summary
alone when the posting is one click away.

## 3. Read the person

- `profile/RESUME.md` — roles, projects, stack, education.
- `profile/DETAILS.md` — the interview answers: how they work, evidence
  with numbers, notice period, right to work, salary expectations.
- `writing-style.md` in the op folder root — the prose rules.
- `jobtriage config get letters.paragraphs` — how many body paragraphs
  the user wants (default 2).

Facts come only from these files. If the letter needs a fact that is
not there — a metric, a start date, a visa status — ask. Never invent
one, and never infer a number from a job title.

## 4. Write it

- Write in the posting's language. A German posting gets a German
  letter; a French one gets French. If the posting is English, write
  English.
- Address a person when the posting names the hiring manager or
  recruiter; otherwise `Dear hiring team` (or the equivalent in the
  posting's language).
- Body length is `letters.paragraphs` paragraphs. The first connects
  the user's actual work to what this company builds. The rest give one
  or two pieces of evidence from the profile and say why this role.
- Match the writing style file: plain words, concrete details, varied
  sentence length, no ornament for its own sake. Formal documents carry
  no humour, no exclamation marks and no jokes.
- Do not repeat the resume line by line, do not summarise the job back
  to them, and do not claim enthusiasm you cannot source from the
  profile.
- Close with the user's name and one practical line: availability,
  notice period or right to work, only if `DETAILS.md` says it.

Sign with the name as it appears in the resume. Do not add a phone
number or address that is not in the profile.

## 5. Save, attach, show

Slug the filename: lowercase ASCII, words joined by hyphens, drop
punctuation and legal suffixes (`Acme, Inc.` → `acme`; `Senior Frontend
Engineer (Remote)` → `senior-frontend-engineer`).

    letters/acme-senior-frontend-engineer.md

The file is the letter, with a greeting line, the body paragraphs, a
closing and the name. Write it in one pass; do not leave placeholders
in the saved file.

    jobtriage attach 12 letters/acme-senior-frontend-engineer.md

`attach` records the file against the job and gives it the kind
`cover_letter` because it lives under `letters/`. If the file already
exists, ask before overwriting; a second version belongs in a new file,
not on top of the first.

Then show the user the letter text and the path. Do not set the job to
`applied`: attaching the letter is not applying. `triage` or
`fill-application` take it from here.

If the user wants the facts traced, end the file with an HTML comment
listing the profile facts used:

    <!-- facts: 6 years React at Acme (RESUME); led design-system migration (DETAILS) -->

It is invisible in the rendered letter and cheap for the user to check.

`references/example-letter.md` is one letter for a made-up person. Read
it for shape, not for content; every fact in it is invented and none of
it belongs to this user.
