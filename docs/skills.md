# Skills and the MCP server

Two ways an agent can drive the tool. **Skills** are instructions in the
op folder: an agent that reads `SKILL.md` files learns the commands and
the rules, and uses the CLI. The **MCP server** is for a client without
skill support: it exposes the same reads and writes as tools over stdio.

## The skills

Source: `src/jobtriage/assets/skills/<name>/`. `jobtriage init` copies
them to `<op>/skills/`, and `jobtriage skills` lists what is shipped,
installed and available in the agent folders.

| Skill | Use it for |
| --- | --- |
| `jobtriage` | the umbrella: commands, status vocabulary, workspace layout, which skill handles a task |
| `jobtriage-db` | counts and reports: list filters, the schema, read-only SQL recipes |
| `triage` | going through the new queue and choosing what to apply to |
| `cover-letter` | writing, saving under `letters/` and attaching a letter for one job |
| `fill-application` | filling an application in the op browser, stopping before submit |
| `add-scraper` | researching a board and writing or fixing a scraper plugin |
| `tune-filters` | changing locations, families, age or the title filter |
| `setup` | onboarding a fresh profile from a resume |

Each directory holds a `SKILL.md` (front matter with `name` and a
triggering `description`, then the instructions) plus optional
`references/`. The umbrella's `references/cli.md` and the db skill's
`references/schema.md` are generated, and a test fails when they drift
from the CLI and the database.

### Discovery

`init` links the three folder conventions to the one copy in
`<op>/skills/`:

    <op>/.agents/skills    -> ../skills
    <op>/.claude/skills    -> ../skills
    <op>/.opencode/skills  -> ../skills

`CLAUDE.md` links to `AGENTS.md` for the same reason. An agent opened
anywhere in the op folder finds both. On Windows, where symlinks need a
privilege, `init` copies instead and `init --upgrade` keeps the copies
in step.

### Global install

For an agent run outside the op folder:

    jobtriage skills install --global --agent claude|opencode|codex|all

That copies the workspace's skills into `~/.claude/skills`,
`~/.codex/skills` and `$XDG_CONFIG_HOME/opencode/skills` (default
`~/.config/opencode/skills`). It is explicit, never part of `init`: a
user-level folder belongs to the agent installation, not this workspace.

## The MCP server

    jobtriage mcp

The command speaks MCP over stdin/stdout; the client launches it as a
server. There is no port and no prompt. The workspace resolves the same
way as every other command, so give the client the folder or the flag:

```json
{
  "mcpServers": {
    "jobtriage": {
      "command": "jobtriage",
      "args": ["mcp"],
      "env": {"JOBTRIAGE_HOME": "/path/to/op"}
    }
  }
}
```

The tools are thin wrappers over the CLI's own functions: use them
instead of SQL. They never submit an application, send email or create
an account.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `list_jobs` | `status`, `source`, `query`, `limit` (default 50) | `{jobs, count}`; the rows `list --json` prints |
| `get_job` | `id` | the job, plus `history` and `attachments`, as `show --json` |
| `set_status` | `id`, `status`, optional `note` | `{id, old, status, changed, notes?}`; actor recorded as `agent` |
| `add_note` | `id`, `note` | `{id, notes}` after the append |
| `attach` | `id`, `path`, optional `kind` | `{id, path, kind, created}`; relative paths resolve from the op folder |
| `scrape` | optional `limit`, `source` | `{source, relevant, new, seen, total, boards}`; hits the network and writes |
| `filter_test` | `title`, optional `preset` | `{title, kept, gate, word, role, verdict, notes}` |

Statuses are `new`, `skipped`, `to_apply` and `applied`. A failure (a
missing id, a bad slug, an unknown board) comes back as an MCP error
result with the CLI's one-line message, not a traceback.

## Which to use

Prefer the skills inside the op folder: they carry the workflow rules
(triage in conversation, stop before submitting, letters in `letters/`)
that a tool list cannot. Use the MCP server when the client cannot read
skills, or when a surface of typed reads and writes is the point.
