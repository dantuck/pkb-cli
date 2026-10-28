"""Tests for phone reminders (pkb_cli/remind_push.py and `kb remind push`): what gets
uploaded, the config/secret handling, and how `kb remind check` uses it. The Worker
is stubbed; nothing here touches the network."""
import io
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from pkb_cli import cli as kb_cli
from pkb_cli import remind_push


def issue(issue_id, status="open", due="2026-10-28T12:00:00Z", defer=None, priority=2, title="secret title"):
    return {"id": issue_id, "status": status, "due_at": due, "defer_until": defer,
            "priority": priority, "title": title, "description": "secret details"}


def epoch(text):
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


class SnapshotTest(unittest.TestCase):
    def test_only_ids_due_times_and_priorities_leave_the_machine(self):
        out = remind_push.snapshot([issue("kb-a", priority=1)])
        self.assertEqual(out, [{"id": "kb-a", "due_at": epoch("2026-10-28T12:00:00Z"), "priority": 1}])

    def test_closed_and_undated_todos_are_skipped(self):
        out = remind_push.snapshot([issue("kb-closed", status="closed"), issue("kb-none", due=None), issue("kb-ok")])
        self.assertEqual([r["id"] for r in out], ["kb-ok"])

    def test_a_snoozed_todo_reports_when_the_snooze_ends(self):
        snoozed = issue("kb-s", status="deferred", due="2026-10-28T12:00:00Z", defer="2026-10-30T09:00:00Z")
        self.assertEqual(remind_push.snapshot([snoozed])[0]["due_at"], epoch("2026-10-30T09:00:00Z"))

    def test_a_snooze_with_no_due_date_still_reminds_when_it_ends(self):
        snoozed = issue("kb-s", status="deferred", due=None, defer="2026-10-30T09:00:00Z")
        self.assertEqual(remind_push.snapshot([snoozed])[0]["due_at"], epoch("2026-10-30T09:00:00Z"))

    def test_priority_is_clamped_to_the_range_the_worker_accepts(self):
        self.assertEqual(remind_push.snapshot([issue("kb-a", priority=9)])[0]["priority"], 4)


class ValidUrlTest(unittest.TestCase):
    def test_https_and_local_http_only(self):
        self.assertTrue(remind_push.valid_url("https://kb-push.example.workers.dev"))
        self.assertTrue(remind_push.valid_url("http://localhost:8787"))
        self.assertFalse(remind_push.valid_url("http://kb-push.example.workers.dev"))
        self.assertFalse(remind_push.valid_url("kb-push.example.workers.dev"))
        self.assertFalse(remind_push.valid_url(""))


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        patcher = mock.patch.dict(os.environ, {"KB_CONFIG_DIR": self.tmp})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("KB_PUSH_URL", "KB_PUSH_TOKEN"):
            os.environ.pop(name, None)

    def test_saved_config_is_owner_only_and_round_trips(self):
        remind_push.save_config("https://w.example/", "tok")
        self.assertEqual(remind_push.get_config(), ("https://w.example", "tok"))
        mode = stat.S_IMODE(os.stat(os.path.join(self.tmp, "config.json")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_disable_clears_it_and_keeps_other_settings(self):
        from pkb_cli import pkb_common as pc
        pc.save_kb_config({"editor": "vim"})
        remind_push.save_config("https://w.example", "tok")
        remind_push.disable()
        self.assertIsNone(remind_push.get_config())
        self.assertEqual(pc.load_kb_config(), {"editor": "vim"})

    def test_env_overrides_saved_config(self):
        remind_push.save_config("https://saved.example", "saved")
        with mock.patch.dict(os.environ, {"KB_PUSH_URL": "https://env.example", "KB_PUSH_TOKEN": "env"}):
            self.assertEqual(remind_push.get_config(), ("https://env.example", "env"))

    def test_half_configured_counts_as_off(self):
        with mock.patch.dict(os.environ, {"KB_PUSH_URL": "https://env.example"}):
            self.assertIsNone(remind_push.get_config())


class InitWorkerTest(unittest.TestCase):
    def setUp(self):
        self.dest = os.path.join(tempfile.mkdtemp(), "worker")

    def test_copies_the_source_and_records_the_kb_version(self):
        info = remind_push.init_worker(self.dest)
        self.assertEqual(info["previous"], None)
        for rel in ("wrangler.jsonc", "src/index.ts", "migrations/0001_init.sql", "public/sw.js"):
            self.assertTrue(os.path.isfile(os.path.join(self.dest, rel)), rel)
        with open(os.path.join(self.dest, remind_push.VERSION_FILE)) as f:
            self.assertEqual(f.read().strip(), remind_push.__version__)

    def test_dev_artifacts_are_not_copied(self):
        info = remind_push.init_worker(self.dest)
        for name in ("node_modules", ".wrangler", ".dev.vars", "pnpm-lock.yaml"):
            self.assertFalse(os.path.exists(os.path.join(info["dest"], name)), name)

    def test_refresh_keeps_the_database_id_and_installed_dependencies(self):
        remind_push.init_worker(self.dest)
        config = os.path.join(self.dest, "wrangler.jsonc")
        db_id = "0a1b2c3d-0000-4000-8000-123456789abc"
        with open(config) as f:
            text = f.read()
        with open(config, "w") as f:
            f.write(text.replace(remind_push._DB_NAME_LINE, f'{remind_push._DB_NAME_LINE} "database_id": "{db_id}",'))
        os.makedirs(os.path.join(self.dest, "node_modules", "x"))
        with open(os.path.join(self.dest, remind_push.VERSION_FILE), "w") as f:
            f.write("0.0.1\n")
        with open(os.path.join(self.dest, "src", "index.ts"), "w") as f:
            f.write("// stale")

        info = remind_push.init_worker(self.dest)

        self.assertEqual(info["previous"], "0.0.1")
        with open(config) as f:
            self.assertEqual(f.read().count(db_id), 1)
        self.assertTrue(os.path.isdir(os.path.join(self.dest, "node_modules", "x")))
        with open(os.path.join(self.dest, "src", "index.ts")) as f:
            self.assertNotIn("stale", f.read())

    def test_reports_migrations_added_since_the_last_copy(self):
        remind_push.init_worker(self.dest)
        os.remove(os.path.join(self.dest, "migrations", "0001_init.sql"))
        self.assertEqual(remind_push.init_worker(self.dest)["new_migrations"], ["0001_init.sql"])
        self.assertEqual(remind_push.init_worker(self.dest)["new_migrations"], [])


class SyncTest(unittest.TestCase):
    CONFIG = ("https://w.example", "tok")

    def run_sync(self, response=(200, {}), error=None):
        stub = mock.Mock(side_effect=error) if error else mock.Mock(return_value=response)
        with mock.patch.object(remind_push, "get_config", return_value=self.CONFIG), \
                mock.patch.object(remind_push, "request", stub):
            return remind_push.sync([issue("kb-a")]), stub

    def test_puts_the_snapshot(self):
        (ok, msg), stub = self.run_sync()
        self.assertTrue(ok)
        method, path, body = stub.call_args.args[1:]
        self.assertEqual((method, path), ("PUT", "/api/sync"))
        self.assertEqual([r["id"] for r in body["reminders"]], ["kb-a"])
        self.assertNotIn("secret", str(body))

    def test_a_rejected_token_says_how_to_fix_it(self):
        (ok, msg), _ = self.run_sync(response=(401, {"error": "unauthorized"}))
        self.assertFalse(ok)
        self.assertIn("kb remind push setup", msg)

    def test_an_unreachable_worker_is_reported_not_raised(self):
        (ok, msg), _ = self.run_sync(error=OSError("timed out"))
        self.assertFalse(ok)
        self.assertIn("timed out", msg)

    def test_unconfigured_does_nothing(self):
        with mock.patch.object(remind_push, "get_config", return_value=None), \
                mock.patch.object(remind_push, "request") as req:
            ok, _ = remind_push.sync([issue("kb-a")])
        self.assertFalse(ok)
        req.assert_not_called()


class StatusTest(unittest.TestCase):
    def check(self, payload, code=200):
        with mock.patch.object(remind_push, "get_config", return_value=("https://w.example", "tok")), \
                mock.patch.object(remind_push, "request", return_value=(code, payload)):
            return remind_push.status()

    def test_healthy_with_a_phone(self):
        ok, msg = self.check({"subscriptions": 1, "reminders": 3})
        self.assertTrue(ok)
        self.assertIn("1 phone", msg)

    def test_no_phone_subscribed_is_not_healthy(self):
        ok, msg = self.check({"subscriptions": 0, "reminders": 0})
        self.assertFalse(ok)
        self.assertIn("Enable notifications", msg)


class CheckSyncsTest(unittest.TestCase):
    """`kb remind check` mirrors to the Worker on every run, and a failure never breaks it."""

    def run_check(self, sync_result):
        args = SimpleNamespace(action="check", rest=[], repeat_hours=4)
        err = io.StringIO()
        with mock.patch.object(kb_cli, "_service_kind", return_value="launchd"), \
                mock.patch.object(kb_cli.shutil, "which", return_value="/usr/bin/bd"), \
                mock.patch.object(kb_cli.pc, "get_repo_root", return_value="/repo"), \
                mock.patch.object(kb_cli, "bd_list", return_value=[issue("kb-a")]), \
                mock.patch.object(kb_cli, "reminders_to_send", return_value=[]), \
                mock.patch.object(kb_cli, "_reminder_load", return_value={}), \
                mock.patch.object(remind_push, "get_config", return_value=("https://w.example", "tok")), \
                mock.patch.object(remind_push, "sync", return_value=sync_result) as sync, \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            rc = kb_cli.cmd_remind(args)
        return rc, sync, err.getvalue()

    def test_syncs_even_when_nothing_was_announced(self):
        rc, sync, _ = self.run_check((True, "synced 1 reminder(s) to your phone"))
        self.assertEqual(rc, 0)
        sync.assert_called_once()

    def test_a_failed_sync_warns_but_does_not_fail_the_check(self):
        rc, _, err = self.run_check((False, "couldn't reach the Worker: timed out"))
        self.assertEqual(rc, 0)
        self.assertIn("warning: couldn't reach the Worker", err)


if __name__ == "__main__":
    unittest.main()
