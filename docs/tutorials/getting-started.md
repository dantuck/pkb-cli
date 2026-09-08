# Getting started

This walks through installing `kb`, pointing it at a data repo, and creating
and finding your first entry. By the end you'll have a working knowledge base
and a feel for the core loop: write, search, journal.

## 1. Install the CLI

```bash
curl -fsSL https://raw.githubusercontent.com/dantuck/pkb-cli/main/install.sh | bash
```

This downloads a snapshot of the tool to `~/pkb-cli` and symlinks `kb` onto
your `PATH`. The only requirement is `python3`. If the installer can't find a
writable directory already on your `PATH`, it creates `~/.local/bin` and
prints an `export PATH=...` line — add that to your shell profile before
continuing.

Check it worked:

```bash
kb doctor
```

## 2. Set up your data repo

`kb` is just the tool — your notes live in a separate repo, conventionally at
`~/.pkb`. If you already have one:

```bash
git clone <your-private-notes-repo-url> ~/.pkb
cd ~/.pkb
kb setup
```

Starting from nothing instead, just run setup -- it bootstraps `~/.pkb` (marker
directory + `git init`) when it finds no data repo anywhere:

```bash
kb setup
```

`kb setup` installs a pre-commit validation hook, builds the search index, and
sets up a local `bd` (beads) TODO store if `bd` is on your `PATH`. You don't
need to create `tutorials/`, `how-to/`, `reference/`, or `explanation/`
yourself — `kb new` creates each on first use.

## 3. Create your first entry

```bash
kb new how-to "Tailscale sidecar config for Synology" --tags networking --body "..."
```

This scaffolds a file under `how-to/` with valid frontmatter (an `id`,
timestamps, `type: how-to`, your tags) and the body you passed.

## 4. Find it again

```bash
kb search "tailscale"
```

`kb search` uses the SQLite FTS5 index `kb setup` built. Add `--type how-to`
to filter by Diataxis type, or `--all` to also search journal/inbox/sources.

## 5. Log something to today's journal

```bash
kb journal
```

Opens (creating if needed) today's `journal/YYYY/MM/YYYY-MM-DD.md` file, and
prints an "on this day" section showing entries from a week ago, a month ago,
and this date in earlier years, if any exist.

## Where to go next

- [How-to guides](../how-to/) for specific tasks (ingestion, inbox triage, the
  Claude Code skill).
- [Reference](../reference/) for the full command surface and frontmatter
  schema.
- [Explanation](../explanation/) for why the tool is built this way.
