"""Tests for `kb setup` bootstrapping a central kb at ~/.pkb when no data repo
exists anywhere -- previously this just printed a todo telling the user to
`mkdir -p ~/.pkb/.pkb && git init` by hand, which made every brand-new
machine's first `kb setup` (including the one install.sh runs automatically)
a no-op unless you already had a data repo.
"""
import argparse
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock


from pkb_cli import pkb_common as pc  # noqa: E402


from pkb_cli import cli as kb_cli  # noqa: E402


@unittest.skipUnless(shutil.which("git"), "git required for setup bootstrap")
class SetupBootstrapTest(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.TemporaryDirectory()
        self._cwd = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.addCleanup(self._cwd.cleanup)

        self._orig_home = os.environ.get("HOME")
        self._orig_cwd = os.getcwd()
        self._orig_repo_root = pc.REPO_ROOT
        os.environ["HOME"] = self._home.name
        os.chdir(self._cwd.name)
        pc.REPO_ROOT = None

        def restore():
            os.chdir(self._orig_cwd)
            if self._orig_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = self._orig_home
            pc.REPO_ROOT = self._orig_repo_root

        self.addCleanup(restore)

    def _run_setup(self):
        # cmd_setup's optional-deps step is guided but unconditional with --yes,
        # so a real `brew` on the test machine would actually get shelled out
        # to. Hide it so this test can't touch the real machine.
        args = argparse.Namespace(yes=True)
        buf = io.StringIO()
        real_which = shutil.which
        with contextlib.redirect_stdout(buf), \
                mock.patch.object(kb_cli.shutil, "which", side_effect=lambda name: None if name == "brew" else real_which(name)):
            kb_cli.cmd_setup(args)
        return buf.getvalue()

    def test_bootstraps_central_kb_when_none_found(self):
        central = os.path.join(self._home.name, ".pkb")
        self.assertFalse(os.path.isdir(central))

        output = self._run_setup()

        self.assertTrue(os.path.isdir(os.path.join(central, ".pkb")))
        self.assertTrue(os.path.isdir(os.path.join(central, ".git")))
        self.assertIn("created central kb", output)

    def test_second_run_does_not_reinit_git(self):
        self._run_setup()
        central = os.path.join(self._home.name, ".pkb")
        git_dir = os.path.join(central, ".git")
        before = os.stat(git_dir).st_mtime_ns

        pc.REPO_ROOT = None
        self._run_setup()

        self.assertEqual(before, os.stat(git_dir).st_mtime_ns)

    def test_existing_data_repo_elsewhere_is_not_overridden(self):
        # A repo found by walking up from cwd should win over bootstrapping a
        # central kb, same as pc.find_repo_root's normal precedence.
        os.makedirs(os.path.join(self._cwd.name, ".pkb"), exist_ok=True)
        pc.REPO_ROOT = None

        self._run_setup()

        central = os.path.join(self._home.name, ".pkb")
        self.assertFalse(os.path.isdir(central))


if __name__ == "__main__":
    unittest.main()
