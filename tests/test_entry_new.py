"""Tests for scripts/kb's entry_new -- the write path shared by `kb new` (CLI)
and kb web's POST /api/entries. Loads scripts/kb the same way kb_web.py does
(it has no .py suffix, so it isn't import-able by name), against a throwaway
repo dir so nothing here touches a real .pkb.
"""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
import threading
import unittest

SCRIPT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

import pkb_common as pc  # noqa: E402


def _load_kb_cli():
    loader = importlib.machinery.SourceFileLoader("kb_cli", os.path.join(SCRIPT_DIR, "kb"))
    spec = importlib.util.spec_from_loader("kb_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


kb_cli = _load_kb_cli()


class EntryNewTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        for type_dir in pc.TYPE_DIR.values():
            os.makedirs(os.path.join(self.root, type_dir), exist_ok=True)

        # entry_new reindexes in-process via _reindex_fast (not the
        # run_script/subprocess path cmd_new used to use), and the temp repo
        # has no .pkb/fts.db for it to write to -- stub it out like
        # test_entry_update_content.py does.
        self.reindex_calls = 0

        def fake_reindex_fast(root):
            self.reindex_calls += 1

        self._orig_reindex_fast = kb_cli._reindex_fast
        kb_cli._reindex_fast = fake_reindex_fast
        self.addCleanup(lambda: setattr(kb_cli, "_reindex_fast", self._orig_reindex_fast))

    def _make_existing(self, entry_id, entry_type="reference"):
        path = os.path.join(self.root, pc.TYPE_DIR[entry_type], f"{entry_id}-existing.md")
        fm = {
            "id": entry_id, "created": pc.now_iso(), "updated": pc.now_iso(),
            "type": entry_type, "extension": None, "source": "manual", "source_id": None,
            "tags": [], "links": [], "title": "Existing",
        }
        pc.write_entry(path, fm, "Existing body.\n")
        return path

    def test_rejects_unknown_type(self):
        result = kb_cli.entry_new(self.root, "not-a-type", "Title")
        self.assertIn("error", result)
        self.assertEqual(self.reindex_calls, 0)

    def test_rejects_unknown_link_target(self):
        result = kb_cli.entry_new(self.root, "how-to", "Title", links=["0000-00-00-0000"])
        self.assertIn("error", result)
        self.assertEqual(self.reindex_calls, 0)

    def test_creates_minimal_entry(self):
        result = kb_cli.entry_new(self.root, "how-to", "Deploy staging")
        self.assertNotIn("error", result)
        self.assertTrue(os.path.exists(result["path"]))
        self.assertIn(os.path.join(self.root, "how-to"), result["path"])

        fm, body = pc.read_entry(result["path"])
        self.assertEqual(fm["id"], result["id"])
        self.assertEqual(fm["type"], "how-to")
        self.assertEqual(fm["title"], "Deploy staging")
        self.assertEqual(fm["tags"], [])
        self.assertEqual(fm["links"], [])
        self.assertEqual(body.strip(), "# Deploy staging")
        self.assertEqual(self.reindex_calls, 1)

    def test_creates_entry_with_tags_links_and_body(self):
        target_path = self._make_existing("2026-01-01-0000")
        result = kb_cli.entry_new(
            self.root, "explanation", "Why we do this",
            tags=["ops", "deploy"], links=["2026-01-01-0000"], body="Some rationale.",
        )
        self.assertNotIn("error", result)

        fm, body = pc.read_entry(result["path"])
        self.assertEqual(fm["tags"], ["ops", "deploy"])
        self.assertEqual(fm["links"], ["2026-01-01-0000"])
        self.assertIn("Some rationale.", body)
        self.assertEqual(self.reindex_calls, 1)
        self.assertTrue(os.path.exists(target_path))  # link target untouched

    def test_normalizes_tags_and_links(self):
        self._make_existing("2026-01-01-0000")
        result = kb_cli.entry_new(
            self.root, "how-to", "Normalize me",
            tags=[" ops", "ops", "  ", "deploy "],
            links=["2026-01-01-0000", " 2026-01-01-0000 "],
        )
        self.assertNotIn("error", result)
        fm, _ = pc.read_entry(result["path"])
        self.assertEqual(fm["tags"], ["ops", "deploy"])
        self.assertEqual(fm["links"], ["2026-01-01-0000"])

    def test_concurrent_creates_get_distinct_ids(self):
        # Regression test: entry_new used to scan existing ids and write with
        # no lock, so two near-simultaneous creates could compute the same
        # gen_id() and one write would silently clobber the other. Force that
        # interleaving by blocking the first call's write_entry until the
        # second call has had a chance to try (and be forced to wait on the
        # lock instead of racing ahead).
        orig_write_entry = pc.write_entry
        write_started = threading.Event()
        proceed = threading.Event()

        def slow_write_entry(path, fm, body):
            if not write_started.is_set():
                write_started.set()
                proceed.wait(timeout=2)
            orig_write_entry(path, fm, body)

        kb_cli.pc.write_entry = slow_write_entry
        self.addCleanup(lambda: setattr(kb_cli.pc, "write_entry", orig_write_entry))

        results = {}

        def make(key):
            results[key] = kb_cli.entry_new(self.root, "reference", "Race test")

        t1 = threading.Thread(target=make, args=("a",))
        t1.start()
        self.assertTrue(write_started.wait(timeout=2), "first call never reached write_entry")

        t2 = threading.Thread(target=make, args=("b",))
        t2.start()
        t2.join(timeout=0.2)  # t2 should block on the lock, not finish yet
        self.assertTrue(t2.is_alive(), "second call didn't block on the lock")

        proceed.set()
        t1.join(timeout=2)
        t2.join(timeout=2)

        self.assertNotIn("error", results["a"])
        self.assertNotIn("error", results["b"])
        self.assertNotEqual(results["a"]["id"], results["b"]["id"])
        self.assertNotEqual(results["a"]["path"], results["b"]["path"])
        self.assertTrue(os.path.exists(results["a"]["path"]))
        self.assertTrue(os.path.exists(results["b"]["path"]))


if __name__ == "__main__":
    unittest.main()
