"""Journal helpers: appending (single line vs block), listing newest-first, month math, on-this-day."""
import os
import tempfile
import unittest
from datetime import date
from unittest import mock

from pkb_cli import journal
from pkb_cli import pkb_common as pc


class JournalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        os.makedirs(os.path.join(self.root, ".pkb"))
        mock.patch.object(journal.pkb_entries, "_reindex_fast", lambda root: None).start()
        mock.patch.object(pc, "git_autocommit", lambda *a, **k: None).start()
        self.addCleanup(mock.patch.stopall)

    def touch(self, d):
        journal._ensure_journal_entry(self.root, d)

    def test_append_single_line_is_a_bullet_and_creates_the_entry(self):
        res = journal.journal_append(self.root, date(2026, 3, 5), "fixed the flaky test")
        self.assertEqual(res["path"], os.path.join("journal", "2026", "03", "2026-03-05.md"))
        _, body = pc.read_entry(os.path.join(self.root, res["path"]))
        self.assertIn(f"- {res['time']}  fixed the flaky test", body)

    def test_append_multiline_is_a_heading_block_and_appends(self):
        journal.journal_append(self.root, date(2026, 3, 5), "one")
        res = journal.journal_append(self.root, date(2026, 3, 5), "line a\nline b\n")
        _, body = pc.read_entry(os.path.join(self.root, res["path"]))
        self.assertIn("one", body)
        self.assertIn(f"## {res['time']}\n\nline a\nline b", body)

    def test_append_empty_writes_nothing(self):
        self.assertIsNone(journal.journal_append(self.root, date(2026, 3, 5), "\n\n"))
        self.assertFalse(os.path.exists(os.path.join(self.root, "journal")))

    def test_list_is_newest_first_and_respects_limit(self):
        for d in (date(2025, 12, 31), date(2026, 1, 2), date(2026, 1, 15)):
            self.touch(d)
        self.assertEqual([e[0] for e in journal.list_journal_entries(self.root)],
                         ["2026-01-15", "2026-01-02", "2025-12-31"])
        self.assertEqual([e[0] for e in journal.list_journal_entries(self.root, limit=2)],
                         ["2026-01-15", "2026-01-02"])
        self.assertEqual(journal.list_journal_entries(os.path.join(self.root, "nope")), [])

    def test_shift_months_clamps_and_crosses_years(self):
        self.assertEqual(journal._shift_months(date(2026, 8, 31), -1), date(2026, 7, 31))
        self.assertEqual(journal._shift_months(date(2026, 3, 31), -1), date(2026, 2, 28))
        self.assertEqual(journal._shift_months(date(2026, 1, 15), -1), date(2025, 12, 15))
        self.assertEqual(journal._shift_months(date(2026, 11, 30), 3), date(2027, 2, 28))

    def test_on_this_day_only_lists_offsets_with_entries(self):
        target = date(2026, 6, 15)
        self.touch(date(2026, 6, 8))   # 1 week ago
        self.touch(date(2024, 6, 15))  # 2 years ago
        self.touch(date(2026, 6, 10))  # not an offset
        labels = [label for label, _d, _p in journal._on_this_day(self.root, target)]
        self.assertEqual(labels[0], "1 week ago")
        self.assertEqual(len(labels), 2)

    def test_looks_like_date(self):
        self.assertTrue(journal._looks_like_date("2026-02-28"))
        self.assertFalse(journal._looks_like_date("2026-02-30"))
        self.assertFalse(journal._looks_like_date("yesterday"))


if __name__ == "__main__":
    unittest.main()
