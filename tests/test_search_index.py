"""Keyword search and the incremental FTS indexer: ranking filters, prefix matching,
fallback for queries FTS5 rejects, the --json error contract, pruning, and recovery
from a corrupt index."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from pkb_cli import cli as kb_cli
from pkb_cli import index_fts
from pkb_cli import pkb_common as pc


def write_entry(root, type_, id_, title, body="", tags=()):
    d = os.path.join(root, pc.TYPE_DIR[type_])
    os.makedirs(d, exist_ok=True)
    fm = "\n".join(["---", f"id: {id_}", f"type: {type_}", f"title: {title}",
                    f"tags: [{', '.join(tags)}]", "created: 2026-01-01T00:00:00Z",
                    "updated: 2026-01-01T00:00:00Z", "---", "", f"# {title}", "", body, ""])
    with open(os.path.join(d, f"{id_}.md"), "w", encoding="utf-8") as f:
        f.write(fm)


class SearchIndexTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        os.makedirs(os.path.join(self.root, ".pkb"))
        write_entry(self.root, "how-to", "2026-01-01-0001", "Bake sourdough bread", "flour and water", ["baking"])
        write_entry(self.root, "reference", "2026-01-01-0002", "Darren contact sheet", "phone numbers", ["people"])
        patch = mock.patch.object(pc, "get_repo_root", lambda: self.root)
        patch.start()
        self.addCleanup(patch.stop)
        mock.patch.object(kb_cli.semantic, "active_model", lambda root: None).start()
        self.addCleanup(mock.patch.stopall)
        index_fts.index_repo(self.root, quiet=True)

    def search(self, *a, **k):
        return kb_cli.search_entries(self.root, *a, mode="keyword", **k)

    def test_prefix_and_filters(self):
        self.assertEqual([r[0] for r in self.search("dar")], ["2026-01-01-0002"])
        self.assertEqual(len(self.search("flour", entry_type="how-to")), 1)
        self.assertEqual(self.search("flour", entry_type="reference"), [])
        self.assertEqual(len(self.search("bread", tag="baking")), 1)
        self.assertEqual(self.search("bread", tag="people"), [])

    def test_invalid_fts_query_falls_back_to_plain_terms(self):
        for q in ('bread"', "bread -", "bread ("):
            rows = self.search(q)
            self.assertEqual([r[0] for r in rows], ["2026-01-01-0001"], q)

    def test_unsearchable_query_still_raises(self):
        import sqlite3
        with self.assertRaises(sqlite3.OperationalError):
            self.search('"')

    def test_json_error_goes_to_stderr_as_json(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["kb", "search", '"', "--json", "--keyword"]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                kb_cli.main()
        code = cm.exception.code
        self.assertEqual((code, out.getvalue()), (1, ""))
        self.assertIn("search error", json.loads(err.getvalue())["error"])

    def test_incremental_index_updates_and_prunes(self):
        os.remove(os.path.join(self.root, "how-to", "2026-01-01-0001.md"))
        write_entry(self.root, "explanation", "2026-01-01-0003", "Why tea", "leaves")
        index_fts.index_repo(self.root, quiet=True)
        self.assertEqual(self.search("bread"), [])
        self.assertEqual(len(self.search("leaves")), 1)

    def test_corrupt_index_is_recreated(self):
        db = os.path.join(self.root, ".pkb", "fts.db")
        with open(db, "wb") as f:
            f.write(b"this is not a sqlite database" * 100)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            index_fts.index_repo(self.root, quiet=True)
        self.assertIn("corrupt", err.getvalue())
        self.assertEqual(len(self.search("bread")), 1)


if __name__ == "__main__":
    unittest.main()
