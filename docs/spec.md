# Personal Knowledge Base — Implementation Spec

## 1. Purpose & Design Principles

A portable, offline-capable personal knowledge base with **Diataxis as the core content
model**, extended (not diluted) by journaling and multi-source ingestion (usememos,
GitLab issues, beads).

Non-negotiable principles, in priority order:

1. **Diataxis is the only classification for knowledge content.** Four types, full stop:
   `tutorial`, `how-to`, `reference`, `explanation`.
2. **Portability.** The source of truth is a git repository of plain markdown files.
   No server, database daemon, or network access required to read, write, or search it.
3. **Offline-first.** All indexing and search tooling must run fully locally. Sync to
   external sources (usememos, GitLab, beads) is a one-way *ingestion* step, never a
   runtime dependency.
4. **Non-core content never competes with core content.** Journals, inbox captures, and
   raw synced source dumps are extensions that link to or feed into the Diataxis core —
   they are never a fifth content type.
5. **Idempotent, incremental everything.** Ingestion and indexing must be safe to re-run
   and must not reprocess unchanged data.

---

## 2. Repository Layout

```
pkb/
  tutorials/
  how-to/
  reference/
  explanation/

  journal/
    2026/
      08/
        2026-08-31.md

  inbox/

  sources/
    memos/
    gitlab/
    beads/

  .pkb/
    config.yml
    cursors.json          # per-source incremental sync cursors
    fts.db                # SQLite FTS5 index (gitignored)
    embeddings.db          # optional vector index (gitignored)
    schema.sql
    scripts/
      sync_memos.py
      sync_gitlab.py
      sync_beads.py
      index_fts.py
      index_embeddings.py
      triage_report.py
      validate_frontmatter.py

  .gitignore               # excludes .pkb/*.db
```

**Rules:**
- `tutorials/`, `how-to/`, `reference/`, `explanation/` — hand-curated or triaged core
  content only. Nothing is written here automatically by a sync script.
- `journal/YYYY/MM/YYYY-MM-DD.md` — one file per day, append-only during the day.
- `inbox/` — landing zone for anything not yet triaged. Nothing is permanent here (see §5).
- `sources/<tool>/` — raw, machine-written mirrors of external systems. Never hand-edited.
  Safe to delete and re-sync from cursor 0 at any time (except for locally-added links).
- `.pkb/` — all generated/config data. Databases are gitignored; `config.yml`, `cursors.json`,
  and `schema.sql` are committed so the repo is portable and reproducible on a fresh clone.

---

## 3. Frontmatter Schema (all files)

```yaml
---
id: 2026-08-31-1423              # required, sortable unique id: YYYY-MM-DD-HHMM[-n]
created: 2026-08-31T14:23:00-06:00   # required, ISO 8601 with offset
updated: 2026-08-31T14:23:00-06:00   # required, bumped on every edit
type: reference                   # required: tutorial|how-to|reference|explanation|journal|inbox|source
extension: null                   # null | journal | inbox | source — null means fully-triaged core content
source: manual                    # manual|memos|gitlab|beads
source_id: null                   # external id (memo id, issue iid, bead id) if source != manual
tags: [tailscale, networking]
links: [2026-08-20-0900]          # ids of related pkb entries
title: "Tailscale sidecar config for Synology"
---
```

**Validation rules (enforced by `validate_frontmatter.py`, run in CI / pre-commit):**
- `type` must be one of the 7 enum values above.
- If `type` is one of the 4 Diataxis types, `extension` **must** be `null`.
- If `type` is `journal`, `inbox`, or `source`, `extension` must match `type`.
- `id` must be unique repo-wide and match the timestamp-based pattern.
- `links` targets must resolve to an existing `id` in the repo (broken-link check).
- Any file failing validation blocks commit (pre-commit hook) and is flagged in CI.

This single schema is what makes the repo queryable as a graph and searchable by
facet without a database — the database is just a derived index, never authoritative.

---

## 4. Ingestion Pipelines

One script per source, all following the same contract:

| Source | Script | Cursor stored | API/mechanism |
|---|---|---|---|
| usememos | `sync_memos.py` | last memo `id`/timestamp | REST API `/api/v1/memos`, over Tailscale |
| GitLab | `sync_gitlab.py` | last `updated_after` timestamp | `glab api` or GitLab REST API, filtered by project |
| beads | `sync_beads.py` | last bead timestamp/id | local `bd` CLI or its backing store, queried directly |

**Contract each script must satisfy:**
1. Read its cursor from `.pkb/cursors.json`.
2. Fetch only items created/updated after the cursor.
3. Write one markdown file per item into `sources/<tool>/`, with frontmatter populated
   (`source`, `source_id`, `created`, `type: source`, `extension: source`).
4. Append a corresponding stub file into `inbox/` for anything that looks like it needs
   human triage (heuristic: configurable per source, e.g. all new GitLab issues; only
   memos above a length threshold).
5. Update the cursor **only after a successful full write**, so a crash mid-run re-fetches
   safely (idempotent — re-writing an already-synced item must not duplicate it; key on
   `source_id`).
6. Exit non-zero with a clear message on auth/network failure; never partially advance
   the cursor on failure.

Runnable via cron, systemd timer, or a Claude Code slash command (`/sync`). Since ingestion
requires network/API access, it is explicitly the *only* part of the system allowed to
depend on connectivity — search and editing must work without it.

---

## 5. Inbox Triage

`inbox/` is a queue, not storage. Enforcement:

- `triage_report.py` scans `inbox/` and flags any file older than a configurable threshold
  (default 14 days) as overdue.
- Every inbox file must end in one of two states:
  - **Promoted:** content rewritten/moved into `tutorials/`, `how-to/`, `reference/`, or
    `explanation/`, with `extension` set to `null` and a fresh `id` if substantially rewritten.
  - **Redirected:** recognized as chronological/log content and appended into the
    appropriate `journal/YYYY/MM/YYYY-MM-DD.md` instead, then deleted from `inbox/`.
  - **Discarded:** deleted outright if not worth keeping.
- Nothing is ever left in `inbox/` indefinitely; the triage report is the enforcement
  mechanism, run on the same cadence as ingestion.

---

## 6. Search & Indexing

### Tier 1 — zero-setup, always available
`rg` (ripgrep) and `fzf` operate directly on the markdown tree. No index required, works
immediately after `git clone`, and is the fallback if `.pkb/*.db` doesn't exist yet.

### Tier 2 — SQLite FTS5 (`index_fts.py`)
- Schema (`schema.sql`): one FTS5 virtual table over `(id, title, body, tags, type)`,
  plus a regular table mirroring frontmatter fields for filtered queries (`type=`,
  `tag=`, `source=`, date ranges).
- **Incremental indexing:** compare each file's `updated` frontmatter field (not mtime,
  which git doesn't preserve) against the value stored at last index time; only
  re-index changed/new files. Deleted files are pruned by diffing the file list.
- Exposed via a `kb` CLI (see §7).
- Default scope: core Diataxis content only. `--all` flag includes journal/inbox/sources.

### Tier 3 — Embeddings (`index_embeddings.py`) — deferred, not built up front
- Add only once FTS5 relevance measurably degrades (large corpus of similar-topic notes).
- `sqlite-vec` extension keeps it a single-file, offline, no-API-call solution.
- Same incremental-update pattern as FTS5.

---

## 7. `kb` CLI — Command Surface

```
kb search <query> [--type tutorial|how-to|reference|explanation] [--tag x] [--all]
kb new <type> "<title>"          # scaffolds a new core file with valid frontmatter
kb journal [today|<date>]         # opens/creates the day's journal file
kb inbox                          # lists current inbox contents with age
kb triage                         # runs triage_report.py
kb sync [memos|gitlab|beads|all]  # runs the relevant ingestion script(s)
kb index [--full]                 # runs incremental (or full) FTS5 reindex
kb validate                       # runs validate_frontmatter.py across the repo
kb links <id>                     # shows backlinks/forward-links for an entry
```

Implementation: a thin Python (or shell) wrapper dispatching to the scripts in
`.pkb/scripts/`. No daemon/server process — every invocation is a one-shot script run.

---

## 8. Journal Handling

- One file per day: `journal/YYYY/MM/YYYY-MM-DD.md`, created on first write via `kb journal`.
- `type: journal`, `extension: journal` frontmatter.
- Journals reference core content via `links:`, they do not duplicate it — e.g., a
  day's entry links to a `reference/` doc rather than containing the write-up itself.
- **Monthly rollup (deferred, low priority):** a periodic job that generates
  `reference/journal-summaries/YYYY-MM.md` summarizing the month's entries, for browsing
  without opening 30 daily files. Not required for v1.

---

## 9. Build Order (phased for handoff)

| Phase | Deliverable | Depends on |
|---|---|---|
| 1 | Repo scaffold: folder layout, `.gitignore`, `schema.sql`, `config.yml` | — |
| 2 | `validate_frontmatter.py` + pre-commit hook | 1 |
| 3 | `kb new`, `kb journal` (manual content creation works end-to-end) | 1, 2 |
| 4 | `rg`/`fzf` documented as day-one search (no code, just usage doc) | 1 |
| 5 | `index_fts.py` + `kb search` | 1, 2 |
| 6 | `sync_memos.py` (first ingestion source — usememos is the active capture point) | 1, 2 |
| 7 | `triage_report.py` + `kb triage`, `kb inbox` | 6 |
| 8 | `sync_gitlab.py`, `sync_beads.py` (same contract as memos) | 1, 2 |
| 9 | `kb links` (graph/backlink queries from frontmatter) | 2 |
| 10 | Embeddings layer (`index_embeddings.py`) — only if FTS5 relevance proves insufficient | 5 |
| 11 | Monthly journal rollups | 8 (journal volume exists) |

Phases 1–7 constitute a usable v1. Phases 8–11 extend it without any architectural change.

---

## 10. Explicit Non-Goals (v1)

- No web UI or server process — CLI/file-based only, per the portability requirement.
- No real-time sync/webhooks from usememos/GitLab/beads — polling/cursor-based batch sync only.
- No automatic Diataxis classification of inbox content — triage is human-in-the-loop.
- No multi-device conflict resolution beyond standard git merge — this is a single-user,
  git-synced repo, not a CRDT system.
