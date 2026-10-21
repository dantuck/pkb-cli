"""Semantic (embedding) search, the optional layer on top of the FTS5 index.

Everything here degrades to "not available": the `semantic` extra
(`pip install 'pkb-cli[semantic]'`, which brings model2vec + numpy) isn't
required, and nothing at search/write time touches the network. A small static
model runs in-process on CPU; vectors live in the same `.pkb/fts.db` as the
keyword index, one per entry, keyed by a hash of the text that produced them
so the incremental indexer re-embeds exactly what changed.

The model comes from, in order: `$PKB_SEMANTIC_MODEL` (a Hugging Face id or a
local directory -- side-load the files by any approved route and kb never
needs the network), then `semantic.model` in .pkb/config.yml. Only
`kb index` / `kb setup` (allow_download=True) may fetch a missing model.
"""
import functools
import hashlib
import importlib.util
import os

MAX_LENGTH = 1024  # tokens embedded per entry; static models just average, so longer is cheap
MIN_SCORE = 0.15   # cosine floor -- below this a "match" is noise, not meaning
RRF_K = 60         # reciprocal-rank-fusion constant (the usual default)


class SemanticUnavailable(Exception):
    """Semantic search can't run here; the message says why and what to do.
    `expected` marks a plain "not set up" (switched off, extra not installed) that
    a default search needn't mention."""

    def __init__(self, message, expected=False):
        super().__init__(message)
        self.expected = expected


def config(root):
    """(enabled, model_ref) for this data repo, honoring $PKB_SEMANTIC_MODEL."""
    from . import pkb_common as pc
    section = pc.load_config(root)["semantic"]  # defaults (pc.DEFAULT_CONFIG) are merged in
    return section["enabled"], os.environ.get("PKB_SEMANTIC_MODEL") or section["model"]


@functools.lru_cache(maxsize=None)
def missing_dependency():
    """None if the extra is installed, else a one-line reason. Checks presence
    without importing, so every write command can ask without paying for numpy."""
    for name in ("numpy", "model2vec"):
        if importlib.util.find_spec(name) is None:
            return f"{name} isn't installed -- `uv tool install 'pkb-cli[semantic]'`"
    return None


def active_model(root):
    """The model ref if semantic indexing is on for this repo (enabled and the extra
    installed), else None."""
    enabled, model_ref = config(root)
    return model_ref if enabled and not missing_dependency() else None


@functools.lru_cache(maxsize=None)  # successes only (exceptions aren't cached); a search resolves twice
def _resolve_model_dir(ref, allow_download):
    if os.path.isdir(ref):
        return ref
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(ref, local_files_only=not allow_download)
    except Exception as e:  # not cached (offline), or the download itself failed
        hint = ("run `kb index` once with network access to fetch it, or point $PKB_SEMANTIC_MODEL "
                "at a local copy of the model directory")
        raise SemanticUnavailable(f"model '{ref}' isn't available locally ({type(e).__name__}) -- {hint}")


_MODELS = {}  # resolved model dir -> loaded model; kb web is long-lived, and a search loads it twice


def check_model(ref, allow_download=False):
    """The local directory holding `ref`'s files, without loading the model. Raises
    SemanticUnavailable if the extra or the files are missing. Never reaches the
    network unless allow_download."""
    reason = missing_dependency()
    if reason:
        raise SemanticUnavailable(reason, expected=not os.environ.get("PKB_SEMANTIC_MODEL"))
    return _resolve_model_dir(ref, allow_download)


def load_model(ref, allow_download=False):
    """The StaticModel for `ref` (cached). Raises SemanticUnavailable as check_model does."""
    model_dir = check_model(ref, allow_download)
    if model_dir not in _MODELS:
        from model2vec import StaticModel
        _MODELS[model_dir] = StaticModel.from_pretrained(model_dir, force_download=False)
    return _MODELS[model_dir]


def encode(model, texts):
    """Unit-length float32 vectors, one row per text (cosine == dot product)."""
    return model.encode(list(texts), normalize=True, max_length=MAX_LENGTH).astype("float32")


def entry_text(fm, body):
    return "\n".join([fm.get("title") or "", " ".join(str(t) for t in fm.get("tags") or []), body or ""])


def text_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def sync_embeddings(conn, model_ref, wanted, allow_download=False):
    """Bring the `embeddings` table in line with `wanted` ({id: text}): drop rows
    for entries that are gone or were embedded by a different model, then embed
    only the entries whose text hash changed. Returns the number embedded.
    Raises SemanticUnavailable (leaving the table untouched) if there's work to
    do but the model can't be loaded."""
    cur = conn.cursor()
    row = cur.execute("SELECT value FROM meta WHERE key = 'embed_model'").fetchone()
    stale_model = row is not None and row[0] != model_ref

    have = {} if stale_model else dict(cur.execute("SELECT id, hash FROM embeddings").fetchall())
    hashes = {entry_id: text_hash(text) for entry_id, text in wanted.items()}
    todo = [entry_id for entry_id, h in hashes.items() if have.get(entry_id) != h]
    gone = [entry_id for entry_id in have if entry_id not in wanted]

    vectors = []
    if todo:
        model = load_model(model_ref, allow_download=allow_download)
        vectors = encode(model, [wanted[entry_id] for entry_id in todo])
    if stale_model:
        cur.execute("DELETE FROM embeddings")
    cur.executemany("DELETE FROM embeddings WHERE id = ?", [(entry_id,) for entry_id in gone])
    cur.executemany(
        "INSERT OR REPLACE INTO embeddings (id, hash, vec) VALUES (?, ?, ?)",
        [(entry_id, hashes[entry_id], vec.tobytes()) for entry_id, vec in zip(todo, vectors)])
    if todo or stale_model:
        cur.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embed_model', ?)", (model_ref,))
    conn.commit()
    return len(todo)


def index_state(conn, model_ref):
    """(embedded, total, current): how many indexed entries have a vector, and whether
    the vectors are complete and were made by `model_ref`. Raises
    sqlite3.OperationalError on a database that predates the embeddings tables."""
    total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    embedded = conn.execute("SELECT COUNT(*) FROM files f JOIN embeddings e ON e.id = f.id").fetchone()[0]
    return embedded, total, embedded == total and stored_model(conn) in (None, model_ref)


def stored_model(conn):
    row = conn.execute("SELECT value FROM meta WHERE key = 'embed_model'").fetchone()
    return row[0] if row else None


def rank(conn, model_ref, query, allow_types=None, tag=None, limit=50):
    """[(id, cosine)] best first for `query` among embedded entries, applying the
    same type/tag scope as the keyword search. Raises SemanticUnavailable."""
    import numpy as np
    model = load_model(model_ref)
    q = encode(model, [query])[0]

    sql = "SELECT e.id, e.vec, f.type, f.tags FROM embeddings e JOIN files f ON f.id = e.id"
    ids, blobs = [], []
    for entry_id, blob, entry_type, tags in conn.execute(sql):
        if allow_types is not None and entry_type not in allow_types:
            continue
        if tag and tag not in (tags or "").split():
            continue
        ids.append(entry_id)
        blobs.append(blob)
    if not ids:
        return []
    scores = np.frombuffer(b"".join(blobs), dtype="float32").reshape(len(ids), -1) @ q
    order = np.argsort(-scores)[:limit]
    return [(ids[i], float(scores[i])) for i in order if scores[i] >= MIN_SCORE]


def fuse(keyword_ids, semantic_ids):
    """Reciprocal-rank fusion of two best-first id lists -> [(id, score)] best first."""
    scores = {}
    for ranked in (keyword_ids, semantic_ids):
        for position, entry_id in enumerate(ranked):
            scores[entry_id] = scores.get(entry_id, 0.0) + 1.0 / (RRF_K + position + 1)
    return sorted(scores.items(), key=lambda kv: -kv[1])
