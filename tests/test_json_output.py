"""`--json` on the write commands: each prints exactly one parseable JSON value
on stdout on success, and {"error": ...} on stderr (exit 1) on failure -- the
contract the kb skill promises an agent. Runs `main()` against a throwaway repo."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

from pkb_cli import cli as kb_cli  # noqa: E402
from pkb_cli import pkb_common as pc  # noqa: E402


class JsonOutputTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        for d in list(pc.TYPE_DIR.values()) + ["inbox", "journal"]:
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        for patch in (mock.patch.object(pc, "get_repo_root", lambda: self.root),
                      mock.patch.object(pc, "git_autocommit", lambda *a, **k: None),
                      mock.patch.object(kb_cli, "_reindex_fast", lambda root: None),
                      mock.patch("pkb_cli.pkb_entries._reindex_fast", lambda root: None)):
            patch.start()
            self.addCleanup(patch.stop)

    def run_kb(self, *argv):
        """(exit code, stdout, stderr) of `kb <argv>`."""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with mock.patch.object(sys, "argv", ["kb", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = kb_cli.main() or 0
            except SystemExit as e:
                code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def ok(self, *argv):
        code, out, err = self.run_kb(*argv)
        self.assertEqual((code, err), (0, ""), out)
        return json.loads(out)  # exactly one JSON document, nothing else on stdout

    def fails(self, *argv):
        code, out, err = self.run_kb(*argv)
        self.assertEqual((code, out), (1, ""))
        return json.loads(err)["error"]

    def test_new_tag_link_mv_rm(self):
        a = self.ok("new", "how-to", "Alpha", "--tags", "x", "--json")
        self.assertEqual((a["type"], a["title"]), ("how-to", "Alpha"))
        self.assertTrue(os.path.exists(a["path"]))
        b = self.ok("new", "reference", "Beta", "--json")

        self.assertEqual(self.ok("tag", a["id"], "--json"), {"id": a["id"], "tags": ["x"]})
        self.assertEqual(self.ok("tag", a["id"], "add", "y", "--json"),
                         {"id": a["id"], "tags": ["x", "y"], "changed": True})
        self.assertFalse(self.ok("tag", a["id"], "add", "y", "--json")["changed"])

        self.assertEqual(self.ok("link", a["id"], "add", b["id"], "--json"),
                         {"id": a["id"], "links": [b["id"]], "changed": True})
        self.assertEqual(self.ok("links", b["id"], "--json")["backlinks"][0]["id"], a["id"])

        moved = self.ok("mv", a["id"], "--type", "tutorial", "--json")
        self.assertEqual(moved["id"], a["id"])
        self.assertTrue(moved["path"].startswith("tutorials/"))

        self.assertIn("linked from", self.fails("rm", b["id"], "--json"))
        gone = self.ok("rm", b["id"], "--force", "--json")
        self.assertEqual((gone["id"], gone["backlinks_cleared"]), (b["id"], 1))

    def test_errors_are_json_on_stderr(self):
        self.assertIn("no entry", self.fails("tag", "nope", "add", "x", "--json"))
        self.assertIn("no entry", self.fails("rm", "nope", "--json"))
        self.assertIn("no entry", self.fails("mv", "nope", "--title", "t", "--json"))
        self.assertIn("unknown link target", self.fails("new", "how-to", "T", "--links", "nope", "--json"))

    def test_journal_add_and_rollup(self):
        added = self.ok("journal", "2026-03-04", "-m", "hello", "--json")
        self.assertEqual((added["added"], added["date"]), (True, "2026-03-04"))
        self.assertEqual(added["path"], os.path.join("journal", "2026", "03", "2026-03-04.md"))
        self.assertEqual(self.ok("journal", "2026-03-04", "-m", "\n", "--json"), {"added": False})
        rolled = self.ok("journal", "rollup", "2026-03", "--json")
        self.assertEqual((rolled["month"], rolled["entries"]), ("2026-03", 1))
        self.assertIn("no journal entries", self.fails("journal", "rollup", "2020-01", "--json"))

    def test_inbox_list_and_actions(self):
        self.assertEqual(self.ok("inbox", "--json"), [])
        for n, item_id in enumerate(("2026-01-01-0001", "2026-01-01-0002", "2026-01-01-0003")):
            fm = {"id": item_id, "created": pc.now_iso(), "updated": pc.now_iso(), "type": "inbox",
                  "extension": "inbox", "source": "manual", "source_id": None, "tags": [],
                  "links": [], "title": f"Item {n}"}
            pc.write_entry(os.path.join(self.root, "inbox", f"{item_id}.md"), fm, f"# Item {n}\n")
        self.assertEqual([i["id"] for i in self.ok("inbox", "--json")],
                         ["2026-01-01-0001", "2026-01-01-0002", "2026-01-01-0003"])

        promoted = self.ok("inbox", "2026-01-01-0001", "promote", "how-to", "--json")
        self.assertEqual((promoted["action"], promoted["type"]), ("promote", "how-to"))
        self.assertEqual(self.ok("inbox", "2026-01-01-0002", "redirect", "--json")["action"], "redirect")
        self.assertEqual(self.ok("inbox", "2026-01-01-0003", "discard", "--json")["action"], "discard")
        self.assertIn("no inbox item", self.fails("inbox", "nope", "discard", "--json"))


if __name__ == "__main__":
    unittest.main()
