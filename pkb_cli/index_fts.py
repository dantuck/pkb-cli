#!/usr/bin/env python3
"""Incremental SQLite FTS5 indexer (Tier 2 search).

Compares each file's frontmatter `updated` timestamp (not mtime, which git
doesn't preserve) against the value stored at last index time. Only
changed/new files are re-indexed; files removed from disk are pruned.
"""
import os
import sqlite3
import sys

from . import pkb_common as pc
from . import semantic


def get_db(root):
    db_path = os.path.join(root, ".pkb", "fts.db")
    conn = sqlite3.connect(db_path)
    # schema.sql is tool-owned, not data-repo-owned -- ships inside the package
    # (templates/), not copied into every data repo that uses it.
    schema_path = os.path.join(os.path.dirname(__file__), "templates", "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def index_repo(root, full=False, quiet=False, allow_download=False):
    """Bring the keyword index -- and, when the `semantic` extra and its model are
    available, the embeddings -- in line with the files on disk. `allow_download`
    lets a missing embedding model be fetched once (kb index / kb setup only)."""
    conn = get_db(root)
    cur = conn.cursor()

    if full:
        cur.execute("DELETE FROM fts")
        cur.execute("DELETE FROM files")
        # embeddings are kept: they're keyed by text hash + model, so sync_embeddings
        # reuses the unchanged ones and drops the rest

    cur.execute("SELECT id, path, indexed_updated FROM files")
    indexed = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    model_ref = semantic.active_model(root)
    seen_ids = set()
    wanted = {}  # id -> text to embed (only when semantic is on); hash-compared later, not `updated`
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
        if model_ref:
            wanted[entry_id] = semantic.entry_text(fm, body)

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
    embedded, note = _embed(conn, model_ref, wanted, allow_download)
    conn.close()
    if not quiet:
        extra = f", {embedded} embedded" if embedded else ""
        print(f"index: {added} added, {updated} updated, {len(stale_ids)} pruned, {skipped} unchanged{extra}")
        if note:
            print(f"semantic index skipped: {note}", file=sys.stderr)


def _embed(conn, model_ref, wanted, allow_download):
    """(entries embedded, why not) -- never raises: the keyword index is already
    committed, and a missing optional extra or model must not fail a write."""
    if not model_ref:
        return 0, None  # not opted in / extra not installed: stay silent
    try:
        if allow_download:
            # kb index / kb setup: make sure the model is on disk even when no entry needs
            # embedding (a cleared cache, a new machine with a copied db), so search works after
            semantic.check_model(model_ref, allow_download=True)
        return semantic.sync_embeddings(conn, model_ref, wanted, allow_download=allow_download), None
    except semantic.SemanticUnavailable as e:
        return 0, str(e)


def main():
    root = pc.get_repo_root()
    full = "--full" in sys.argv[1:]
    index_repo(root, full=full, allow_download=True)


if __name__ == "__main__":
    main()
