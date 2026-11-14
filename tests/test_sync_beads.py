"""sync_beads: fetch (via a faked `bd`), idempotent mirror writing, inbox stub only on first sight."""
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from pkb_cli import pkb_common as pc
from pkb_cli import sync_beads

BEAD = {"id": "bd-1", "title": "Fix it", "description": "details",
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FetchTest(unittest.TestCase):
    def fetch(self, result=None, side_effect=None, **kw):
        with mock.patch.object(sync_beads.subprocess, "run", return_value=result, side_effect=side_effect) as run:
            out = sync_beads.fetch_beads("bd", kw.pop("since", None), **kw)
        return out, run.call_args[0][0]

    def test_sorted_by_update_and_dir_flag(self):
        newer = {**BEAD, "id": "bd-2", "updated_at": "2026-02-01T00:00:00Z"}
        beads, args = self.fetch(completed(json.dumps([newer, BEAD])), directory="/proj")
        self.assertEqual([b["id"] for b in beads], ["bd-1", "bd-2"])
        self.assertEqual(args[-2:], ["-C", "/proj"])

    def test_since_adds_lookback_filter(self):
        _, args = self.fetch(completed("[]"), since="2026-01-01T00:00:00Z")
        self.assertIn("--updated-after", args)

    def test_errors_become_runtime_errors(self):
        for kwargs in ({"side_effect": FileNotFoundError()},
                       {"side_effect": subprocess.TimeoutExpired("bd", 30)},
                       {"result": completed("", 1, "boom")},
                       {"result": completed("not json")}):
            with self.assertRaises(RuntimeError):
                self.fetch(**kwargs)


class WriteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def write(self, bead, existing, inbox_all=True):
        return sync_beads.write_bead(self.root, bead, existing, {"x"}, inbox_all)

    def test_add_then_unchanged_then_updated(self):
        existing = {}
        path, outcome = self.write(BEAD, existing)
        self.assertEqual(outcome, "added")
        self.assertEqual(len(os.listdir(os.path.join(self.root, "inbox"))), 1)
        existing["bd-1"] = path
        self.assertEqual(self.write(BEAD, existing)[1], "unchanged")
        self.assertEqual(self.write({**BEAD, "title": "Renamed", "updated_at": "2026-03-01T00:00:00Z"}, existing)[1],
                         "updated")
        self.assertEqual(pc.read_entry(path)[0]["title"], "Renamed")
        self.assertEqual(len(os.listdir(os.path.join(self.root, "inbox"))), 1)  # no second stub

    def test_no_inbox_stub_when_disabled(self):
        self.write(BEAD, {}, inbox_all=False)
        self.assertFalse(os.path.exists(os.path.join(self.root, "inbox")))


if __name__ == "__main__":
    unittest.main()
