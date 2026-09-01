---
name: kb
description: Use the `kb` CLI to work with the user's personal Diataxis-based knowledge base -- search notes, create tutorial/how-to/reference/explanation entries, log to today's journal, triage the inbox, check TODOs (via beads), and sync external sources (usememos, GitLab, beads). Trigger whenever the user asks to look something up in their notes, write something down, log a journal entry, check their inbox or TODO list, or sync notes from an external source.
---

# kb -- personal knowledge base CLI

`kb` is a personal CLI for a Diataxis-based knowledge base: `tutorials/`,
`how-to/`, `reference/`, `explanation/` (core content), plus `journal/`,
`inbox/`, and `sources/` (extensions). It resolves which data repo to operate
on by walking up from the current directory to a `.pkb/` subdirectory,
falling back to `~/.pkb` -- so it works from anywhere, not just from inside
the data repo.

## When to reach for it

- **Look something up**: `kb search "<query>"` (add `--type how-to` etc. to
  narrow to one Diataxis type, `--all` to also include journal/inbox/sources).
  Opens an interactive picker if fzf + a real terminal are available; add
  `--plain` for script-parseable output.
- **Log that something happened**: `kb journal -m "<note>"` appends a
  timestamped line to today's entry, creating it first if needed -- no
  frontmatter to touch. `kb journal <date> -m "<note>"` backfills a specific
  day. `kb journal -m` with no text opens a scratch buffer in `$EDITOR` for a
  longer, multiline note instead.
- **Write a new tutorial/how-to/reference/explanation entry**:
  `kb new <type> "<title>"` scaffolds it with correct frontmatter and prints
  the path -- edit that path to fill in the body.
- **Check what's pending triage**: `kb inbox` (interactive picker: promote to
  a Diataxis type / redirect into the journal / discard) or `kb triage`
  (read-only, exit-code-friendly, good for scripts).
- **Check TODOs**: `kb todo` lists open items from `bd` (beads), sorted by
  priority. Act on one with `kb bd show <id>` / `kb bd close <id>` /
  `kb bd comment <id> "..."`.
- **See how entries connect**: `kb links <id>` prints forward links and
  backlinks for an entry.
- **Pull in external sources**: `kb sync all` (or `memos` / `gitlab` /
  `beads`) ingests into `sources/`, stubbing anything notable into `inbox/`
  for triage.
- **Something seems broken**: `kb doctor` is read-only -- reports on
  frontmatter validity, search-index freshness, pre-commit hook state, and
  tool availability. `kb setup` acts on the same checks.

## Conventions to respect

- **Never hand-edit frontmatter** (`id`, `created`, `updated`, `type`,
  `extension`, `source`, `source_id`, `tags`, `links`, `title`). Every kb
  command that creates or moves an entry manages these correctly; editing by
  hand risks a duplicate id or a type/directory mismatch that `kb validate`
  will flag.
- **Use `kb new`, not a raw file**, for new core content -- it generates the
  id and timestamps and puts the file under the right directory
  (`tutorials/`, `how-to/`, `reference/`, `explanation/`).
- **Use `kb journal -m` for quick notes** instead of opening the file
  directly -- it appends correctly formatted, timestamped content and bumps
  the entry's `updated` timestamp.
- **After editing an entry's body by hand** (never the frontmatter), run
  `kb index` so search stays current. `kb validate` catches structural
  mistakes before they're committed (also runs automatically as a
  pre-commit hook once `kb setup` has been run in the data repo).
- **`kb sync` and `kb update` are the only commands that touch the network**;
  search, journal, inbox, and links all work fully offline.

## Command reference

```
kb new <type> "<title>"          type: tutorial|how-to|reference|explanation
kb journal [<date>] [-m [TEXT]]  today (default) or YYYY-MM-DD; -m TEXT quick-adds,
                                  bare -m opens $EDITOR for multiline input
kb search "<query>" [--type T] [--tag TAG] [--all] [--plain]
kb inbox [--plain]                interactive triage: promote / redirect / discard
kb triage                         read-only overdue-inbox report
kb links <id>                     forward links + backlinks
kb todo [--all] [--plain]         open bd TODOs, sorted by priority
kb bd <any bd subcommand>         show/close/comment/create/update, resolved-repo-aware
kb sync [memos|gitlab|beads|all]  pull in external sources
kb validate                       frontmatter/id/link integrity check
kb index [--full]                 rebuild/refresh the search index
kb doctor                         read-only health check
kb config editor [<cmd>]          view/set the editor kb spawns when $EDITOR isn't set
kb setup [--install] [--install-skill]
```

Run `kb <command> -h` for full flag details on any of these, or `kb help` for
the top-level list.
