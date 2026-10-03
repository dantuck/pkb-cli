"""Tests for pkb_cli/cli.py's doctor_checks -- the read-only health-check logic
behind `kb doctor`, factored out so kb web's GET /api/admin/status can return
it as data (for the Admin panel's badge/auto-status) instead of parsing
printed output. Only covers the checks that matter to that panel; the rest
of doctor_checks is exercised end-to-end by `kb doctor` itself.
"""
import os
import subprocess
import sys
import tempfile
import unittest


from pkb_cli import pkb_common as pc  # noqa: E402


from pkb_cli import cli as kb_cli  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=True)


class DoctorChecksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        os.makedirs(os.path.join(self.root, ".pkb"), exist_ok=True)
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "test@test.com")
        _git(self.root, "config", "user.name", "test")
        _git(self.root, "commit", "--allow-empty", "-q", "-m", "init")

    def test_returns_ok_and_todo_lists(self):
        ok, todo = kb_cli.doctor_checks(self.root)
        self.assertIsInstance(ok, list)
        self.assertIsInstance(todo, list)

    def test_clean_git_repo_reports_ok(self):
        ok, todo = kb_cli.doctor_checks(self.root)
        self.assertTrue(any("data repo is clean" in line for line in ok))
        self.assertFalse(any("uncommitted change" in line for line in todo))

    def test_dirty_repo_is_flagged(self):
        with open(os.path.join(self.root, "stray.md"), "w") as f:
            f.write("nobody committed this")
        ok, todo = kb_cli.doctor_checks(self.root)
        self.assertTrue(any("uncommitted change" in line for line in todo))
        self.assertFalse(any("data repo is clean" in line for line in ok))

    def test_non_git_repo_is_flagged(self):
        with tempfile.TemporaryDirectory() as non_git_root:
            os.makedirs(os.path.join(non_git_root, ".pkb"), exist_ok=True)
            ok, todo = kb_cli.doctor_checks(non_git_root)
            self.assertTrue(any("not a git repo" in line for line in todo))

    def test_missing_autocommit_excludes_are_flagged(self):
        # A repo that predates auto-commit (or never ran `kb setup`) won't
        # have .git/info/exclude entries yet -- doctor should say so rather
        # than silently relying on the next mutation to self-heal it.
        ok, todo = kb_cli.doctor_checks(self.root)
        self.assertTrue(any("auto-commit excludes missing" in line for line in todo))

        pc._ensure_git_excludes(self.root)
        ok, todo = kb_cli.doctor_checks(self.root)
        self.assertTrue(any("auto-commit excludes" in line and "in place" in line for line in ok))

    def _write_entry(self, rel_path, **fm_overrides):
        fm = {
            "id": "2026-01-01-0000", "created": "2026-01-01T00:00:00", "updated": "2026-01-01T00:00:00",
            "type": "how-to", "extension": None, "source": "manual", "source_id": None,
            "tags": [], "links": [], "title": "t",
        }
        fm.update(fm_overrides)
        path = os.path.join(self.root, rel_path)
        pc.write_entry(path, fm, "body\n")
        return path

    def test_no_promoted_entries_reports_ok(self):
        ok, todo = kb_cli.doctor_checks(self.root)
        self.assertTrue(any("no promoted entries are stale" in line for line in ok))
        self.assertFalse(any("is stale" in line for line in todo))

    def test_promoted_entry_current_with_source_reports_ok(self):
        self._write_entry("sources/memos/2026-01-01-0001.md", id="2026-01-01-0001",
                           type="source", extension="source", source="memos", source_id="101",
                           updated="2026-01-01T00:00:00")
        self._write_entry("how-to/2026-01-01-0000-t.md",
                           source="memos", source_id="101", updated="2026-01-02T00:00:00")
        ok, todo = kb_cli.doctor_checks(self.root)
        self.assertTrue(any("no promoted entries are stale" in line for line in ok))
        self.assertFalse(any("is stale" in line for line in todo))

    def test_promoted_entry_behind_updated_source_is_flagged(self):
        self._write_entry("sources/memos/2026-01-01-0001.md", id="2026-01-01-0001",
                           type="source", extension="source", source="memos", source_id="101",
                           updated="2026-02-01T00:00:00")  # re-synced after promotion
        promoted = self._write_entry("how-to/2026-01-01-0000-t.md",
                                      source="memos", source_id="101", updated="2026-01-02T00:00:00")
        ok, todo = kb_cli.doctor_checks(self.root)
        self.assertTrue(any("is stale" in line and os.path.relpath(promoted, self.root) in line
                             for line in todo))
        self.assertFalse(any("no promoted entries are stale" in line for line in ok))


if __name__ == "__main__":
    unittest.main()


class ConfigProblemsTest(unittest.TestCase):
    def test_clean_config_has_no_problems(self):
        self.assertEqual(pc.config_problems("inbox_triage_days: 7\nsync:\n  memos:\n    inbox_min_length: 100\n"), [])

    def test_flags_unknown_key_missing_colon_and_wrong_type(self):
        problems = pc.config_problems("inbox_triage_dayz: 7\nauto_push: maybe\nbogus line\n")
        text = "\n".join(problems)
        self.assertIn("unknown key `inbox_triage_dayz`", text)
        self.assertIn("`auto_push` should be bool", text)
        self.assertIn("not a `key: value` line", text)

    def test_flags_section_replaced_by_scalar(self):
        self.assertTrue(any("should be a section" in p for p in pc.config_problems("sync: off\n")))
