---
name: kb
description: Use the `kb` CLI to work with the user's personal Diataxis-based knowledge base -- search notes, create tutorial/how-to/reference/explanation entries, log to today's journal, triage the inbox, check TODOs (via beads), and sync external sources (usememos, GitLab, GitHub, beads). Trigger whenever the user asks to look something up in their notes, write something down, log a journal entry, check their inbox or TODO list, or sync notes from an external source.
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
  `--plain` for script-parseable output. If the `semantic` extra is installed
  the default ranking also matches by *meaning* (so a query can use different
  words than the note); `--keyword` forces exact FTS5 matching, `--semantic`
  meaning only. When a search comes back empty, rephrase it with synonyms
  before concluding the note doesn't exist. Notes on stderr (e.g. "semantic
  search unavailable, using keyword only") are informational, not errors.
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
- **Delete or move a core entry**: `kb rm <id>` deletes it (refuses if other
  entries still link to it, unless `--force`, which also strips it from those
  entries' `links`). `kb mv <id> [--type T] [--title "..."]` recategorizes
  and/or renames it, relocating the file to match. These are the sanctioned
  way to remove or reorganize a file created under the Diataxis structure --
  not `rm`/`mv` on the filesystem, which would leave the search index (and,
  for `rm`, any backlinks) stale.
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
  To be reminded, add `--due WHEN` (`30m`, `2h`, `tomorrow 9am`, `friday`);
  `kb todo snooze <id> <when>` hides one until then and reminds again,
  `kb remind` lists what's due now.
- **See how entries connect**: `kb links <id>` prints forward links and
  backlinks for an entry (`--json` for machine-readable).
- **Read an entry's content by id**: `kb show <id>` prints it directly --
  no need to look up its path first (`search`/`links`/`inbox` all print
  paths too, but `show` is the direct route when you already know the id).
- **Pull in external sources**: `kb sync all` (or `memos` / `gitlab` /
  `github` / `beads`) ingests into `sources/`, stubbing anything notable into `inbox/`
  for triage.
- **Push to the remote**: `kb push` pushes the data repo's auto-commits to its
  configured upstream (it never guesses a remote).
- **Web UI / background services**: `kb web [--port N] [--no-open]` runs the
  local UI in the foreground until Ctrl-C (don't start it unless asked --
  it blocks). `kb service install|uninstall|status|refresh` runs it as a login
  service; `kb sync-service install|uninstall|status [--interval-minutes MIN]`
  runs `kb sync` on a timer; `kb remind install|uninstall|status` schedules
  due-TODO notifications. All take `--repo DIR` to pin the data repo.
- **Credentials**: `kb secrets` manages the sops-encrypted ingestion
  credentials -- run it only when the user asks to set up sync sources, never
  read or print the values.
- **Preferences, install, upgrade**: `kb config editor [<cmd>]`, `kb setup
  [--yes]` (idempotent onboarding), `kb update [--check]`, `kb help`.
- **Something seems broken**: `kb doctor` is read-only -- reports on
  frontmatter validity, search-index freshness, pre-commit hook state, and
  tool availability. `kb setup` acts on the same checks.

## Driving kb from an agent (no terminal)

You never get a TTY, so the fzf pickers never open. Rules of thumb:

- **Always pass the id and every argument explicitly.** A command that would
  open a picker (`kb tag`/`kb link` with the id or values left out, bare
  `kb todo -a`, bare `kb journal -m`) either errors or blocks -- don't use
  those forms.
- **Add `--json` to every write command and parse the result** instead of
  scraping text. Each prints exactly one JSON line on stdout:

  | command | `--json` output |
  |---|---|
  | `kb new` | `{id, path, type, title}` (`path` is absolute) |
  | `kb journal -m` | `{added, date, path, time}` (`{added: false}` if the text was empty) |
  | `kb journal rollup` | `{id, path, month, entries}` |
  | `kb tag <id>` | `{id, tags}` |
  | `kb tag <id> add\|rm ...` | `{id, tags, changed}` (`changed: false` = no-op) |
  | `kb link <id> add\|rm ...` | `{id, links, changed}` |
  | `kb link <id>` / `kb links <id>` | `{id, path, forward, backlinks}` |
  | `kb rm` | `{id, path, backlinks_cleared}` |
  | `kb mv` | `{id, path}` |
  | `kb inbox` | `[{id, name, title, age_days, created}, ...]` |
  | `kb inbox <id> promote <type>` | `{action, id, type, path}` |
  | `kb inbox <id> redirect` | `{action, id, path}` |
  | `kb inbox <id> discard` | `{action, name}` |
  | `kb todo -a "<title>"` | bd's issue JSON for the new TODO |
  | `kb todo snooze\|unsnooze\|due ...` | `{action, id, until\|due}` |
  | `kb search` / `kb show` / `kb todo` / `kb triage` | read-only; they take `--json` too |

  Paths other than `kb new`'s and `kb journal rollup`'s are relative to the data repo.
- **Errors**: exit code 1, and with `--json` stderr is `{"error": "..."}`
  (otherwise `error: ...`). Exit 2 is a usage error from argparse. A
  read-only entry (synced from GitLab/GitHub/usememos/beads) refuses
  `tag`/`link` edits -- change it at the source instead.
- **Every write is auto-committed** to the data repo and reindexed. Nothing
  is pushed unless `auto_push: true` is set -- run `kb push` when asked to
  publish.
- **Find before you create.** `kb search "<topic>" --json` first; extend or
  link an existing entry rather than adding a near-duplicate. `kb show <id>
  --json` reads one in full.
- **Tag what you write** with the project's tag (see the project's
  CLAUDE.md) so it's filterable later: `kb new ... --tags a,b` /
  `kb journal -m` then `kb tag <id> add <tag>`.

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
- **`kb new`, `kb tag`, `kb link`, `kb rm`, `kb mv`, and `kb inbox
  promote`/`redirect` all reindex automatically** -- an entry is searchable
  immediately after any of these. Only reach for `kb index` yourself after
  editing an entry's body by
  hand (never the frontmatter). `kb validate` catches structural mistakes
  before they're committed (also runs automatically as a pre-commit hook
  once `kb setup` has been run in the data repo).
- **`kb sync` and `kb update` are the only commands that touch the network**;
  search, journal, inbox, and links all work fully offline.

## Command reference

```
kb new <type> "<title>" [--tags a,b] [--links id,id] [--body TEXT] [--json]
                                  type: tutorial|how-to|reference|explanation
kb journal [<date>] [-m [TEXT]] [--json]  today (default) or YYYY-MM-DD; -m TEXT quick-adds,
                                  bare -m opens $EDITOR for multiline input
kb journal --tag <tag> [--plain]  past entries carrying <tag>, newest first
kb journal rollup [YYYY-MM] [--json]  generate/refresh that month's summary page (default: last month)
kb search "<query>" [--type T] [--tag TAG] [--all] [--keyword|--semantic] [--plain] [--json]
kb inbox [--plain] [--json]       interactive triage: promote / redirect / discard
kb inbox <id> promote <type>|redirect|discard [--json]   non-interactive triage (no terminal needed)
kb triage [--json]                read-only overdue-inbox report
kb links <id> [--json]            forward links + backlinks
kb show <id> [--json]             print an entry's full content by id
kb tag <id> [add|rm <tag>...] [--json]   view/add/remove tags (never hand-edit frontmatter)
kb link <id> [add|rm <id>...] [--json]   view/add/remove links, same reasoning
kb rm <id> [--force] [--json]     delete an entry (--force also cleans up backlinks)
kb mv <id> [--type T] [--title "<title>"] [--json]   recategorize and/or rename a core entry
kb todo [--all] [--plain] [--json]   open bd TODOs, sorted by priority
kb todo -a ["<title>"] [-p 0-4] [-t TYPE] [-d TEXT] [-l labels] [--due WHEN] [--json]
                                  quick-add a TODO; bare -a opens bd's interactive form
kb todo snooze <id> <when> [--json]   hide a TODO until <when>, then remind again
kb todo unsnooze <id> [--json]    end a snooze early
kb todo due <id> <when>|none [--json]   set or clear a TODO's reminder time
kb remind [list|check|test|install|uninstall|status] [--interval-minutes MIN] [--repeat-hours H] [--repo DIR]
                                  list what's due; `check` sends desktop notifications;
                                  `install` runs `check` on a timer (launchd/systemd --user)
kb bd <any bd subcommand>         show/close/comment/create/update, resolved-repo-aware
kb sync [memos|gitlab|github|beads|all]  pull in external sources
kb validate                       frontmatter/id/link integrity check
kb index [--full]                 rebuild/refresh the search index
kb push                           push the data repo's auto-commits to its upstream
kb web [--port N] [--no-open]     local web UI (foreground; blocks)
kb service install|uninstall|status|refresh [--port N] [--repo DIR]   kb web as a login service
kb sync-service install|uninstall|status [--interval-minutes MIN] [--repo DIR]   kb sync on a timer
kb secrets                        manage ingestion credentials (sops)
kb doctor                         read-only health check
kb config editor [<cmd>]          view/set the editor kb spawns when $EDITOR isn't set
kb setup [--yes]                  guided onboarding, asks before each optional step
kb update [--check]               upgrade kb (--check: only look)
kb help                           top-level command list
```

Run `kb <command> -h` for full flag details on any of these, or `kb help` for
the top-level list.
