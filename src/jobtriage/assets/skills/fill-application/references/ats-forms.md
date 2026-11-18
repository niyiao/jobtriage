# ATS form notes

The four systems most of these applications use. Forms are rebuilt
often, so treat this as a map of the terrain, not a list of selectors:
read the labels on the page, and let the snapshot decide.

## Greenhouse

One long page on `job-boards.greenhouse.io` or the company's own
domain. Resume upload sits at the top and usually fills name, email,
phone and links from the file — check every autofilled field anyway,
because PDF parsing drops hyphens and accents. Custom questions follow,
then optional demographic questions. `Submit application` at the bottom
is the real send; `Apply` on some skins opens this page, so do not treat
the first click as submission.

## Lever

One page on `jobs.lever.co`. `Resume/CV` upload autofills the basic
fields. The form is shorter than Greenhouse's; custom questions are
inline. `Submit application` is the send. Some postings offer
`Apply with LinkedIn`, which signs in and imports a profile — leave that
choice to the user.

## Ashby

`jobs.ashbyhq.com`. Usually two steps: a profile step with resume upload
and basic details, then application questions. `Continue` moves between
steps; `Submit application` is last. Ashby keeps answers in the URL or
in local storage, so a reload usually does not lose the page.

## Workday

`*.myworkdayjobs.com`. The heaviest form: an account is required before
the application, then `My Information`, `My Experience`, `Application
Questions`, `Voluntary Disclosures` and `Review`. A resume upload can
create work-history rows, which need checking against the profile.
Sessions time out — say so if the user walks away. Account creation is
the user's: stop and hand over at that step. The final button is
`Submit` on the Review page; `Save and Continue` on earlier pages is
navigation.

## Everywhere

- The posting on the job board is often not the form; the `Apply` button
  leads to the ATS. Keep one tab for the whole application.
- File inputs usually need the real file path. Use
  `<op>/profile/resume.pdf` and the letter under `<op>/letters/`.
- A required field marked with `*` and an empty value blocks the review
  page. Ask rather than invent.
- `Back` is safe. The final submit is the only irreversible button on
  the page.
