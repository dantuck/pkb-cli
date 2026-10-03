-- SQLite FTS5 index schema for the pkb search tier (Tier 2).
-- Committed so the index is reproducible on a fresh clone via `kb index --full`.
-- The .db file itself is gitignored: it is a derived cache, never authoritative.

CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    id,
    title,
    body,
    tags,
    type UNINDEXED
);

-- Mirrors frontmatter fields for filtered/faceted queries (type=, tag=, source=, date ranges)
-- without needing to touch the fts5 match index.
CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    type TEXT NOT NULL,
    extension TEXT,
    source TEXT,
    source_id TEXT,
    created TEXT,
    updated TEXT,
    tags TEXT,
    title TEXT,
    indexed_updated TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_files_type ON files(type);
CREATE INDEX IF NOT EXISTS idx_files_source ON files(source);
CREATE INDEX IF NOT EXISTS idx_files_created ON files(created);
