"""Semantic search: embeddings are kept in step by the incremental indexer, search
fuses them with FTS5 results, and everything degrades to keyword search when the
optional extra or its model is missing. Uses a tiny fake model, so no real model
(or network) is needed -- only numpy, and those tests skip without it."""
import os
import tempfile
import unittest
from unittest import mock

from pkb_cli import cli as kb_cli  # noqa: E402
from pkb_cli import index_fts, semantic  # noqa: E402
from pkb_cli import pkb_common as pc  # noqa: E402

try:
    import numpy as np
except ImportError:  # the semantic extra (and numpy) isn't installed
    np = None

# a "model" whose notion of meaning is a fixed concept per word
CONCEPTS = {"bread": 0, "baking": 0, "flour": 0, "vpn": 1, "tailnet": 1, "remote": 1,
            "flaky": 2, "intermittent": 2, "failures": 2}


class FakeModel:
    def __init__(self):
        self.encoded = []

    def encode(self, texts, normalize=True, max_length=None):
        self.encoded.extend(texts)
        out = np.zeros((len(texts), 3), dtype="float32")
        for row, text in enumerate(texts):
            for word in text.lower().replace("\n", " ").split():
                if word.strip(".,") in CONCEPTS:
                    out[row, CONCEPTS[word.strip(".,")]] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class FuseTest(unittest.TestCase):
    def test_rank_fusion_prefers_ids_both_lists_agree_on(self):
        fused = semantic.fuse(["a", "b", "c"], ["c", "d"])
        self.assertEqual(fused[0][0], "c")
        self.assertEqual({i for i, _ in fused}, {"a", "b", "c", "d"})


@unittest.skipIf(np is None, "numpy (the semantic extra) isn't installed")
class SemanticIndexTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        os.makedirs(os.path.join(self.root, ".pkb"))
        for d in pc.TYPE_DIR.values():
            os.makedirs(os.path.join(self.root, d))
        self.model = FakeModel()
        for patch in (mock.patch.object(semantic, "missing_dependency", lambda: None),
                      mock.patch.object(semantic, "check_model", lambda ref, allow_download=False: "/model"),
                      mock.patch.object(semantic, "load_model", lambda ref, allow_download=False: self.model),
                      mock.patch.dict(os.environ, {}, clear=False)):
            patch.start()
            self.addCleanup(patch.stop)
        os.environ.pop("PKB_SEMANTIC_MODEL", None)

    def write(self, entry_id, title, body, entry_type="how-to"):
        fm = {"id": entry_id, "created": "2026-01-01T00:00:00+00:00", "updated": pc.now_iso(),
              "type": entry_type, "extension": None, "source": "manual", "source_id": None,
              "tags": [], "links": [], "title": title}
        path = os.path.join(self.root, pc.TYPE_DIR[entry_type], f"{entry_id}.md")
        pc.write_entry(path, fm, body)
        return path

    def count(self, table="embeddings"):
        conn = index_fts.get_db(self.root)
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    def test_only_changed_entries_are_reembedded(self):
        path = self.write("a", "Starter", "Feed flour daily")
        self.write("b", "Mesh", "tailnet vpn")
        index_fts.index_repo(self.root, quiet=True)
        self.assertEqual((self.count(), len(self.model.encoded)), (2, 2))

        index_fts.index_repo(self.root, quiet=True)  # nothing changed
        self.assertEqual(len(self.model.encoded), 2)

        fm, _ = pc.read_entry(path)
        pc.write_entry(path, fm, "Feed flour daily, then bread")  # hand-edited body, same `updated`
        index_fts.index_repo(self.root, quiet=True)
        self.assertEqual(len(self.model.encoded), 3)

    def test_deleted_entries_are_pruned_and_model_change_rebuilds(self):
        path = self.write("a", "Starter", "flour")
        self.write("b", "Mesh", "vpn")
        index_fts.index_repo(self.root, quiet=True)
        os.remove(path)
        index_fts.index_repo(self.root, quiet=True)
        self.assertEqual(self.count(), 1)

        with mock.patch.object(semantic, "config", lambda root: (True, "other/model")):
            index_fts.index_repo(self.root, quiet=True)
        self.assertEqual(len(self.model.encoded), 3)  # b embedded again by the new model
        conn = index_fts.get_db(self.root)
        self.assertEqual(semantic.stored_model(conn), "other/model")
        conn.close()

    def test_unavailable_model_never_breaks_indexing(self):
        self.write("a", "Starter", "flour")

        def boom(ref, allow_download=False):
            raise semantic.SemanticUnavailable("no model")

        with mock.patch.object(semantic, "load_model", boom):
            index_fts.index_repo(self.root, quiet=True)
        self.assertEqual((self.count("files"), self.count()), (1, 0))

    def test_search_modes(self):
        self.write("a", "Sourdough starter", "Feed flour daily.")
        self.write("b", "Mesh network", "Join the tailnet.")
        index_fts.index_repo(self.root, quiet=True)

        # "baking" appears in no note: keyword finds nothing, meaning finds the bread one
        self.assertEqual(kb_cli.search_entries(self.root, "baking", mode="keyword"), [])
        self.assertEqual([r[0] for r in kb_cli.search_entries(self.root, "baking", mode="semantic")], ["a"])
        self.assertEqual([r[0] for r in kb_cli.search_entries(self.root, "baking")], ["a"])  # auto = hybrid

        # an exact word still works, and type scoping applies to both halves
        self.assertEqual([r[0] for r in kb_cli.search_entries(self.root, "tailnet")], ["b"])
        self.assertEqual(kb_cli.search_entries(self.root, "baking", entry_type="reference"), [])

    def test_search_builds_missing_vectors_and_notes_it(self):
        self.write("a", "Sourdough starter", "Feed flour daily.")
        with mock.patch.object(semantic, "missing_dependency", lambda: "numpy isn't installed"):
            index_fts.index_repo(self.root, quiet=True)  # indexed before the extra was installed
        self.assertEqual(self.count(), 0)

        notes = []
        rows = kb_cli.search_entries(self.root, "baking", notes=notes)
        self.assertEqual([r[0] for r in rows], ["a"])
        self.assertEqual(self.count(), 1)
        self.assertTrue(any("building the semantic index" in n for n in notes))

    def test_missing_model_falls_back_to_keyword_but_semantic_mode_errors(self):
        self.write("a", "Sourdough starter", "Feed flour daily.")
        index_fts.index_repo(self.root, quiet=True)
        self.model = None

        def boom(ref, allow_download=False):
            raise semantic.SemanticUnavailable("model isn't on disk")

        conn = index_fts.get_db(self.root)
        conn.execute("DELETE FROM embeddings")
        conn.commit()
        conn.close()
        with mock.patch.object(semantic, "check_model", boom), mock.patch.object(semantic, "load_model", boom):
            notes = []
            rows = kb_cli.search_entries(self.root, "flour", notes=notes)
            self.assertEqual([r[0] for r in rows], ["a"])  # keyword still answers
            self.assertTrue(any("keyword only" in n for n in notes))
            with self.assertRaises(semantic.SemanticUnavailable):
                kb_cli.search_entries(self.root, "flour", mode="semantic")

    def test_index_and_setup_fetch_a_missing_model_even_with_nothing_to_embed(self):
        self.write("a", "Starter", "flour")
        index_fts.index_repo(self.root, quiet=True)  # fully embedded already
        fetched = []

        def fake_check(ref, allow_download=False):
            fetched.append(allow_download)

        with mock.patch.object(semantic, "check_model", fake_check):
            index_fts.index_repo(self.root, quiet=True)  # a write command: never downloads
            self.assertEqual(fetched, [])
            index_fts.index_repo(self.root, quiet=True, allow_download=True)  # kb index / kb setup
        self.assertEqual(fetched[0], True)

    def test_full_reindex_reuses_unchanged_vectors(self):
        self.write("a", "Starter", "flour")
        index_fts.index_repo(self.root, quiet=True)
        index_fts.index_repo(self.root, full=True, quiet=True)
        self.assertEqual((self.count(), len(self.model.encoded)), (1, 1))

    def test_disabled_in_config_is_inert(self):
        self.write("a", "Sourdough starter", "Feed flour daily.")
        with mock.patch.object(semantic, "config", lambda root: (False, "m")):
            index_fts.index_repo(self.root, quiet=True)
            self.assertEqual(self.count(), 0)
            self.assertEqual(kb_cli.search_entries(self.root, "baking"), [])


if __name__ == "__main__":
    unittest.main()
