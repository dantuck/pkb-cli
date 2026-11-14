"""Querying the index: FTS5 keyword matching (with a plain-terms fallback), semantic ranking, and their fusion. Shared by kb search and kb web."""
import os
import re
import sqlite3

from . import index_fts
from . import pkb_common as pc
from . import semantic

_FTS_OPERATORS = {"AND", "OR", "NOT", "NEAR"}


def _fts_autoprefix(query):
    """Append FTS5 prefix syntax (*) to bare word tokens, so `kb search dar`
    matches "Darren" the way a user typing a partial word expects. Leaves
    quoted phrases, boolean operators, column filters (title:...), and
    tokens that already use FTS5 syntax (*, parens, ^) untouched."""
    tokens = re.findall(r'"[^"]*"|\S+', query)
    out = []
    for tok in tokens:
        if tok.startswith('"') or tok.upper() in _FTS_OPERATORS or any(c in tok for c in '*():^'):
            out.append(tok)
        else:
            out.append(tok + "*")
    return " ".join(out)


def _fts_literal(query):
    """Fallback for a query FTS5 rejected (unbalanced quote, stray `-`, ...): treat each
    run of word characters as a plain prefix term, ignoring all FTS5 syntax."""
    words = re.findall(r"\w+", query)
    return " ".join(f'"{w}"*' for w in words)


def search_entries(root, query, entry_type=None, tag=None, include_all=False, limit=50,
                   mode="auto", notes=None):
    """Search the index, returning (id, title, type, path, rank) rows best first --
    the core of `kb search`, factored out so both the CLI and `kb web`'s
    /api/search can call the same query. Returns None if there's no index (distinct
    from "no results", an empty list). Raises sqlite3.OperationalError on a bad FTS5
    query (e.g. unbalanced quotes) so callers can report it however fits their
    context (stderr print vs. a JSON error response).

    `mode`: "keyword" (FTS5 only), "semantic" (embeddings only; raises
    semantic.SemanticUnavailable if they can't be used), or "auto" -- keyword and
    semantic fused when the semantic index is usable, plain keyword otherwise.
    Anything worth telling the user (an index being built, a model that's
    missing) is appended to `notes` rather than printed, so --json stays clean."""
    db_path = os.path.join(root, ".pkb", "fts.db")
    if not os.path.exists(db_path):
        return None

    conn = index_fts.get_db(root)  # one connection for the whole search; applies any newer tables
    try:
        model_ref = None
        if mode != "keyword":
            notes = notes if notes is not None else []
            try:
                model_ref = _semantic_ready(root, conn, notes)
            except semantic.SemanticUnavailable as e:
                if mode == "semantic":
                    raise
                if not e.expected:
                    notes.append(f"semantic search unavailable, using keyword only: {e}")
        if model_ref is None:
            return _fts_rows(conn, query, entry_type, tag, include_all, limit)

        allow_types = {entry_type} if entry_type else None if include_all else set(pc.CORE_TYPES)
        ranked = semantic.rank(conn, model_ref, query, allow_types=allow_types, tag=tag, limit=limit)
        by_id = {}
        if mode != "semantic":
            try:
                kw_rows = _fts_rows(conn, query, entry_type, tag, include_all, limit)
            except sqlite3.OperationalError:
                if not ranked:
                    raise
                kw_rows = []  # a query that isn't valid FTS5 can still be a fine semantic one
            ranked = semantic.fuse([r[0] for r in kw_rows], [i for i, _ in ranked])[:limit]
            by_id = {r[0]: r[:4] for r in kw_rows}
        by_id.update(_file_rows(conn, [i for i, _ in ranked if i not in by_id]))
        return [(*by_id[i], score) for i, score in ranked if i in by_id]
    finally:
        conn.close()


def _fts_rows(conn, query, entry_type, tag, include_all, limit):
    match = _fts_autoprefix(query)
    try:
        return _fts_query(conn, match, entry_type, tag, include_all, limit)
    except sqlite3.OperationalError:
        literal = _fts_literal(query)
        if not literal or literal == match:
            raise
        return _fts_query(conn, literal, entry_type, tag, include_all, limit)


def _fts_query(conn, match, entry_type, tag, include_all, limit):
    where = ["fts MATCH ?"]
    params = [match]
    if not include_all:
        placeholders = ",".join("?" for _ in pc.CORE_TYPES)
        where.append(f"fts.type IN ({placeholders})")
        params.extend(pc.CORE_TYPES)
    if entry_type:
        where.append("fts.type = ?")
        params.append(entry_type)
    if tag:
        where.append("fts.tags MATCH ?")
        params.append(tag)

    sql = f"""
        SELECT f.id, f.title, f.type, f.path, bm25(fts) as rank
        FROM fts JOIN files f ON f.id = fts.id
        WHERE {' AND '.join(where)}
        ORDER BY rank LIMIT ?
    """
    return conn.execute(sql, params + [limit]).fetchall()


def _file_rows(conn, ids):
    """{id: (id, title, type, path)} for the given ids."""
    out = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        sql = f"SELECT id, title, type, path FROM files WHERE id IN ({','.join('?' for _ in chunk)})"
        for row in conn.execute(sql, chunk):
            out[row[0]] = row
    return out


def _semantic_ready(root, conn, notes):
    """The model ref if embeddings can be used right now; raises SemanticUnavailable
    (with `expected` set for a plain "not set up") if not. Builds any missing vectors
    first, offline: a model not already on disk is reported, never downloaded."""
    enabled, model_ref = semantic.config(root)
    if not enabled:
        raise semantic.SemanticUnavailable("it's switched off (`semantic.enabled: false` in .pkb/config.yml)",
                                           expected=True)
    semantic.check_model(model_ref)  # fail fast and cheap: extra installed, model on disk
    embedded, total, current = semantic.index_state(conn, model_ref)
    if not current:
        notes.append(f"building the semantic index ({total} entries)...")
        index_fts.index_repo(root, quiet=True)
    return model_ref
