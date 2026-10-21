# Getting started

This walks through installing `kb`, pointing it at a data repo, and creating
and finding your first entry. By the end you'll have a working knowledge base
and a feel for the core loop: write, search, journal.

## 1. Install the CLI

```bash
curl -fsSL https://raw.githubusercontent.com/dantuck/pkb-cli/main/install.sh | bash
```

This needs [uv](https://docs.astral.sh/uv/) or [pipx](https://pipx.pypa.io/)
and installs the latest release of the tool with `kb` on your `PATH`. (Prefer
no script? Run `uv tool install git+https://github.com/dantuck/pkb-cli` or
`pipx install git+https://github.com/dantuck/pkb-cli`, then `kb setup`.) If `kb`
isn't found afterwards, run `uv tool update-shell` or `pipx ensurepath` and
open a new shell.

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
sets up a local `bd` (beads) TODO store if `bd` is on your `PATH`. If `bd` is
there it also offers, defaulting to no, to check for due TODOs every few minutes
and send a desktop notification (see step 6). You don't
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

## 6. Track a TODO and get reminded

TODOs aren't only for code. Add one with a reminder time:

```bash
kb todo -a "review the vendor email" --due "tomorrow 9am"
```

`--due` takes `30m`, `2h`, `3d`, `friday`, `5pm`, `tomorrow 9am`, or a date.
`kb remind` lists whatever has come due. To be notified without having to
look, turn on the background check (setup offers this too; it needs `bd`):

```bash
kb remind install
```

When the time comes you get a desktop notification. Not now? Snooze it:

```bash
kb todo snooze <id> 1h
```

On macOS, `kb remind install` sends a test notification, and if it's blocked
tells you to allow **kb reminders** in System Settings → Notifications
(`kb remind test` re-checks). `kb doctor` reports whether the reminder check
is scheduled and working. More in [how to get reminded about a
TODO](../how-to/get-reminded-about-a-todo.md).

## Where to go next

- [How-to guides](../how-to/) for specific tasks (ingestion, inbox triage, the
  Claude Code skill).
- [Reference](../reference/) for the full command surface and frontmatter
  schema.
- [Explanation](../explanation/) for why the tool is built this way.
