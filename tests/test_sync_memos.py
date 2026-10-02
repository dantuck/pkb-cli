"""Tests for scripts/sync_memos.py's pure/offline logic: id and filename
handling, attachment markdown, and write_memo's add/update/unchanged paths.
Network access (attachment downloads) is stubbed out."""
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

import pkb_common as pc  # noqa: E402
import sync_memos  # noqa: E402


class HelpersTest(unittest.TestCase):
    def test_memo_source_id(self):
        self.assertEqual(sync_memos.memo_source_id({"name": "memos/101"}), "101")
        self.assertIsNone(sync_memos.memo_source_id({}))

    def test_safe_attachment_filename_blocks_traversal(self):
        name = sync_memos.safe_attachment_filename({"name": "attachments/7", "filename": "../../etc/pass wd"})
        self.assertEqual(name, "7-pass_wd")
        self.assertNotIn("/", name)

    def test_safe_attachment_filename_falls_back_when_name_collapses(self):
        self.assertEqual(sync_memos.safe_attachment_filename({"name": "attachments/9", "filename": "../.."}), "9-attachment")

    def test_attachments_markdown_inlines_media_and_links_the_rest(self):
        md = sync_memos.attachments_markdown([
            ("/a.png", "a.png", "image/png"),
            ("/b.mp4", "b.mp4", "video/mp4"),
            ("/c.pdf", "c.pdf", "application/pdf"),
        ])
        self.assertEqual(md.split("\n"), ["![a.png](/a.png)", "![b.mp4](/b.mp4)", "[c.pdf](/c.pdf)"])


class WriteMemoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        for d in ("sources/memos", "inbox"):
            os.makedirs(os.path.join(self.root, d))
        self.memo = {"name": "memos/101", "content": "Hello world\nmore", "tags": ["x"],
                     "createTime": "2026-01-01T00:00:00Z", "updateTime": "2026-01-01T00:00:00Z"}

    def write(self, memo=None, existing=None, threshold=None):
        return sync_memos.write_memo(self.root, memo or self.memo, existing or {}, set(), threshold,
                                     "https://memos.example", "tok")

    def test_added_mirror_is_read_only_with_permalink(self):
        path, status = self.write()
        self.assertEqual(status, "added")
        self.assertFalse(os.access(path, os.W_OK))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o444)
        fm, body = pc.read_entry(path)
        self.assertEqual(fm["source_id"], "101")
        self.assertEqual(fm["title"], "Hello world")
        self.assertTrue(body.rstrip().endswith("[Memo](https://memos.example/memos/101)"))

    def test_rerun_is_unchanged_and_edit_is_updated(self):
        path, _ = self.write()
        self.assertEqual(self.write(existing={"101": path})[1], "unchanged")
        edited = dict(self.memo, content="Changed", updateTime="2026-02-01T00:00:00Z")
        path2, status = self.write(memo=edited, existing={"101": path})
        self.assertEqual((path2, status), (path, "updated"))
        self.assertEqual(pc.read_entry(path)[0]["title"], "Changed")

    def test_inbox_stub_only_for_long_memos(self):
        self.write(threshold=1000)
        self.assertEqual(os.listdir(os.path.join(self.root, "inbox")), [])
        self.write(threshold=5)
        self.assertEqual(len(os.listdir(os.path.join(self.root, "inbox"))), 1)

    def test_attachments_are_downloaded_once(self):
        memo = dict(self.memo, attachments=[{"name": "attachments/5", "filename": "p.png", "type": "image/png"}])
        with mock.patch.object(sync_memos, "fetch_attachment_bytes", return_value=b"PNG") as fetch:
            path, _ = self.write(memo=memo)
            self.write(memo=memo, existing={"101": path})
        self.assertEqual(fetch.call_count, 1)
        self.assertIn("![5-p.png](/sources/memos/assets/", pc.read_entry(path)[1])


if __name__ == "__main__":
    unittest.main()
