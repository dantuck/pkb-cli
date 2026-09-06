# Why search is tiered

Search is deliberately layered so the knowledge base is always searchable —
even on a fresh clone, before any tooling has run — and so more powerful
search is opt-in rather than a prerequisite. This follows directly from the
[offline-first and portability principles](design.md).

## Tier 1 — zero-setup, always available

`rg` (ripgrep) and `fzf` operate directly on the markdown tree. No index
required, works immediately after `git clone`, and is the fallback if
`.pkb/*.db` doesn't exist yet. See [how-to: search without the
CLI](../how-to/search-without-the-cli.md) for usage.

## Tier 2 — SQLite FTS5

- Schema (`schema.sql`): one FTS5 virtual table over `(id, title, body, tags,
  type)`, plus a regular table mirroring frontmatter fields for filtered
  queries (`type=`, `tag=`, `source=`, date ranges).
- **Incremental indexing:** compare each file's `updated` frontmatter field
  (not mtime, which git doesn't preserve) against the value stored at last
  index time; only re-index changed/new files. Deleted files are pruned by
  diffing the file list.
- Exposed via `kb search` — see [reference: CLI](../reference/cli.md).
- Default scope: core Diataxis content only. `--all` includes
  journal/inbox/sources.

This is what most day-to-day use runs on once `kb setup` has built the index:
relevance ranking and faceted queries that plain `rg` can't give you.

## Tier 3 — embeddings (deferred, not built up front)

- Add only once FTS5 relevance measurably degrades (large corpus of
  similar-topic notes).
- `sqlite-vec` extension keeps it a single-file, offline, no-API-call
  solution.
- Same incremental-update pattern as FTS5.

Deferring this tier is deliberate: it's real added complexity that most
corpora never need, so it isn't built until FTS5 actually falls short.
