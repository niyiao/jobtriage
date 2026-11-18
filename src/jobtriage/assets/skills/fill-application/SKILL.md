---
name: fill-application
description: Use when the user wants an application form filled for a job, says "apply to job ID" or "fill this application", or asks to prepare an application in a jobtriage workspace. Fills the form from the profile in the op browser, attaches the resume and letter, and stops before submitting.
---

# Filling an application

Fill the form in the op browser, then hand it back. Submitting is the
user's action.

## Before opening the form

    jobtriage show ID --json

Read `title`, `company`, `url` and `attachments`. Read
`profile/RESUME.md` and `profile/DETAILS.md`; those are the only facts
you may enter. Check that `profile/resume.pdf` exists — it is the file
for upload fields. If there is a `cover_letter` attachment, the letter
goes in the form too; if there is none, ask whether to write one with
the `cover-letter` skill or apply without.

If the profile is missing a fact the form requires (notice period,
salary expectation, right to work, a start date), ask now rather than
at the field. Offer to save the answer to `profile/DETAILS.md` so the
next application does not ask again. Write it in the user's words.

## Open the job in the op browser

    jobtriage open ID

This opens the posting in the workspace's Chrome (`.browser-profile/`)
and starts that browser if it is not running. It prints the URL.

Attach agent-browser over CDP, headed:

    jobtriage config get browser.port        # default 9222
    agent-browser --cdp 9222 --pin-tab --session apply-<id> tab list
    agent-browser --cdp 9222 --pin-tab --session apply-<id> snapshot

Use the agent-browser skill for the commands themselves. Never launch a
fresh automation profile: it has no sessions, and sites treat it
differently from the real Chrome the user sees.

The rest of the application may be on the employer's ATS rather than
the board. Click through to it in the same tab and keep working there.

## Fill

Work from a snapshot of the form, not from what a form usually asks.
Labels change; the page is the truth.

- Name, email, phone, location: from `RESUME.md` / `DETAILS.md`.
- Resume upload: `profile/resume.pdf`. Use the PDF for file fields and
  the Markdown only when a form asks for text.
- Cover letter: the attached file under `letters/`, when the form has a
  field for it.
- Work history, education, links: from the resume.
- Work authorisation, notice period, salary, start date: from
  `DETAILS.md`, exactly as written. If a range is asked for and the
  profile gives a number, ask before widening it.
- Free-text "why this role": write it from the posting and the profile,
  in the posting's language, in the same voice as the cover letter. Use
  the `writing-style.md` rules.
- Demographic and voluntary-disclosure sections: leave them for the
  user unless they have said how to answer. They are optional and
  personal.

Do not paste the resume into a textarea that is really a file upload.
Do not invent a company, a date, a degree or a number. An empty required
field is a question for the user, not a gap to fill plausibly.

## Multi-page forms

Click `Next`, `Continue` or `Save and continue` to move between pages —
those are navigation, not submission. Check for required-field errors
after each click. Keep a short list of what is filled and what is still
open, so the handoff is accurate.

`references/ats-forms.md` has the shapes of Greenhouse, Lever, Ashby and
Workday forms and where they tend to hide the real submit button.

## Login walls

If the form asks for an account or a sign-in you do not already have:

1. Stop.
2. Ask the user to sign in in the visible browser window, or to create
   the account themselves.
3. Resume only after they confirm.

Never enter credentials, never create an account, never use a password
from a file. Sessions in `.browser-profile` persist, so this is usually
a one-time step per site.

## Stop before submitting

The review page is the finish line. Do not click `Submit application`,
`Apply`, `Send` or anything that sends the form. Instead, hand back:

- the job and company;
- what is filled, and what is still blank or marked required;
- which files are attached (resume, letter);
- any answer you were unsure about;
- the browser tab left open on the review page.

Then wait. When the user says they submitted, record it:

    jobtriage status ID applied
    jobtriage note ID "applied via Greenhouse"

`status` writes `applied` and its history row. If the user explicitly
asks you to submit in this session, re-read the review page once and
submit only then; an earlier "looks good" about the fields is not that
request.

## Rules

- Fill with the browser; write with `jobtriage status`, `note` and
  `attach`. No SQL writes.
- Never submit, email or create an account without the user saying so
  in this session.
- Only profile facts. Ask for what is missing.
- The op browser only: `.browser-profile`, port from the config, headed
  and attached.
