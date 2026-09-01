# pkb-cli

The `kb` command-line tool for a [Diataxis](https://diataxis.fr/)-based personal
knowledge base. This repo is the tool only — it has no opinion about your actual
notes, which live in a separate (typically private) data repo containing
`tutorials/`, `how-to/`, `reference/`, `explanation/`, `journal/`, `inbox/`,
`sources/`, and a `.pkb/` config directory.

Full design rationale: [docs/spec.md](docs/spec.md).
Zero-setup search without this tool at all: [docs/search.md](docs/search.md).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/dantuck/pkb-cli/main/install.sh | bash
```

Clones this repo to `~/pkb-cli` (override with `$PKB_CLI_HOME`) and symlinks `kb`
onto your PATH. Safe to re-run — pulls latest and re-runs setup idempotently.

No external Python packages are required — everything runs on the stdlib
(`sqlite3` for FTS5, no `pyyaml`).

## Use it against your data repo

The central kb lives at `~/.pkb` — content (`tutorials/`, `how-to/`, etc.) sits
directly inside it, alongside its own `.pkb/` config subdir (`config.yml`,
`cursors.json`, the generated `fts.db`, optionally sops-encrypted `secrets.enc.env`).
None of that lives in this tool repo.

```bash
git clone <your-private-notes-repo-url> ~/.pkb
cd ~/.pkb
kb setup       # pre-commit hook, search index, local bd store if `bd` is installed
```

`kb` resolves which data repo to use by walking up from the current directory
looking for a `.pkb/` subdirectory (so a repo you're actually standing inside —
e.g. a separate work/scratch pkb elsewhere — always wins). If that walk finds
nothing, it falls back to `~/.pkb`, so day-to-day commands (`kb search`, `kb
triage`, `kb inbox`, ...) work from anywhere without `cd`-ing into a specific
repo first.

## Command surface

```bash
kb new how-to "Tailscale sidecar config for Synology"
kb journal                      # today's journal file
kb search "tailscale" --type how-to
kb search "tailscale" --all     # include journal/inbox/sources
kb inbox                        # what's waiting for triage
kb triage                       # flag anything overdue (> 14 days)
kb links 2026-08-31-1423        # forward links + backlinks for an entry
kb validate                     # frontmatter/id/link checks (also runs pre-commit)
kb index                        # incremental reindex after edits
kb sync all                     # pull in usememos / GitLab / beads
kb setup [--install [DIR]]      # onboarding: hook, index, bd store, and (with --install) PATH
kb doctor                       # diagnose issues -- read-only, never writes anything
```

`kb doctor` and `kb setup` share the same detection logic (hook state, index freshness,
tool presence) — `setup` acts on it, `doctor` only reports, plus a few deeper checks
`setup` doesn't do: a hook pointing at a pkb-cli install that no longer exists, a
search index whose row count has drifted from the files on disk, and cursors.json
sanity.

Search also works with zero setup via `rg`/`fzf` directly on a data repo's file
tree — see [docs/search.md](docs/search.md).

## Ingestion credentials

Sync scripts read credentials only from the environment — they have no opinion on
how those get set. Options:

- Export `PKB_MEMOS_URL`/`PKB_MEMOS_TOKEN` (usememos) and `PKB_GITLAB_PROJECT`
  (GitLab, via an authenticated `glab`) directly.
- Or use [sops](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age):
  a data repo can commit `.sops.yaml` + `.pkb/secrets.enc.env` (ciphertext, safe to
  commit) and run `sops exec-env .pkb/secrets.enc.env 'kb sync memos'`. See
  [templates/secrets.env.example](templates/secrets.env.example) for the expected keys.
- **beads**: requires the `bd` CLI on `PATH`. By default it points `bd` at your
  resolved data repo (`~/.pkb`, or whichever repo you're standing inside) rather
  than relying on `bd`'s own cwd-based auto-discovery, which wouldn't know about
  kb's home-directory fallback. Initialize one in your data repo with
  `bd init --stealth` to keep it out of git. `PKB_BEADS_DIR` points at a different
  project's store instead; `PKB_BEADS_GLOBAL=1` uses bd's shared cross-project store.

Sync is the only part of this system that touches the network; search and editing
always work offline. See [docs/spec.md](docs/spec.md) §4 for the full per-source
contract.

## Layout

```
pkb-cli/
  scripts/      the kb CLI and everything it dispatches to
  templates/    schema.sql (loaded at runtime, never copied into a data repo)
                and secrets.env.example (documentation only)
  docs/         spec.md, search.md
  install.sh
```

See [docs/spec.md](docs/spec.md) §2 for the data repo's own expected layout.
