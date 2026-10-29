"""Tests for phone reminders (pkb_cli/remind_push.py and `kb remind push`): what gets
uploaded, the config/secret handling, and how `kb remind check` uses it. The Worker
is stubbed; nothing here touches the network."""
import io
import json
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
    def test_only_id_title_due_time_and_priority_leave_the_machine(self):
        out = remind_push.snapshot([issue("kb-a", priority=1)])
        self.assertEqual(out, [{"id": "kb-a", "title": "secret title",
                                "due_at": epoch("2026-10-28T12:00:00Z"), "priority": 1}])
        self.assertNotIn("secret details", json.dumps(out), "descriptions never leave the machine")

    def test_a_long_title_is_cut_to_what_the_worker_keeps(self):
        out = remind_push.snapshot([issue("kb-a", title="x" * 500)])
        self.assertEqual(len(out[0]["title"]), remind_push.TITLE_MAX_CHARS)

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

    def test_copies_the_source_and_returns_where(self):
        self.assertEqual(remind_push.init_worker(self.dest), self.dest)
        for rel in ("wrangler.jsonc", "src/index.ts", "migrations/0001_init.sql", "public/sw.js"):
            self.assertTrue(os.path.isfile(os.path.join(self.dest, rel)), rel)

    def test_dev_artifacts_are_not_copied(self):
        remind_push.init_worker(self.dest)
        for name in ("node_modules", ".wrangler", ".dev.vars", "pnpm-lock.yaml"):
            self.assertFalse(os.path.exists(os.path.join(self.dest, name)), name)

    def test_refresh_keeps_the_database_id_and_installed_dependencies(self):
        remind_push.init_worker(self.dest)
        config = os.path.join(self.dest, "wrangler.jsonc")
        db_id = "0a1b2c3d-0000-4000-8000-123456789abc"
        self.assertTrue(remind_push.set_database_id(config, db_id))
        os.makedirs(os.path.join(self.dest, "node_modules", "x"))
        with open(os.path.join(self.dest, "src", "index.ts"), "w") as f:
            f.write("// stale")

        remind_push.init_worker(self.dest)

        with open(config) as f:
            self.assertEqual(f.read().count(db_id), 1)
        self.assertTrue(os.path.isdir(os.path.join(self.dest, "node_modules", "x")))
        with open(os.path.join(self.dest, "src", "index.ts")) as f:
            self.assertNotIn("stale", f.read())

    def write(self, rel, text="x"):
        path = os.path.join(self.dest, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def set_manifest(self, files):
        self.write(remind_push.MANIFEST_FILE, json.dumps(files))

    def test_refresh_removes_files_an_earlier_copy_shipped_and_this_release_dropped(self):
        remind_push.init_worker(self.dest)
        stale = self.write("public/old.png")
        nested = self.write("retired/helper.js")
        self.set_manifest(["public/old.png", "retired/helper.js", "src/index.ts"])

        remind_push.init_worker(self.dest)

        self.assertFalse(os.path.exists(stale))
        self.assertFalse(os.path.exists(nested))
        self.assertFalse(os.path.isdir(os.path.join(self.dest, "retired")), "emptied directory is removed too")
        self.assertTrue(os.path.isfile(os.path.join(self.dest, "src", "index.ts")))

    def test_refresh_leaves_the_users_own_files_alone(self):
        remind_push.init_worker(self.dest)
        mine = self.write("notes.txt")
        remind_push.init_worker(self.dest)
        self.assertTrue(os.path.exists(mine))

    def test_a_copy_from_before_the_manifest_still_loses_the_icon_an_old_release_shipped(self):
        remind_push.init_worker(self.dest)
        os.remove(os.path.join(self.dest, remind_push.MANIFEST_FILE))
        old_icon = self.write("public/icon.png")
        remind_push.init_worker(self.dest)
        self.assertFalse(os.path.exists(old_icon))

    def test_the_manifest_lists_what_was_copied(self):
        remind_push.init_worker(self.dest)
        with open(os.path.join(self.dest, remind_push.MANIFEST_FILE)) as f:
            listed = json.load(f)
        self.assertIn("src/index.ts", listed)
        self.assertEqual(sorted(listed), sorted(remind_push._source_files()))

    def test_a_tampered_manifest_cannot_delete_outside_the_worker_directory(self):
        remind_push.init_worker(self.dest)
        outside = os.path.join(os.path.dirname(self.dest), "precious.txt")
        with open(outside, "w") as f:
            f.write("keep")
        self.set_manifest(["../precious.txt", "/etc/hosts", "."])
        remind_push.init_worker(self.dest)
        self.assertTrue(os.path.exists(outside))
        self.assertTrue(os.path.isdir(self.dest))

    def test_set_database_id_reports_when_the_config_no_longer_has_its_anchor(self):
        remind_push.init_worker(self.dest)
        config = os.path.join(self.dest, "wrangler.jsonc")
        with open(config, "w") as f:
            f.write("{}")
        self.assertFalse(remind_push.set_database_id(config, "0a1b2c3d-0000-4000-8000-123456789abc"))


class SourceHashTest(unittest.TestCase):
    """The deploy fingerprint must agree between a checkout and an installed wheel."""

    def hash_of(self, files):
        src = tempfile.mkdtemp()
        for rel, text in files.items():
            path = os.path.join(src, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(text)
        remind_push.source_hash.cache_clear()
        with mock.patch.object(remind_push, "WORKER_SRC", src):
            result = remind_push.source_hash()
        remind_push.source_hash.cache_clear()
        return result

    def test_ignores_dotfiles_a_checkout_has_and_a_wheel_lacks(self):
        files = {"src/index.ts": "a", "wrangler.jsonc": "b"}
        self.assertEqual(self.hash_of(files), self.hash_of({**files, ".gitignore": "node_modules/"}))

    def test_changes_when_a_shipped_file_changes_or_is_added(self):
        base = {"src/index.ts": "a"}
        self.assertNotEqual(self.hash_of(base), self.hash_of({"src/index.ts": "b"}))
        self.assertNotEqual(self.hash_of(base), self.hash_of({**base, "public/new.png": "x"}))


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
        self.assertEqual(body["reminders"][0]["title"], "secret title")
        self.assertNotIn("secret details", str(body), "descriptions never leave the machine")

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


class AskFlagTest(unittest.TestCase):
    """--ask is `kb update`'s way of asking for deploy-if-stale; nothing else takes it."""

    def run_remind(self, action, rest):
        err = io.StringIO()
        args = SimpleNamespace(action=action, rest=rest, ask=True)
        with redirect_stderr(err), mock.patch.object(kb_cli.worker_deploy, "deploy", return_value=0) as deploy:
            rc = kb_cli.cmd_remind(args)
        return rc, deploy, err.getvalue()

    def test_rejected_for_other_actions(self):
        for action, rest in (("check", []), ("test", []), ("push", ["sync"]), ("push", [])):
            rc, deploy, err = self.run_remind(action, rest)
            self.assertEqual(rc, 2, (action, rest))
            self.assertIn("--ask only applies to `kb remind push deploy`", err)
            deploy.assert_not_called()

    def test_accepted_for_push_deploy(self):
        rc, deploy, _ = self.run_remind("push", ["deploy"])
        self.assertEqual(rc, 0)
        deploy.assert_called_once_with(ask=True)


class CheckSyncsTest(unittest.TestCase):
    """`kb remind check` mirrors to the Worker on every run, and a failure never breaks it."""

    def run_check(self, sync_result):
        args = SimpleNamespace(action="check", rest=[], repeat_hours=4, ask=False)
        err = io.StringIO()
        with mock.patch.object(kb_cli, "_service_kind", return_value="launchd"), \
                mock.patch.object(kb_cli.shutil, "which", return_value="/usr/bin/bd"), \
                mock.patch.object(kb_cli.pc, "get_repo_root", return_value="/repo"), \
                mock.patch.object(kb_cli, "bd_list", return_value=[issue("kb-a")]), \
                mock.patch.object(kb_cli, "reminders_to_send", return_value=[]), \
                mock.patch.object(kb_cli, "_reminder_load", return_value={}), \
                mock.patch.object(remind_push, "get_config", return_value=("https://w.example", "tok")), \
                mock.patch.object(remind_push, "pull_actions", return_value=([], None)), \
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


def action(action_id, todo_id, verb, until=None):
    return {"id": action_id, "todo_id": todo_id, "action": verb, "until": until}


class PhoneExchangeTest(unittest.TestCase):
    """What the phone asked for is applied on this machine before the reminders are mirrored."""

    def run_exchange(self, actions, pull_error=None, bd_fails=()):
        calls = []

        def bd_action(root, verb, todo_id, *rest):
            if todo_id in bd_fails:
                raise RuntimeError("bd: no such issue")
            calls.append((verb, todo_id))

        def bd_snooze(root, todo_id, until):
            if todo_id in bd_fails:
                raise RuntimeError("bd: no such issue")
            calls.append(("snooze", todo_id, until))

        order = []
        err = io.StringIO()
        with mock.patch.object(remind_push, "pull_actions", return_value=(actions, pull_error)), \
                mock.patch.object(remind_push, "ack_actions", side_effect=lambda ids: order.append(("ack", ids))) as ack, \
                mock.patch.object(remind_push, "sync", side_effect=lambda issues: order.append(("sync", [i["id"] for i in issues])) or (True, "ok")) as sync, \
                mock.patch.object(kb_cli.pc, "get_repo_root", return_value="/repo"), \
                mock.patch.object(kb_cli, "bd_action", side_effect=bd_action), \
                mock.patch.object(kb_cli, "bd_snooze", side_effect=bd_snooze), \
                mock.patch.object(kb_cli, "bd_list", return_value=[issue("kb-after")]), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            result = kb_cli._phone_exchange([issue("kb-before")])
        return result, calls, order, ack, sync, err.getvalue()

    def test_nothing_asked_just_syncs_the_issues_it_was_given(self):
        result, calls, order, ack, _, _ = self.run_exchange([])
        self.assertEqual(result, (True, "ok"))
        self.assertEqual((calls, order), ([], [("sync", ["kb-before"])]))
        ack.assert_not_called()

    def test_close_and_snooze_are_applied_then_acked_then_synced_from_fresh_issues(self):
        until = int(datetime.now().timestamp()) + 3 * 3600
        _, calls, order, _, _, _ = self.run_exchange([action(1, "kb-a", "close"), action(2, "kb-b", "snooze", until)])
        self.assertEqual(calls[0], ("close", "kb-a"))
        self.assertEqual(calls[1][:2], ("snooze", "kb-b"))
        self.assertEqual(order, [("ack", [1, 2]), ("sync", ["kb-after"])], "acked, then synced from a re-read")

    def test_the_snooze_ends_when_the_phone_said_not_when_kb_got_to_it(self):
        until = int(datetime.now().timestamp()) + 3 * 3600
        _, calls, *_ = self.run_exchange([action(1, "kb-b", "snooze", until)])
        self.assertEqual(datetime.fromisoformat(calls[0][2]).timestamp(), until)

    def test_a_snooze_the_phone_asked_for_long_ago_is_still_in_the_future(self):
        _, calls, *_ = self.run_exchange([action(1, "kb-b", "snooze", 1000)])
        self.assertGreater(datetime.fromisoformat(calls[0][2]).timestamp(), datetime.now().timestamp())

    def test_a_failed_request_warns_and_is_still_acked_so_it_cannot_loop(self):
        _, calls, order, _, _, err = self.run_exchange([action(1, "kb-gone", "close")], bd_fails=("kb-gone",))
        self.assertIn("kb-gone", err)
        self.assertEqual(order, [("ack", [1]), ("sync", ["kb-before"])])

    def test_ids_that_could_be_read_as_flags_or_paths_never_reach_bd(self):
        bad = [action(1, "--force", "close"), action(2, "-rf", "close"), action(3, "../x", "close"),
               action(4, "kb-a b", "close"), action(5, "", "close"), action(6, None, "close"),
               action(7, "kb-a" * 40, "close")]
        _, calls, _, _, _, err = self.run_exchange(bad)
        self.assertEqual(calls, [])
        self.assertEqual(err.count("ignored a request for an odd TODO id"), len(bad))

    def test_only_close_and_snooze_run(self):
        _, calls, _, _, _, err = self.run_exchange([action(1, "kb-a", "delete"), action(2, "kb-a", "snooze", "soon")])
        self.assertEqual(calls, [])
        self.assertIn("ignored an unknown request", err)

    def test_an_unreachable_worker_warns_but_still_attempts_the_sync(self):
        result, _, order, _, _, err = self.run_exchange([], pull_error="couldn't reach the Worker: timed out")
        self.assertIn("couldn't fetch the phone's requests", err)
        self.assertEqual(order, [("sync", ["kb-before"])])
        self.assertTrue(result[0])


class PullActionsTest(unittest.TestCase):
    CONFIG = ("https://w.example", "tok")

    def pull(self, status, payload):
        with mock.patch.object(remind_push, "get_config", return_value=self.CONFIG), \
                mock.patch.object(remind_push, "request", return_value=(status, payload)):
            return remind_push.pull_actions()

    def test_returns_the_queued_requests(self):
        got, err = self.pull(200, {"actions": [action(1, "kb-a", "close"), "junk"]})
        self.assertEqual((got, err), ([action(1, "kb-a", "close")], None))

    def test_a_worker_from_before_the_phone_page_means_nothing_was_asked(self):
        self.assertEqual(self.pull(404, {"error": "not found"}), ([], None))

    def test_other_failures_are_errors(self):
        self.assertEqual(self.pull(401, {"error": "unauthorized"}), ([], "unauthorized"))
        self.assertEqual(self.pull(500, {}), ([], "the Worker answered HTTP 500"))

    def test_a_network_failure_is_an_error(self):
        with mock.patch.object(remind_push, "get_config", return_value=self.CONFIG), \
                mock.patch.object(remind_push, "request", side_effect=OSError("timed out")):
            got, err = remind_push.pull_actions()
        self.assertEqual(got, [])
        self.assertIn("timed out", err)


class DevicesCommandTest(unittest.TestCase):
    def run_devices(self, rest, **patches):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(remind_push, "get_config", return_value=("https://w.example", "tok")), \
                redirect_stdout(out), redirect_stderr(err):
            for name, value in patches.items():
                mock.patch.object(remind_push, name, **value).start()
            try:
                rc = kb_cli._remind_push_devices(rest)
            finally:
                mock.patch.stopall()
        return rc, out.getvalue(), err.getvalue()

    def test_lists_each_phone_with_its_id_and_label(self):
        found = [{"id": 3, "label": "Chrome on Pixel 8", "created_at": 1793234000, "service": "fcm.googleapis.com"},
                 {"id": 4, "label": None, "created_at": 1793235000, "service": "push.services.mozilla.com"}]
        rc, out, _ = self.run_devices([], devices={"return_value": (found, None)})
        self.assertEqual(rc, 0)
        self.assertIn("Chrome on Pixel 8", out)
        self.assertIn("(unnamed)", out)
        self.assertIn("devices rm <id>", out)

    def test_says_so_when_there_are_none(self):
        rc, out, _ = self.run_devices([], devices={"return_value": ([], None)})
        self.assertEqual((rc, out.strip()), (0, "no phones are subscribed"))

    def test_rm_removes_by_id(self):
        rc, out, _ = self.run_devices(["rm", "3"], remove_device={"return_value": (True, "removed device 3")})
        self.assertEqual((rc, out.strip()), (0, "removed device 3"))

    def test_rm_passes_an_integer_and_reports_a_failure(self):
        with mock.patch.object(remind_push, "get_config", return_value=("https://w.example", "tok")), \
                mock.patch.object(remind_push, "remove_device", return_value=(False, "no such device")) as rm, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = kb_cli._remind_push_devices(["rm", "9"])
        rm.assert_called_once_with(9)
        self.assertEqual(rc, 1)

    def test_bad_usage_is_refused_without_calling_the_worker(self):
        for rest in (["rm"], ["rm", "abc"], ["rm", "1", "2"], ["bogus"]):
            rc, _, err = self.run_devices(rest, remove_device={"side_effect": AssertionError("called")})
            self.assertEqual(rc, 2, rest)
            self.assertIn("usage:", err)


if __name__ == "__main__":
    unittest.main()
