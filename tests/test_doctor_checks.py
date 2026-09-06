"""Tests for scripts/kb's doctor_checks -- the read-only health-check logic
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

SCRIPT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

import pkb_common as pc  # noqa: E402


def _load_kb_cli():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("kb_cli", os.path.join(SCRIPT_DIR, "kb"))
    spec = importlib.util.spec_from_loader("kb_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


kb_cli = _load_kb_cli()


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


if __name__ == "__main__":
    unittest.main()
