"""updater.update_status against real git repos: a clone behind its origin, with and without local edits."""
import json
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
        patcher = mock.patch.object(updater, "_checkout_root", return_value=self.clone)
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



class CheckoutRootTest(unittest.TestCase):
    """_checkout_root reads how kb was installed from package metadata (PEP 610 direct_url.json)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.checkout = os.path.join(tmp.name, "my checkout")  # a space: the URL is percent-encoded
        os.makedirs(os.path.join(self.checkout, ".git"))

    def _dist(self, direct_url):
        dist = mock.Mock()
        dist.read_text.return_value = None if direct_url is None else json.dumps(direct_url)
        return mock.patch.object(updater.metadata, "distribution", return_value=dist)

    def test_editable_install_points_at_its_checkout(self):
        url = "file://" + self.checkout.replace(" ", "%20")
        with self._dist({"url": url, "dir_info": {"editable": True}}):
            self.assertEqual(updater._checkout_root(), self.checkout)

    def test_editable_install_whose_checkout_has_no_git_dir_is_a_release(self):
        os.rmdir(os.path.join(self.checkout, ".git"))
        with self._dist({"url": "file://" + self.checkout, "dir_info": {"editable": True}}):
            self.assertIsNone(updater._checkout_root())

    def test_vcs_wheel_and_unrecorded_installs_are_releases(self):
        for direct_url in ({"url": "https://github.com/x/pkb-cli", "vcs_info": {"vcs": "git", "commit_id": "a"}},
                           {"url": "file://" + self.checkout, "dir_info": {}},  # non-editable local install
                           None):
            with self._dist(direct_url):
                self.assertIsNone(updater._checkout_root(), direct_url)

    def test_uninstalled_source_tree_falls_back_to_whether_it_is_a_checkout(self):
        with mock.patch.object(updater.metadata, "distribution", side_effect=updater.metadata.PackageNotFoundError), \
                mock.patch.object(updater, "_SOURCE_TREE", self.checkout):
            self.assertEqual(updater._checkout_root(), self.checkout)
            with mock.patch.object(updater.os.path, "isdir", return_value=False):
                self.assertIsNone(updater._checkout_root())


if __name__ == "__main__":
    unittest.main()
