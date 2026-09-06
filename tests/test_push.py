"""Tests for the push flow: pkb_common.git_push, scripts/kb's push_status, and
auto_push wiring through git_autocommit. Uses a local bare repo as the
"remote" so nothing here touches the network.
"""
import importlib.machinery
import importlib.util
import os
import subprocess
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


def _git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=True)


class PushFlowTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

        self.remote = os.path.join(base, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)

        self.root = os.path.join(base, "repo")
        os.makedirs(os.path.join(self.root, ".pkb"))
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "test@test.com")
        _git(self.root, "config", "user.name", "test")
        _git(self.root, "remote", "add", "origin", self.remote)
        _git(self.root, "commit", "--allow-empty", "-q", "-m", "init")
        _git(self.root, "push", "-q", "-u", "origin", "HEAD:main")

    def test_git_push_fails_without_upstream(self):
        no_upstream_root = os.path.join(os.path.dirname(self.root), "no-upstream")
        os.makedirs(no_upstream_root)
        _git(no_upstream_root, "init", "-q")
        ok, message = pc.git_push(no_upstream_root)
        self.assertFalse(ok)
        self.assertIn("no upstream", message)

    def test_push_status_reports_up_to_date(self):
        status = kb_cli.push_status(self.root)
        self.assertTrue(status["is_git"])
        self.assertEqual(status["upstream"], "origin/main")
        self.assertEqual(status["ahead"], 0)
        self.assertEqual(status["behind"], 0)

    def test_push_status_counts_ahead_commits(self):
        with open(os.path.join(self.root, "note.md"), "w") as f:
            f.write("hello")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "add note")

        status = kb_cli.push_status(self.root)
        self.assertEqual(status["ahead"], 1)

    def test_git_push_publishes_local_commits(self):
        with open(os.path.join(self.root, "note.md"), "w") as f:
            f.write("hello")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "add note")

        ok, message = pc.git_push(self.root)
        self.assertTrue(ok)
        self.assertEqual(message, "origin/main")
        self.assertEqual(kb_cli.push_status(self.root)["ahead"], 0)

    def test_autocommit_does_not_push_by_default(self):
        with open(os.path.join(self.root, "note.md"), "w") as f:
            f.write("hello")
        pc.git_autocommit(self.root, "add note")
        self.assertEqual(kb_cli.push_status(self.root)["ahead"], 1)

    def test_autocommit_pushes_when_auto_push_enabled(self):
        with open(os.path.join(self.root, ".pkb", "config.yml"), "w") as f:
            f.write("auto_push: true\n")
        with open(os.path.join(self.root, "note.md"), "w") as f:
            f.write("hello")
        pc.git_autocommit(self.root, "add note")
        self.assertEqual(kb_cli.push_status(self.root)["ahead"], 0)


if __name__ == "__main__":
    unittest.main()
