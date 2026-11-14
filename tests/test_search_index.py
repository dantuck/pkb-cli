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

import fakes
from pkb_cli import cli as kb_cli
from pkb_cli import search
from pkb_cli import index_fts
from pkb_cli import pkb_common as pc


def write_entry(root, type_, id_, title, body="", tags=()):
    return fakes.write_test_entry(root, f"{pc.TYPE_DIR[type_]}/{id_}.md", body=f"# {title}\n\n{body}\n",
                                  id=id_, type=type_, title=title, tags=list(tags))


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
        return search.search_entries(self.root, *a, mode="keyword", **k)

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

    def corrupt(self):
        with open(os.path.join(self.root, ".pkb", "fts.db"), "wb") as f:
            f.write(b"this is not a sqlite database" * 100)

    def test_search_on_a_corrupt_index_says_to_rebuild_it(self):
        self.corrupt()
        with self.assertRaises(index_fts.IndexCorrupt) as cm:
            self.search("bread")
        self.assertIn("kb index", str(cm.exception))

    def test_kb_index_rebuilds_a_corrupt_index(self):
        self.corrupt()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            index_fts.index_repo(self.root, quiet=True)
        self.assertIn("corrupt", err.getvalue())
        self.assertEqual(len(self.search("bread")), 1)


if __name__ == "__main__":
    unittest.main()
