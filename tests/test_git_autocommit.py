"""Tests for pkb_common.git_autocommit -- the auto-commit-on-every-mutation
helper shared by entry_new/entry_set_tags/entry_set_links/entry_update_content/
journal_append/inbox actions/cmd_sync, so .pkb content is never more than one
action away from being in git history without anyone remembering to commit.
"""
import os
import subprocess
import sys
import tempfile
import threading
import unittest

SCRIPT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

import pkb_common as pc  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, check=True)


class GitAutocommitTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _init_repo(self):
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "test@test.com")
        _git(self.root, "config", "user.name", "test")
        _git(self.root, "commit", "--allow-empty", "-q", "-m", "init")

    def test_noop_when_not_a_git_repo(self):
        with open(os.path.join(self.root, "file.md"), "w") as f:
            f.write("content")
        pc.git_autocommit(self.root, "should not crash")  # must not raise
        self.assertFalse(os.path.isdir(os.path.join(self.root, ".git")))

    def test_noop_when_nothing_changed(self):
        self._init_repo()
        before = _git(self.root, "rev-parse", "HEAD").stdout
        pc.git_autocommit(self.root, "nothing to commit")
        after = _git(self.root, "rev-parse", "HEAD").stdout
        self.assertEqual(before, after)

    def test_commits_new_and_modified_files(self):
        self._init_repo()
        with open(os.path.join(self.root, "note.md"), "w") as f:
            f.write("hello")
        pc.git_autocommit(self.root, "add note")

        log = _git(self.root, "log", "--format=%s", "-1").stdout.strip()
        self.assertEqual(log, "add note")
        self.assertEqual(_git(self.root, "status", "--porcelain").stdout, "")

    def test_excludes_fts_db_and_lock_files(self):
        self._init_repo()
        os.makedirs(os.path.join(self.root, ".pkb"), exist_ok=True)
        with open(os.path.join(self.root, ".pkb", "fts.db"), "w") as f:
            f.write("binary-ish index data")
        with open(os.path.join(self.root, "entry.md.lock"), "w") as f:
            f.write("")
        with open(os.path.join(self.root, "entry.md"), "w") as f:
            f.write("# real content")

        pc.git_autocommit(self.root, "add entry")

        tracked = _git(self.root, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
        self.assertIn("entry.md", tracked)
        self.assertNotIn(".pkb/fts.db", tracked)
        self.assertNotIn("entry.md.lock", tracked)

    def test_ensures_excludes_even_without_setup(self):
        self._init_repo()
        with open(os.path.join(self.root, "note.md"), "w") as f:
            f.write("hello")
        pc.git_autocommit(self.root, "add note")

        exclude_path = os.path.join(self.root, ".git", "info", "exclude")
        with open(exclude_path) as f:
            content = f.read()
        for pattern in pc.AUTOCOMMIT_EXCLUDES:
            self.assertIn(pattern, content)

    def test_serializes_concurrent_calls(self):
        # Regression test: kb web serves requests on separate threads, so two
        # mutations to two different entries can call git_autocommit at the
        # same time. Without the repo-level lock, two concurrent `git add -A`
        # + `git commit` sequences can step on the same .git/index.lock --
        # one `git commit` fails, silently (the subprocess return code isn't
        # checked), leaving that write on disk but never committed.
        #
        # A real collision between two independent git subprocesses is timing
        # -sensitive and won't reliably reproduce on a fast filesystem, so
        # verify the actual guarantee directly instead: stall the *first* git
        # subprocess call a call makes (the `git status --porcelain` right
        # after the lock is acquired) and confirm a second call never even
        # reaches its own first git call -- not just its commit -- while the
        # first is stalled. (Blocking every "commit" call indiscriminately
        # would falsely "pass" this test even with no lock at all: the second
        # call would race in, reach its own commit, and get stuck on the same
        # patched hook instead of on the real flock.)
        self._init_repo()
        with open(os.path.join(self.root, "a.md"), "w") as f:
            f.write("a")
        with open(os.path.join(self.root, "b.md"), "w") as f:
            f.write("b")

        orig_run = subprocess.run
        call_count = [0]
        count_lock = threading.Lock()
        first_status_started = threading.Event()
        release_first_status = threading.Event()

        def patched_run(cmd, *args, **kwargs):
            if "status" in cmd and "--porcelain" in cmd:
                with count_lock:
                    is_first = call_count[0] == 0
                    call_count[0] += 1
                if is_first:
                    first_status_started.set()
                    release_first_status.wait(timeout=2)
            return orig_run(cmd, *args, **kwargs)

        pc.subprocess.run = patched_run
        self.addCleanup(lambda: setattr(pc.subprocess, "run", orig_run))

        t1 = threading.Thread(target=pc.git_autocommit, args=(self.root, "commit a"))
        t1.start()
        self.assertTrue(first_status_started.wait(timeout=2), "first call never reached git status")

        t2 = threading.Thread(target=pc.git_autocommit, args=(self.root, "commit b"))
        t2.start()
        t2.join(timeout=0.3)
        self.assertTrue(t2.is_alive(), "second call didn't block on the lock")
        # The real assertion: t2 hasn't even made its own `git status` call
        # yet, because it's still waiting to acquire the lock t1 holds --
        # not because it raced in and got caught by the same patched hook.
        self.assertEqual(call_count[0], 1, "second call reached git status before the first released the lock")

        release_first_status.set()
        t1.join(timeout=2)
        t2.join(timeout=2)

        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertEqual(call_count[0], 2)
        self.assertEqual(_git(self.root, "status", "--porcelain").stdout, "")
        tracked = set(_git(self.root, "ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines())
        self.assertIn("a.md", tracked)
        self.assertIn("b.md", tracked)


if __name__ == "__main__":
    unittest.main()
