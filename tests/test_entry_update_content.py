"""Tests for scripts/kb's entry_update_content -- the write path behind kb
web's content-edit panel. Loads scripts/kb the same way kb_web.py does (it has
no .py suffix, so it isn't import-able by name), against a throwaway repo dir
so nothing here touches a real .pkb.
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


class EntryUpdateContentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        # Count reindex calls instead of actually reindexing -- the reindex
        # side effect isn't what this test is about, and the temp repo has no
        # .pkb/fts.db for it to write to anyway. entry_update_content reindexes
        # in-process via _reindex_fast (not the run_script/subprocess path
        # other kb commands use), so that's what gets stubbed here.
        self.reindex_calls = 0

        def fake_reindex_fast(root):
            self.reindex_calls += 1

        self._orig_reindex_fast = kb_cli._reindex_fast
        kb_cli._reindex_fast = fake_reindex_fast
        self.addCleanup(lambda: setattr(kb_cli, "_reindex_fast", self._orig_reindex_fast))

        self.entry_id = "2026-01-01-0000"
        self.path = os.path.join(self.root, "reference", "2026-01-01-0000-example.md")
        fm = {
            "id": self.entry_id,
            "created": "2026-01-01T00:00:00-06:00",
            "updated": "2026-01-01T00:00:00-06:00",
            "type": "reference",
            "extension": None,
            "source": "manual",
            "source_id": None,
            "tags": [],
            "links": [],
            "title": "Original title",
        }
        # Written directly (not via pc.write_entry) so the on-disk body is
        # exactly "Original body.\n" with no separator quirks -- write_entry
        # always inserts a normalizing blank line between frontmatter and
        # body, which would make the fixture's stored body not match what we
        # pass in below.
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(pc.dump_frontmatter(fm))
            f.write("Original body.\n")

    def _read(self):
        return pc.read_entry(self.path)

    def test_unknown_entry_returns_none(self):
        result = kb_cli.entry_update_content(self.root, "0000-00-00-0000", title="x")
        self.assertIsNone(result)
        self.assertEqual(self.reindex_calls, 0)

    def test_updates_title_only(self):
        result = kb_cli.entry_update_content(self.root, self.entry_id, title="New title")
        self.assertEqual(result["title"], "New title")
        self.assertEqual(result["body"], "Original body.\n")

        fm, body = self._read()
        self.assertEqual(fm["title"], "New title")
        self.assertEqual(body.strip(), "Original body.")
        self.assertNotEqual(fm["updated"], "2026-01-01T00:00:00-06:00")
        self.assertEqual(self.reindex_calls, 1)

    def test_updates_body_only(self):
        result = kb_cli.entry_update_content(self.root, self.entry_id, body="New body.\n")
        self.assertEqual(result["title"], "Original title")
        self.assertEqual(result["body"], "New body.\n")

        fm, body = self._read()
        self.assertEqual(fm["title"], "Original title")
        self.assertEqual(body.strip(), "New body.")
        self.assertEqual(self.reindex_calls, 1)

    def test_updates_title_and_body(self):
        result = kb_cli.entry_update_content(self.root, self.entry_id, title="New title", body="New body.\n")
        self.assertEqual(result, {"title": "New title", "body": "New body.\n"})

        fm, body = self._read()
        self.assertEqual(fm["title"], "New title")
        self.assertEqual(body.strip(), "New body.")
        self.assertEqual(self.reindex_calls, 1)

    def test_no_change_is_a_noop(self):
        result = kb_cli.entry_update_content(
            self.root, self.entry_id, title="Original title", body="Original body.\n"
        )
        self.assertEqual(result, {"title": "Original title", "body": "Original body.\n"})

        fm, _ = self._read()
        self.assertEqual(fm["updated"], "2026-01-01T00:00:00-06:00")
        self.assertEqual(self.reindex_calls, 0)

    def test_no_args_is_a_noop(self):
        result = kb_cli.entry_update_content(self.root, self.entry_id)
        self.assertEqual(result, {"title": "Original title", "body": "Original body.\n"})
        self.assertEqual(self.reindex_calls, 0)


if __name__ == "__main__":
    unittest.main()
