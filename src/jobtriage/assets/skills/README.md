Agent skills for this workspace.

One directory per skill, each with a `SKILL.md`: YAML front matter
(`name`, `description`) and the instructions. `references/` holds the
detail the instructions point at; `scripts/` would hold code, but none
of the shipped skills needs one.

Start with `jobtriage/SKILL.md`. The agent discovery folders
`.agents/skills`, `.claude/skills` and `.opencode/skills` link here, so
an agent opened in this folder finds them without configuration.

These files are shipped by `jobtriage init` and refreshed by
`jobtriage init --upgrade`: an untouched skill is replaced, an edited
one is left alone with the new version beside it as `<file>.new`.
