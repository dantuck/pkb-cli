"""Tests for scripts/kb's entry_delete and entry_move -- the write paths behind
`kb rm` and `kb mv`. Loads scripts/kb the same way test_entry_new.py does,
against a throwaway repo dir so nothing here touches a real .pkb.
"""
import importlib.machinery
import importlib.util
import os
import sys
import tempfile
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


class EntryDeleteMoveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        for type_dir in pc.TYPE_DIR.values():
            os.makedirs(os.path.join(self.root, type_dir), exist_ok=True)

        self.reindex_calls = 0

        def fake_reindex_fast(root):
            self.reindex_calls += 1

        self._orig_reindex_fast = kb_cli._reindex_fast
        kb_cli._reindex_fast = fake_reindex_fast
        self.addCleanup(lambda: setattr(kb_cli, "_reindex_fast", self._orig_reindex_fast))

    def _make(self, entry_id, entry_type="reference", title="Entry", links=None):
        path = os.path.join(self.root, pc.TYPE_DIR[entry_type], f"{entry_id}-{pc.slugify(title)}.md")
        fm = {
            "id": entry_id, "created": pc.now_iso(), "updated": pc.now_iso(),
            "type": entry_type, "extension": None, "source": "manual", "source_id": None,
            "tags": [], "links": list(links or []), "title": title,
        }
        pc.write_entry(path, fm, f"# {title}\n\nBody.\n")
        return path

    # -- entry_delete --

    def test_delete_rejects_unknown_id(self):
        result = kb_cli.entry_delete(self.root, "0000-00-00-0000")
        self.assertIn("error", result)

    def test_deletes_entry_with_no_backlinks(self):
        path = self._make("2026-01-01-0000")
        result = kb_cli.entry_delete(self.root, "2026-01-01-0000")
        self.assertNotIn("error", result)
        self.assertEqual(result["backlinks_cleared"], 0)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.reindex_calls, 1)

    def test_refuses_delete_with_backlinks_unless_forced(self):
        self._make("2026-01-01-0000")
        linker_path = self._make("2026-01-02-0000", title="Linker", links=["2026-01-01-0000"])

        result = kb_cli.entry_delete(self.root, "2026-01-01-0000")
        self.assertIn("error", result)
        self.assertIn("2026-01-02-0000", result["error"])
        self.assertEqual(self.reindex_calls, 0)

        result = kb_cli.entry_delete(self.root, "2026-01-01-0000", force=True)
        self.assertNotIn("error", result)
        self.assertEqual(result["backlinks_cleared"], 1)
        fm, _body = pc.read_entry(linker_path)
        self.assertEqual(fm["links"], [])

    # -- entry_move --

    def test_rejects_no_change_requested(self):
        self._make("2026-01-01-0000")
        result = kb_cli.entry_move(self.root, "2026-01-01-0000")
        self.assertIn("error", result)

    def test_move_rejects_unknown_id(self):
        result = kb_cli.entry_move(self.root, "0000-00-00-0000", new_type="how-to")
        self.assertIn("error", result)

    def test_rejects_non_core_type(self):
        path = os.path.join(self.root, "journal", "2026-01-01.md")
        fm = {
            "id": "2026-01-01-0000", "created": pc.now_iso(), "updated": pc.now_iso(),
            "type": "journal", "extension": "journal", "source": "manual", "source_id": None,
            "tags": [], "links": [], "title": "2026-01-01",
        }
        pc.write_entry(path, fm, "# 2026-01-01\n\n")
        result = kb_cli.entry_move(self.root, "2026-01-01-0000", new_type="how-to")
        self.assertIn("error", result)

    def test_moves_type(self):
        old_path = self._make("2026-01-01-0000", entry_type="reference", title="Entry")
        result = kb_cli.entry_move(self.root, "2026-01-01-0000", new_type="how-to")
        self.assertNotIn("error", result)
        new_path = os.path.join(self.root, result["path"])
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(new_path))
        fm, _body = pc.read_entry(new_path)
        self.assertEqual(fm["type"], "how-to")
        self.assertEqual(fm["title"], "Entry")
        self.assertEqual(self.reindex_calls, 1)

    def test_renames_title(self):
        old_path = self._make("2026-01-01-0000", entry_type="reference", title="Old title")
        result = kb_cli.entry_move(self.root, "2026-01-01-0000", new_title="New title")
        self.assertNotIn("error", result)
        new_path = os.path.join(self.root, result["path"])
        self.assertFalse(os.path.exists(old_path))
        self.assertTrue(os.path.exists(new_path))
        fm, _body = pc.read_entry(new_path)
        self.assertEqual(fm["type"], "reference")
        self.assertEqual(fm["title"], "New title")

    def test_no_op_when_type_and_title_unchanged(self):
        path = self._make("2026-01-01-0000", entry_type="reference", title="Entry")
        result = kb_cli.entry_move(self.root, "2026-01-01-0000", new_type="reference", new_title="Entry")
        self.assertNotIn("error", result)
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.reindex_calls, 0)


if __name__ == "__main__":
    unittest.main()
