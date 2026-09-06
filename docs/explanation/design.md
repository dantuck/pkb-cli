# Design principles

A portable, offline-capable personal knowledge base with **Diataxis as the
core content model**, extended (not diluted) by journaling and multi-source
ingestion (usememos, GitLab issues, GitHub issues, beads).

Non-negotiable principles, in priority order:

1. **Diataxis is the only classification for knowledge content.** Four
   types, full stop: `tutorial`, `how-to`, `reference`, `explanation`.
2. **Portability.** The source of truth is a git repository of plain
   markdown files. No server, database daemon, or network access required to
   read, write, or search it.
3. **Offline-first.** All indexing and search tooling must run fully
   locally. Sync to external sources (usememos, GitLab, GitHub, beads) is a one-way
   *ingestion* step, never a runtime dependency.
4. **Non-core content never competes with core content.** Journals, inbox
   captures, and raw synced source dumps are extensions that link to or feed
   into the Diataxis core — they are never a fifth content type.
5. **Idempotent, incremental everything.** Ingestion and indexing must be
   safe to re-run and must not reprocess unchanged data.

These principles are why the [frontmatter
schema](../reference/frontmatter-schema.md) enforces `extension` as null for
core content, why [search](search-architecture.md) is tiered so nothing
requires a running service, and why [ingestion](../reference/ingestion.md) is
cursor-based rather than a live sync.

## Why this repo applies Diataxis to itself too

The data-repo content model above treats Diataxis as non-negotiable for
*notes*. This tool repo's own documentation (this `docs/` tree) follows the
same structure for the same reason: a tutorial, a how-to guide, a reference
page, and an explanation page answer different questions and shouldn't be
smashed into one README. See [diataxis.fr](https://diataxis.fr/) for the
framework itself.

## Explicit non-goals (v1)

- No web UI or server process — CLI/file-based only, per the portability
  requirement.
- No real-time sync/webhooks from usememos/GitLab/GitHub/beads — polling/cursor-based
  batch sync only.
- No automatic Diataxis classification of inbox content — triage is
  human-in-the-loop.
- No multi-device conflict resolution beyond standard git merge — this is a
  single-user, git-synced repo, not a CRDT system.

## Build order (historical, for context)

The tool was built in phases, each depending on the last:

| Phase | Deliverable | Depends on |
|---|---|---|
| 1 | Repo scaffold: folder layout, `.gitignore`, `schema.sql`, `config.yml` | — |
| 2 | `validate_frontmatter.py` + pre-commit hook | 1 |
| 3 | `kb new`, `kb journal` (manual content creation works end-to-end) | 1, 2 |
| 4 | `rg`/`fzf` documented as day-one search (no code, just usage doc) | 1 |
| 5 | `index_fts.py` + `kb search` | 1, 2 |
| 6 | `sync_memos.py` (first ingestion source — usememos is the active capture point) | 1, 2 |
| 7 | `triage_report.py` + `kb triage`, `kb inbox` | 6 |
| 8 | `sync_gitlab.py`, `sync_github.py`, `sync_beads.py` (same contract as memos) | 1, 2 |
| 9 | `kb links` (graph/backlink queries from frontmatter) | 2 |
| 10 | Embeddings layer (`index_embeddings.py`) — only if FTS5 relevance proves insufficient | 5 |
| 11 | Monthly journal rollups + on-this-day look-back | 8 (journal volume exists) — done |

Phases 1–7 constitute a usable v1. Phases 8–11 extend it without any
architectural change.
