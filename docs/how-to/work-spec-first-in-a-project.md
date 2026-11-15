# Work spec-first in a project

Give a code project a Diataxis `docs/` tree, specs that outlive any one
session, decision records, and an `AGENTS.md` that tells agents how to use them.
This is separate from your personal data repo: everything below is committed
with the project.

## Set it up

From anywhere inside the project:

```bash
kb project init      # docs/{tutorials,how-to,reference,explanation,specs,decisions}
kb agents init       # AGENTS.md with a managed workflow block, CLAUDE.md that imports it
kb agents init --hook   # also resume specs automatically at the start of each Claude Code session
kb agents init --guard  # Claude Code asks you before an agent approves a spec or uses --force
```

Existing files are never overwritten. If the project already has a
`CLAUDE.md`, `kb agents init` leaves it alone and says so; add the import with
`kb agents init --link`. `kb agents check` reports what to fix, and
`kb project status` shows the whole picture.

## Write and approve a spec

```bash
kb spec new "Add login"     # docs/specs/0001-add-login.md, status draft
```

Fill in Requirements (each with an `Accept:` line) and Tasks. Then:

```bash
kb spec status 1 approved
```

The move is refused until the spec has a filled-in requirement, an acceptance
criterion, and at least one task. Review the spec yourself before approving.

## Implement across sessions

```bash
kb spec status 1 implementing
kb spec task 1 T1           # tick a task when it is finished (ids are stable; inserting tasks is safe)
kb spec resume              # next session: what is active and what is next
kb spec status 1 done       # refused while any task is unticked
```

Run `kb spec todos 1` to mirror the open tasks into beads issues; ticking a task then closes its issue.
`--force` skips a gate when you need to. Use `kb spec status <id> superseded
--by <id>` when a newer spec replaces one.

## Record a decision

```bash
kb adr new "Use SQLite for the index"
kb adr status 1 accepted
```

Link the ADR from the spec's Design section.

## Keep the docs tidy

```bash
kb docs search "rate limit"            # is there already a page?
kb docs new how-to "Deploy to staging" # skeleton in the right quadrant
kb docs check                          # misfiled, unlinked pages, broken links
```

`--hook` and `--guard` write `.claude/settings.json`, which you would normally commit so the whole team gets it. The guard matches the command as typed, so it is a prompt on the normal path rather than a sandbox; check `/permissions` to see the rules loaded.

If two branches each create the next spec and you merge them, `kb project status` and `kb docs check` report the duplicate id; fix it with `kb spec renumber <slug>`.

See [explanation: spec-driven development](../explanation/spec-driven-development.md)
for why the flow is shaped this way, and the [CLI reference](../reference/cli.md)
for every flag.
