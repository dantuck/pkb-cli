# Spec-driven development with kb

An agent session starts with no memory. Whatever it needs to continue has to
be on disk, in a place it reads at the start. `kb` puts three things there.

**Specs** (`docs/specs/`) say what is being built and how you will know it is
done. They are plain markdown in the project's own git history, so they are
reviewed, branched and merged with the code. A status lifecycle (draft,
approved, implementing, done) and a task checklist turn "where were we?" into
`kb spec resume`. The gates are deliberately small: a spec cannot be approved
without an acceptance criterion and a task, and cannot be marked done with
tasks open. They exist to make skipping the thinking visible, not to police it.

**Decision records** (`docs/decisions/`) keep the *why* of a choice after the
discussion that produced it is gone. They are kept after being superseded.

**Diataxis docs** (`docs/{tutorials,how-to,reference,explanation}`) hold the
long-lived documentation. Specs are the plan; once a change ships, its lasting
facts move into reference and how-to pages, and its reasoning into explanation.

## AGENTS.md and CLAUDE.md

`kb agents init` writes the workflow into `AGENTS.md`, the file several coding
agents read. Claude Code's docs treat `CLAUDE.md` as the primary file and read
`AGENTS.md` only as a fallback when no `CLAUDE.md` exists (and only on recent
versions). Anthropic does not recommend one over the other; the documented
pattern for a shared repo is a `CLAUDE.md` whose first line is `@AGENTS.md`.
That is what kb creates, so one file stays the source of truth and every
version of Claude Code sees it. The kb section sits between `kb:begin` and
`kb:end` markers, so rerunning `kb agents init` refreshes it without touching
what you wrote around it.
