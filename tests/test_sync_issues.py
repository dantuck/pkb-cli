"""The issue-style sync sources (github, gitlab, beads) share pc.write_source_mirror and
pc.mirror_items: first sight mirrors + stubs, repeats are no-ops, edits update in place,
and the cursor only advances when every item was written."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from pkb_cli import pkb_common as pc
from pkb_cli import sync_github, sync_gitlab


class SyncIssuesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        os.makedirs(os.path.join(self.root, ".pkb"))

    def count(self, d):
        p = os.path.join(self.root, d)
        return len(os.listdir(p)) if os.path.isdir(p) else 0

    def test_github_lifecycle(self):
        issue = {"number": 7, "title": "Bug", "body": "it breaks", "html_url": "https://x/7",
                 "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}
        existing = {}
        path, outcome = sync_github.write_issue(self.root, issue, existing, set(), True)
        self.assertEqual(outcome, "added")
        fm, body = pc.read_entry(path)
        self.assertEqual((fm["source"], fm["source_id"], fm["type"]), ("github", "7", "source"))
        self.assertIn("[GitHub issue](https://x/7)", body)
        self.assertEqual((self.count("sources/github"), self.count("inbox")), (1, 1))
        existing["7"] = path
        self.assertEqual(sync_github.write_issue(self.root, issue, existing, set(), True)[1], "unchanged")
        edited = {**issue, "title": "Bug!", "updated_at": "2026-02-01T00:00:00Z"}
        self.assertEqual(sync_github.write_issue(self.root, edited, existing, set(), True)[1], "updated")
        self.assertEqual((self.count("sources/github"), self.count("inbox")), (1, 1))

    def test_gitlab_uses_iid_and_its_own_label(self):
        issue = {"iid": 3, "title": "Task", "description": "do it", "web_url": "https://gl/3",
                 "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"}
        path, _ = sync_gitlab.write_issue(self.root, issue, {}, set(), True)
        self.assertEqual(pc.read_entry(path)[0]["source_id"], "3")
        stub = os.path.join(self.root, "inbox", os.listdir(os.path.join(self.root, "inbox"))[0])
        self.assertIn("Synced from GitLab", pc.read_entry(stub)[1])

    def test_github_fetch_drops_pull_requests(self):
        out = json.dumps([{"number": 1}, {"number": 2, "pull_request": {}}])
        with mock.patch.object(sync_github.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0, out, "")):
            self.assertEqual(sync_github.fetch_issues("o/r", None), [{"number": 1}])

    def test_mirror_items_advances_cursor_only_on_success(self):
        items = [{"n": 1, "updated_at": "2026-01-01T00:00:00Z"}, {"n": 2, "updated_at": "2026-01-02T00:00:00Z"}]

        def ok(item, paths, ids):
            return f"/p/{item['n']}", "added"

        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(pc, "save_cursors") as save:
            pc.mirror_items(self.root, "thing", items, lambda i: i["n"], ok, "last", None)
        self.assertEqual(save.call_args[0][0]["thing"]["last"], "2026-01-02T00:00:00Z")
        self.assertIn("2 new", out.getvalue())

        def boom(item, paths, ids):
            raise OSError("disk full")

        with mock.patch.object(pc, "save_cursors") as save, self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                pc.mirror_items(self.root, "thing", items, lambda i: i["n"], boom, "last", None)
        save.assert_not_called()
        self.assertIn("cursor not advanced", err.getvalue())

    def test_mirror_items_with_nothing_new_leaves_cursor_alone(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, mock.patch.object(pc, "save_cursors") as save:
            pc.mirror_items(self.root, "thing", [], lambda i: i, None, "last", "t")
        save.assert_not_called()
        self.assertIn("no new/updated issues", out.getvalue())


if __name__ == "__main__":
    unittest.main()
