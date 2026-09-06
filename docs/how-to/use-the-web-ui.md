# How to use the web UI

`kb` ships a local-only web UI for browsing, capturing, and triaging your
knowledge base without a terminal.

## Start it

```bash
kb web
```

Starts a stdlib-only HTTP server bound to `127.0.0.1` (never a
network-reachable interface) and opens it in your browser. Default port is
`4173`; override with `--port`:

```bash
kb web --port 8080
```

It runs in the foreground for as long as you're using it — `Ctrl-C` to stop.
Like every `kb` command, it operates on the resolved data repo (walking up
from the current directory, falling back to `~/.pkb`), so `cd` into a
specific data repo first if you want to browse something other than the
default.

## Run it as a login service instead

To have it always running at `http://127.0.0.1:4173/` without starting it by
hand each time:

```bash
kb service install                # serves the repo resolved from cwd (or --repo DIR)
kb service status
kb service uninstall
```

This installs a per-user launchd LaunchAgent on macOS, or a systemd `--user`
unit on Linux — no root/admin needed, and it still binds `127.0.0.1` only.
`--port PORT` and `--repo DIR` (at install time) work the same as `kb web
--port` / `cd`-ing into a repo first. Logs go to `~/Library/Logs/kb-web.log`
on macOS or `journalctl --user -u kb-web.service` on Linux.

## What's in it

- **Feed** — the main view: your Diataxis content, newest first. Filter by
  type with the chips along the top (`tutorials`, `how-to`, `reference`,
  `explanation`, or All), or click any tag on an entry to filter by that tag
  (`clear` removes the filter). Click an entry to open it.
- **Entry modal** — click any feed item to view it rendered as markdown; hit
  **edit** to change its title, body, tags, or links in place (tag/link
  inputs autocomplete against existing tags and entries). Corresponds to
  editing the file plus `kb tag`/`kb link` — never hand-edited frontmatter.
- **New** (header button) — opens a modal to create an entry: pick a Diataxis
  type, title, markdown body (with a preview toggle), tags, and links, then
  **create**. Equivalent to `kb new <type> "<title>" --tags ... --links ...`.
- **Search (⌘K)** — a command-palette-style jump-to-entry search over title
  and content, the same index `kb search` uses.
- **Capture** — the box at the bottom of the feed. Text typed here goes into
  *today's journal entry*, equivalent to `kb journal -m "<text>"` — this is
  quick capture, not a core-content entry.
- **Inbox** (header button, with a live count) — everything waiting for
  triage. For each item, pick a Diataxis type and **Promote**, or
  **Redirect** into today's journal, or **Discard**. Same three outcomes as
  `kb inbox <id> promote|redirect|discard`.
- **Todos** (header button, with a live count) — the full `bd` (beads) todo
  list: filter by title/id/label, add a todo (with optional description and
  labels), and open one to change priority/type/assignee/labels/description,
  manage `blocked by`/`blocks` dependencies, add comments, and close, reopen,
  or permanently delete it.
- **Admin** (header button) — **Reindex** (`kb index`), **Validate**
  (`kb validate`), **Doctor** (`kb doctor`), and **Sync** against a chosen
  source (`kb sync [memos|gitlab|beads|all]`), with command output shown
  inline.
- **Theme toggle** — light / dark / system, in the header.

## When to reach for it instead of the CLI

The web UI is a convenience layer over the same operations as `kb`'s
subcommands — nothing it does is unavailable from the CLI, and nothing it
does bypasses frontmatter validation or reindexing. Reach for it when
browsing and clicking is more natural than remembering ids and flags (e.g.
skimming the feed by tag, or triaging a full inbox in one sitting); reach for
the CLI for scripting, automation, or when you already know an entry's id.

See [reference: CLI commands](../reference/cli.md) for the command-line
equivalents of everything above.
