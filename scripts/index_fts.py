#!/usr/bin/env python3
"""Incremental SQLite FTS5 indexer (Tier 2 search).

Compares each file's frontmatter `updated` timestamp (not mtime, which git
doesn't preserve) against the value stored at last index time. Only
changed/new files are re-indexed; files removed from disk are pruned.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pkb_common as pc
import sqlite3


def get_db(root):
    db_path = os.path.join(root, ".pkb", "fts.db")
    conn = sqlite3.connect(db_path)
    # schema.sql is tool-owned, not data-repo-owned -- ships in templates/ next to
    # scripts/ in the pkb-cli repo, not copied into every data repo that uses it.
    schema_path = os.path.join(os.path.dirname(__file__), "..", "templates", "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def index_repo(root, full=False):
    conn = get_db(root)
    cur = conn.cursor()

    if full:
        cur.execute("DELETE FROM fts")
        cur.execute("DELETE FROM files")

    cur.execute("SELECT id, path, indexed_updated FROM files")
    indexed = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    seen_ids = set()
    added, updated, skipped = 0, 0, 0

    for path in pc.iter_markdown_files(root):
        rel = os.path.relpath(path, root)
        try:
            fm, body = pc.read_entry(path)
        except ValueError:
            continue
        entry_id = fm.get("id")
        if not entry_id:
            continue
        seen_ids.add(entry_id)

        prev = indexed.get(entry_id)
        current_updated = fm.get("updated") or ""
        if prev is not None and prev[0] == rel and prev[1] == current_updated:
            skipped += 1
            continue

        tags = fm.get("tags") or []
        tags_str = " ".join(str(t) for t in tags)

        cur.execute("DELETE FROM fts WHERE id = ?", (entry_id,))
        cur.execute(
            "INSERT INTO fts (id, title, body, tags, type) VALUES (?, ?, ?, ?, ?)",
            (entry_id, fm.get("title") or "", body, tags_str, fm.get("type") or ""),
        )
        cur.execute(
            """INSERT INTO files (id, path, type, extension, source, source_id, created, updated, tags, title, indexed_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 path=excluded.path, type=excluded.type, extension=excluded.extension,
                 source=excluded.source, source_id=excluded.source_id, created=excluded.created,
                 updated=excluded.updated, tags=excluded.tags, title=excluded.title,
                 indexed_updated=excluded.indexed_updated""",
            (
                entry_id, rel, fm.get("type"), fm.get("extension"), fm.get("source"),
                fm.get("source_id"), fm.get("created"), fm.get("updated"), tags_str,
                fm.get("title"), current_updated,
            ),
        )
        if prev is None:
            added += 1
        else:
            updated += 1

    # prune deleted files
    stale_ids = set(indexed.keys()) - seen_ids
    for stale_id in stale_ids:
        cur.execute("DELETE FROM fts WHERE id = ?", (stale_id,))
        cur.execute("DELETE FROM files WHERE id = ?", (stale_id,))

    conn.commit()
    conn.close()
    print(f"index: {added} added, {updated} updated, {len(stale_ids)} pruned, {skipped} unchanged")


def main():
    root = pc.get_repo_root()
    full = "--full" in sys.argv[1:]
    index_repo(root, full=full)


if __name__ == "__main__":
    main()
