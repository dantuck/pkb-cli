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
- **Look back at the journal**: opening `kb journal` (today, no `-m`) prints
  an "on this day" section -- entries from 1 week ago, 1 month ago, and this
  same month/day in earlier years, whichever of those actually exist -- ahead
  of the usual recent-entries list, and pins the same entries to the top of
  the interactive picker. `kb journal --tag <tag>` lists every past entry
  carrying that tag, newest first, for browsing one theme across time instead
  of `kb search`'s relevance ranking. `kb journal rollup [YYYY-MM]` (default:
  last month) generates/refreshes `reference/journal-summaries/YYYY-MM.md`,
  concatenating that month's daily entries plus a tag-frequency line onto one
  browsable page -- rerunning it just refreshes the same file/id in place.
- **Write a new tutorial/how-to/reference/explanation entry**:
  `kb new <type> "<title>"` scaffolds it with correct frontmatter and prints
  the path -- edit that path to fill in the body, or pass `--body "..."` to
  set it in the same call. `--tags a,b` and `--links <id>,<id>` set those at
  creation time too (link targets must already exist).
- **Tag or link an existing entry**: `kb tag <id> add <tag>...` /
  `kb tag <id> rm <tag>...` (bare `kb tag <id>` lists current tags).
  `kb link <id> add <target-id>...` / `kb link <id> rm <target-id>...` (bare
  `kb link <id>` is the same as `kb links <id>`). These are the *only*
  sanctioned way to change `tags`/`links` after creation -- see below.
- **Check what's pending triage**: `kb inbox` (interactive picker: promote to
  a Diataxis type / redirect into the journal / discard) or `kb triage`
  (read-only, exit-code-friendly and `--json`-able, good for scripts). No
  interactive picker is available here (no terminal) -- resolve an item
  directly instead: `kb inbox <id> promote <type>` / `kb inbox <id> redirect`
  / `kb inbox <id> discard`.
- **Check TODOs**: `kb todo` lists open items from `bd` (beads), sorted by
  priority (`--json` for machine-readable). Act on one with `kb bd show <id>`
  / `kb bd close <id>` / `kb bd comment <id> "..."`. Add one with
  `kb todo -a "<title>" [-p 0-4] [-t bug|feature|task|epic|chore|decision]
  [-d "<description>"] [-l labels]` -- ticket-shaped in one call. Bare
  `kb todo -a` opens bd's own interactive form instead.
- **See how entries connect**: `kb links <id>` prints forward links and
  backlinks for an entry (`--json` for machine-readable).
- **Read an entry's content by id**: `kb show <id>` prints it directly --
  no need to look up its path first (`search`/`links`/`inbox` all print
  paths too, but `show` is the direct route when you already know the id).
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
  will flag. Use `kb tag`/`kb link` to change tags/links on an existing entry
  -- not an editor, even for "just" adding a tag.
- **Use `kb new`, not a raw file**, for new core content -- it generates the
  id and timestamps and puts the file under the right directory
  (`tutorials/`, `how-to/`, `reference/`, `explanation/`).
- **Use `kb journal -m` for quick notes** instead of opening the file
  directly -- it appends correctly formatted, timestamped content and bumps
  the entry's `updated` timestamp.
- **`kb new`, `kb tag`, `kb link`, and `kb inbox promote`/`redirect` all
  reindex automatically** -- an entry is searchable immediately after any of
  these. Only reach for `kb index` yourself after editing an entry's body by
  hand (never the frontmatter). `kb validate` catches structural mistakes
  before they're committed (also runs automatically as a pre-commit hook
  once `kb setup` has been run in the data repo).
- **`kb sync` and `kb update` are the only commands that touch the network**;
  search, journal, inbox, and links all work fully offline.

## Command reference

```
kb new <type> "<title>" [--tags a,b] [--links id,id] [--body TEXT]
                                  type: tutorial|how-to|reference|explanation
kb journal [<date>] [-m [TEXT]]  today (default) or YYYY-MM-DD; -m TEXT quick-adds,
                                  bare -m opens $EDITOR for multiline input
kb journal --tag <tag>            past entries carrying <tag>, newest first
kb journal rollup [YYYY-MM]       generate/refresh that month's summary page (default: last month)
kb search "<query>" [--type T] [--tag TAG] [--all] [--plain] [--json]
kb inbox [--plain]                interactive triage: promote / redirect / discard
kb inbox <id> promote <type>|redirect|discard   non-interactive triage (no terminal needed)
kb triage [--json]                read-only overdue-inbox report
kb links <id> [--json]            forward links + backlinks
kb show <id> [--json]             print an entry's full content by id
kb tag <id> [add|rm <tag>...]     view/add/remove tags (never hand-edit frontmatter)
kb link <id> [add|rm <id>...]     view/add/remove links, same reasoning
kb todo [--all] [--plain] [--json]   open bd TODOs, sorted by priority
kb todo -a ["<title>"] [-p 0-4] [-t TYPE] [-d TEXT] [-l labels]
                                  quick-add a TODO; bare -a opens bd's interactive form
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
