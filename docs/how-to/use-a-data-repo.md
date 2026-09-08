# How to point `kb` at a data repo

`kb` has no opinion about your notes — it operates on whichever data repo it
resolves. This covers both starting points.

## Clone an existing data repo

```bash
git clone <your-private-notes-repo-url> ~/.pkb
cd ~/.pkb
kb setup       # pre-commit hook, search index, local bd store if `bd` is installed
```

## Start a data repo from scratch

There's no `kb init`. If `kb setup` finds no data repo anywhere (no `.pkb/`
walking up from cwd, and nothing at `~/.pkb`), it bootstraps one at `~/.pkb`
for you -- creates the `.pkb/` marker directory and runs `git init`:

```bash
kb setup
```

To start a data repo somewhere other than `~/.pkb`, create the marker
directory yourself first:

```bash
mkdir -p /path/to/repo/.pkb
cd /path/to/repo
git init
kb setup
```

Content type directories (`tutorials/`, `how-to/`, `reference/`,
`explanation/`) don't need to exist ahead of time — `kb new` creates them on
first use.

## Auto-commit

If the data repo is a git repo, every mutation (`kb new`, `kb journal`, `kb
tag`/`kb link`, `kb inbox` promote/redirect/discard, `kb sync`, and their web
UI equivalents) commits itself immediately — one commit per action, message
like `new how-to: Deploy staging` or `tags: 2026-08-31-1423`. There's nothing
to configure: it's a no-op if the data repo isn't a git repo (or `git` isn't
installed), and it never fails the command it's attached to. `kb setup`
(and, lazily, the first auto-commit itself) excludes `.pkb/fts.db` and the
`*.md.lock` files `kb`'s per-entry locking creates from git, the same way
`.beads/` is excluded — otherwise every commit would carry index/lock churn
alongside the actual change. `kb doctor` reports whether these excludes are
in place.

This only commits — it never pushes on its own. See [reference: repository
layout](../reference/repository-layout.md) for what else lives in the repo.

## Push

`kb push` pushes whatever auto-commit has accumulated to the data repo's
configured upstream:

```bash
kb push
```

It never guesses a remote — if the repo has no upstream yet, set one once
(`git push -u origin main` or similar) and every `kb push` after that just
works. If there's nothing to push it says so and exits cleanly; if the
remote has diverged it warns before attempting, but still lets git itself
decide whether the push is rejected (no force-push, ever). A local-only
data repo (no remote at all) is a perfectly normal setup — auto-commit's
durability doesn't depend on pushing anywhere, and `kb doctor`/the web UI's
Admin panel only mention push status once an upstream actually exists.

To push automatically after every auto-commit instead of running `kb push`
by hand, set this in `.pkb/config.yml` (committed, so it applies to every
clone of this data repo):

```yaml
auto_push: true
```

Auto-push is best-effort: a failed push (offline, no upstream, rejected)
never blocks the write that triggered it, and never retries on its own —
`kb doctor` and the Admin panel's badge will show commits piling up
unpushed if it keeps failing, at which point `kb push` surfaces the real
error.

The web UI's Admin panel mirrors all of this: it shows ahead/behind status
next to the other auto-refreshed checks, and a **Push** button appears
whenever there's something to push.

## How `kb` finds your data repo

`kb` resolves which data repo to use by walking up from the current directory
looking for a `.pkb/` subdirectory, so a repo you're actually standing inside
(e.g. a separate work/scratch pkb elsewhere) always wins. If that walk finds
nothing, it falls back to `~/.pkb`, so day-to-day commands (`kb search`, `kb
triage`, `kb inbox`, ...) work from anywhere without `cd`-ing into a specific
repo first.

See [reference: repository layout](../reference/repository-layout.md) for
what `kb setup` expects to find or create inside the data repo.
