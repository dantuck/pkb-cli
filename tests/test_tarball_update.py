"""Tests for scripts/kb's tarball-based self-update path (used when TOOL_ROOT
isn't a git checkout -- the common case for an install.sh-installed kb). Loads
scripts/kb the same way kb_web.py does (it has no .py suffix, so it isn't
import-able by name). Network calls (_github_request/_fetch_remote_sha) are
stubbed throughout -- nothing here touches the real GitHub API.
"""
import argparse
import importlib.machinery
import importlib.util
import io
import os
import sys
import tarfile
import tempfile
import unittest

SCRIPT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)


def _load_kb_cli():
    loader = importlib.machinery.SourceFileLoader("kb_cli", os.path.join(SCRIPT_DIR, "kb"))
    spec = importlib.util.spec_from_loader("kb_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


kb_cli = _load_kb_cli()


def _make_tarball(top_dir, files):
    """Build an in-memory gzipped tarball with a single top-level directory
    (mirroring a GitHub codeload archive), containing `files` ({relpath: bytes}).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        top_info = tarfile.TarInfo(name=top_dir)
        top_info.type = tarfile.DIRTYPE
        tar.addfile(top_info)
        for rel, content in files.items():
            info = tarfile.TarInfo(name=f"{top_dir}/{rel}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class VersionMarkerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_version_file = kb_cli.VERSION_FILE
        kb_cli.VERSION_FILE = os.path.join(self._tmp.name, ".pkb-cli-version")
        self.addCleanup(lambda: setattr(kb_cli, "VERSION_FILE", self._orig_version_file))

    def test_missing_marker_reads_as_none(self):
        self.assertIsNone(kb_cli._read_local_version())

    def test_empty_marker_reads_as_empty_string_not_none(self):
        with open(kb_cli.VERSION_FILE, "w") as f:
            pass  # empty file, e.g. an interrupted write
        self.assertEqual(kb_cli._read_local_version(), "")
        self.assertIsNotNone(kb_cli._read_local_version())

    def test_write_then_read_round_trips(self):
        kb_cli._write_local_version("abc123")
        self.assertEqual(kb_cli._read_local_version(), "abc123")


class WriteFileAtomicTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_writes_content_and_no_leftover_temp_file(self):
        path = os.path.join(self._tmp.name, "sub", "out.txt")
        kb_cli._write_file_atomic(path, io.BytesIO(b"hello"))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"hello")
        self.assertEqual(os.listdir(os.path.dirname(path)), ["out.txt"])

    def test_existing_file_untouched_if_copy_fails(self):
        path = os.path.join(self._tmp.name, "out.txt")
        with open(path, "wb") as f:
            f.write(b"original")

        class ExplodingReader:
            def read(self, *a):
                raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            kb_cli._write_file_atomic(path, ExplodingReader())

        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"original")
        self.assertEqual(os.listdir(self._tmp.name), ["out.txt"])

    def test_sets_executable_mode(self):
        path = os.path.join(self._tmp.name, "script")
        kb_cli._write_file_atomic(path, io.BytesIO(b"#!/bin/sh\n"), mode=0o755)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o755)


class ApplyTarballUpdateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_tool_root = kb_cli.TOOL_ROOT
        kb_cli.TOOL_ROOT = self._tmp.name
        self.addCleanup(lambda: setattr(kb_cli, "TOOL_ROOT", self._orig_tool_root))

        self._orig_request = kb_cli._github_request
        self.addCleanup(lambda: setattr(kb_cli, "_github_request", self._orig_request))

    def test_extracts_files_preserving_relative_paths(self):
        tarball = _make_tarball("pkb-cli-deadbeef", {
            "README.md": b"hello",
            "scripts/kb": b"#!/usr/bin/env python3\n",
        })
        kb_cli._github_request = lambda url: tarball

        kb_cli._apply_tarball_update("deadbeef")

        with open(os.path.join(self._tmp.name, "README.md"), "rb") as f:
            self.assertEqual(f.read(), b"hello")
        with open(os.path.join(self._tmp.name, "scripts", "kb"), "rb") as f:
            self.assertEqual(f.read(), b"#!/usr/bin/env python3\n")

    def test_overwrites_existing_file_contents(self):
        existing = os.path.join(self._tmp.name, "README.md")
        with open(existing, "w") as f:
            f.write("stale content")
        tarball = _make_tarball("pkb-cli-deadbeef", {"README.md": b"fresh content"})
        kb_cli._github_request = lambda url: tarball

        kb_cli._apply_tarball_update("deadbeef")

        with open(existing) as f:
            self.assertEqual(f.read(), "fresh content")


class TarballStatusTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_version_file = kb_cli.VERSION_FILE
        kb_cli.VERSION_FILE = os.path.join(self._tmp.name, ".pkb-cli-version")
        self.addCleanup(lambda: setattr(kb_cli, "VERSION_FILE", self._orig_version_file))

        self._orig_fetch = kb_cli._fetch_remote_sha
        self.addCleanup(lambda: setattr(kb_cli, "_fetch_remote_sha", self._orig_fetch))

    def test_no_network_call_when_fetch_false(self):
        kb_cli._write_local_version("abc123")
        kb_cli._fetch_remote_sha = lambda: (_ for _ in ()).throw(AssertionError("should not be called"))
        status = kb_cli.tarball_status(fetch=False)
        self.assertEqual(status["local"], "abc123")
        self.assertIsNone(status["remote"])

    def test_fetch_true_queries_remote_when_local_present(self):
        kb_cli._write_local_version("abc123")
        kb_cli._fetch_remote_sha = lambda: "def456"
        status = kb_cli.tarball_status(fetch=True)
        self.assertEqual(status["remote"], "def456")

    def test_fetch_true_skips_network_when_no_local_marker(self):
        kb_cli._fetch_remote_sha = lambda: (_ for _ in ()).throw(AssertionError("should not be called"))
        status = kb_cli.tarball_status(fetch=True)
        self.assertIsNone(status["local"])
        self.assertIsNone(status["remote"])


class CmdUpdateTarballTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig_version_file = kb_cli.VERSION_FILE
        kb_cli.VERSION_FILE = os.path.join(self._tmp.name, ".pkb-cli-version")
        self.addCleanup(lambda: setattr(kb_cli, "VERSION_FILE", self._orig_version_file))

        self._orig_fetch = kb_cli._fetch_remote_sha
        self.addCleanup(lambda: setattr(kb_cli, "_fetch_remote_sha", self._orig_fetch))
        self._orig_apply = kb_cli._apply_tarball_update
        self.addCleanup(lambda: setattr(kb_cli, "_apply_tarball_update", self._orig_apply))
        self.apply_calls = []
        kb_cli._apply_tarball_update = lambda sha: self.apply_calls.append(sha)

    def _args(self, check=False):
        return argparse.Namespace(check=check)

    def test_no_marker_errors_without_applying(self):
        kb_cli._fetch_remote_sha = lambda: "def456"
        rc = kb_cli._cmd_update_tarball(self._args())
        self.assertEqual(rc, 1)
        self.assertEqual(self.apply_calls, [])

    def test_up_to_date_does_not_apply(self):
        kb_cli._write_local_version("abc123")
        kb_cli._fetch_remote_sha = lambda: "abc123"
        rc = kb_cli._cmd_update_tarball(self._args())
        self.assertEqual(rc, 0)
        self.assertEqual(self.apply_calls, [])

    def test_behind_applies_and_updates_marker(self):
        kb_cli._write_local_version("abc123")
        kb_cli._fetch_remote_sha = lambda: "def456"
        rc = kb_cli._cmd_update_tarball(self._args())
        self.assertEqual(rc, 0)
        self.assertEqual(self.apply_calls, ["def456"])
        self.assertEqual(kb_cli._read_local_version(), "def456")

    def test_check_flag_reports_without_applying(self):
        kb_cli._write_local_version("abc123")
        kb_cli._fetch_remote_sha = lambda: "def456"
        rc = kb_cli._cmd_update_tarball(self._args(check=True))
        self.assertEqual(rc, 1)
        self.assertEqual(self.apply_calls, [])
        self.assertEqual(kb_cli._read_local_version(), "abc123")

    def test_empty_marker_is_treated_as_unknown_not_missing(self):
        with open(kb_cli.VERSION_FILE, "w"):
            pass  # exists but empty/corrupt
        kb_cli._fetch_remote_sha = lambda: "def456"
        rc = kb_cli._cmd_update_tarball(self._args())
        # Distinct from the "no marker at all" case: it still proceeds to
        # refresh rather than erroring out and telling the user to reinstall.
        self.assertEqual(rc, 0)
        self.assertEqual(self.apply_calls, ["def456"])
        self.assertEqual(kb_cli._read_local_version(), "def456")


if __name__ == "__main__":
    unittest.main()
