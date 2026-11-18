---
name: setup
description: Use when the workspace is new, the profile is empty, or the user says "set me up" or asks how to start in a jobtriage workspace. Turns a resume into profile/RESUME.md, interviews for DETAILS.md, sets the search locations and families, and runs a first explained scrape.
---

# Setting up

Onboarding is a conversation, not a form. The result is a profile the
other skills can trust, a config that matches the person, and one
explained scrape they have seen.

## 0. The folder

    jobtriage where

If that fails with `no workspace`, ask where the folder should live
(default `~/jobtriage`) and run `jobtriage init`. If it succeeds but
`profile/` has no `RESUME.md`, this is a fresh profile; continue here.

If anything else looks wrong — a board missing, a config error, no
browser — run `jobtriage doctor` before guessing: it names the fix.

Read `AGENTS.md` in the folder for the ground rules you will follow.

## 1. The resume

Ask for the resume as a PDF, a LinkedIn export, or pasted text. Read it
and write `profile/RESUME.md`:

- Name and contact line at the top.
- A short summary, only if the resume has one or the user wants one.
- Experience, newest first: role, company, dates, and the bullets that
  carry a result. Keep the numbers exactly as written.
- Projects, skills, education, languages — what the resume has, in the
  order it has.
- Plain Markdown headings. This file is read by agents and by the user;
  it is not the formatted resume.

Ask before dropping anything. If the resume is a PDF with a layout that
does not extract cleanly, read it directly rather than reconstructing
dates from memory. Copy the PDF to `profile/resume.pdf` with the user's
agreement; it is the file upload fields use. Do not invent a date, a
title, a degree or a number that the resume does not contain.

## 2. The details interview

`profile/DETAILS.md` ships blank with four headings. Fill it by asking,
a few questions at a time, and writing the answers in the user's words:

- **Who I am** — discipline, years, the kind of company and product.
- **How I work** — team size, autonomy, remote habits, what they want
  more or less of.
- **Evidence** — the concrete claims a letter can use: products shipped,
  migrations, numbers, teams led, talks, open source. Ask for the scale
  where it is missing.
- **Logistics** — notice period, right to work, salary expectations,
  location and time zone, earliest start.

These answers are what a form or a letter asks for later. A missing
answer is a question to ask, not a line to leave vague. Do not put
anything in this file that the user did not say.

`writing-style.md` in the folder root holds the prose rules for letters.
Read it once; ask whether they want to change it, and leave it alone if
they do not.

## 3. The config

Set the two keys that decide what a scrape keeps:

    jobtriage config set search.locations '["emea", "GB"]'
    jobtriage config set search.families '["software"]'

Locations are `worldwide`, a region token (`emea`, `europe`, `apac`,
`latam`, …) or an ISO country code (`GB`, `NG`, `US`). Ask which places
they can actually work from; "remote" is not a location without a
country or region. Families are role families (`software`, `design`,
`product`, `data`, `devops`, `marketing`, or a slug the boards declare).

If `jobtriage init` already asked these questions and wrote the answers,
confirm them with `jobtriage config get search.locations` and
`jobtriage config get search.families` instead of asking again.

Leave the title filter at `any` — or whatever preset the user chose at
init — for the first run. It is easier to add a filter after seeing what
arrives; `tune-filters` does that with the user. Do not set
`max_age_days` or `limit` unless the user has an opinion; the defaults
are reasonable.

## 4. The first scrape

Run it with the explanation on, and read the output together:

    jobtriage scrape --explain

For each board, the line says `fetched`, then how many the age gate,
the title filter and duplication removed, and how many were kept. If a
board fails, the line names the error; a broken board is not a reason
to stop the others. Then:

    jobtriage list --status new --json

Report how many jobs arrived and from which boards. Offer the `triage`
skill for the next step. If the count is zero, check the locations and
families before changing the filter: a wrong location is the usual
cause.

## Done

`profile/RESUME.md`, `profile/DETAILS.md`, `profile/resume.pdf` (when
there is a PDF), `search.locations` and `search.families` set, and one
scrape the user has seen explained. Tell them what is in each file and
what to run next.

## Rules

- Facts come from the user and their resume. Ask, do not infer.
- Do not apply, email or create accounts during setup.
- Write config through `jobtriage config set`; it validates the value.
- One question at a time is slower than a list of five; use short lists
  and let the user answer in their own order.
