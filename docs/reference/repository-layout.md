# Data repo layout

> **Implementation note (2026-09-01):** two things below have diverged from
> the original spec, documented here rather than by rewriting history: the
> `scripts/` (and `schema.sql`) now live in this separate tool repo
> (`pkb-cli`), not inside `.pkb/` in the data repo — see the top-level
> [README](../../README.md). And the default data repo location is `~/.pkb`
> itself (the central kb), not an arbitrarily-named `pkb/` directory
> elsewhere — `kb` still supports a differently-located repo if you're
> standing inside one, `~/.pkb` is only the fallback. The internal structure
> below (content dirs alongside `.pkb/`) is otherwise unchanged.

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

  .gitignore               # excludes .pkb/*.db
```

**Rules:**
- `tutorials/`, `how-to/`, `reference/`, `explanation/` — hand-curated or
  triaged core content only. Nothing is written here automatically by a sync
  script.
- `journal/YYYY/MM/YYYY-MM-DD.md` — one file per day, append-only during the
  day. See [journal](journal.md).
- `inbox/` — landing zone for anything not yet triaged. Nothing is permanent
  here — see [how-to: triage the inbox](../how-to/triage-the-inbox.md).
- `sources/<tool>/` — raw, machine-written mirrors of external systems. Never
  hand-edited. Safe to delete and re-sync from cursor 0 at any time (except
  for locally-added links).
- `.pkb/` — all generated/config data. Databases are gitignored;
  `config.yml`, `cursors.json`, and `schema.sql` are committed so the repo is
  portable and reproducible on a fresh clone.

This single-schema-plus-fixed-layout design is what makes the repo queryable
as a graph and searchable by facet without a database — the database (`kb
index`) is just a derived index, never authoritative. See [explanation:
design principles](../explanation/design.md).
