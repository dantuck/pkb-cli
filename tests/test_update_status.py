"""updater.update_status against real git repos: a clone behind its origin, with and without local edits."""
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from pkb_cli import updater


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=True).stdout.strip()


class UpdateStatusTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.origin = os.path.join(tmp.name, "origin")
        self.clone = os.path.join(tmp.name, "clone")
        os.mkdir(self.origin)
        git(self.origin, "init", "-q", "-b", "main")
        for k, v in (("user.email", "t@t"), ("user.name", "t")):
            git(self.origin, "config", k, v)
        git(self.origin, "commit", "--allow-empty", "-q", "-m", "one")
        subprocess.run(["git", "clone", "-q", self.origin, self.clone], check=True)
        patcher = mock.patch.object(updater, "TOOL_ROOT", self.clone)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_up_to_date_and_clean(self):
        s = updater.update_status()
        self.assertEqual((s["is_git"], s["upstream"], s["behind"], s["dirty"]), (True, "origin/main", 0, False))
        self.assertEqual(s["local"], git(self.clone, "rev-parse", "HEAD"))
        self.assertEqual(s["remote"], s["local"])

    def test_behind_after_origin_advances_and_fetch(self):
        git(self.origin, "commit", "--allow-empty", "-q", "-m", "two")
        git(self.origin, "commit", "--allow-empty", "-q", "-m", "three")
        self.assertEqual(updater.update_status(fetch=False)["behind"], 0)  # not fetched yet
        s = updater.update_status(fetch=True)
        self.assertEqual(s["behind"], 2)
        self.assertEqual(s["remote"], git(self.origin, "rev-parse", "HEAD"))

    def test_dirty_tree(self):
        with open(os.path.join(self.clone, "x.txt"), "w") as f:
            f.write("x")
        self.assertTrue(updater.update_status()["dirty"])

    def test_no_upstream(self):
        git(self.clone, "branch", "--unset-upstream")
        s = updater.update_status()
        self.assertEqual((s["is_git"], s["upstream"], s["behind"]), (True, None, None))


if __name__ == "__main__":
    unittest.main()
