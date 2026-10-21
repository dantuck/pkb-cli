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

-- Optional semantic layer (see semantic.py): one unit-length float32 vector per entry,
-- hash = sha1 of the text it was computed from, so only changed entries get re-embedded.
-- `meta.embed_model` records which model produced them; a different model wipes the table.
CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    vec BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_type ON files(type);
CREATE INDEX IF NOT EXISTS idx_files_source ON files(source);
CREATE INDEX IF NOT EXISTS idx_files_created ON files(created);
