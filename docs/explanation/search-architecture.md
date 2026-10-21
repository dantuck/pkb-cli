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

## Tier 3 — semantic (optional)

Keyword search misses a note that says "VPN mesh" when you search "remote
access". Tier 3 adds one embedding per entry, stored in the same `fts.db`
(`embeddings` table), and `kb search` fuses the two rankings with reciprocal
rank fusion.

- **Opt-in by installing an extra** (`pkb-cli[semantic]`): the base install
  keeps its no-dependencies promise, and without the extra nothing changes.
- **A static model (model2vec) run in-process**, not a server such as Ollama:
  no daemon or open port to be blocked, ~30 MB, milliseconds per entry on
  CPU. A brute-force cosine scan over a personal KB's few thousand vectors
  needs no vector database.
- **Offline-first still holds.** The model is fetched at most once, and only by
  `kb index` / `kb setup`; search never touches the network, and
  `$PKB_SEMANTIC_MODEL` can point at a side-loaded local copy.
- **The same incremental rule as Tier 2**, keyed on a hash of each entry's text
  rather than `updated` (so a hand-edited body is noticed). A different
  model invalidates all vectors. A missing extra or model degrades to Tier 2.

See [how-to: search by meaning](../how-to/search-by-meaning.md).
