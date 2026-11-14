"""Tests for the TODO reminder/snooze logic in pkb_cli/cli.py: due-date parsing,
what counts as due, the announce-once-then-repeat state, and `kb remind check`.
bd and the desktop notifier are stubbed; nothing here touches a real beads database."""
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

from pkb_cli import cli as kb_cli
from pkb_cli import reminders
from pkb_cli import todos
from pkb_cli import service
from pkb_cli import notifier  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def iso(delta):
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue(issue_id, status="open", due=None, defer=None, title="t"):
    return {"id": issue_id, "title": title, "status": status, "priority": 2,
            "due_at": iso(due) if due is not None else None,
            "defer_until": iso(defer) if defer is not None else None}


class RemindAtTest(unittest.TestCase):
    def at(self, **kw):
        when = kb_cli.pc.todo_remind_at(issue("x", **kw))
        return None if when is None else int((when - NOW).total_seconds())

    def test_closed_and_undated_have_no_time(self):
        self.assertIsNone(self.at(status="closed", due=timedelta(hours=1)))
        self.assertIsNone(self.at())

    def test_a_plain_todo_is_due_at_its_due_date(self):
        self.assertEqual(self.at(due=timedelta(hours=1)), 3600)

    def test_a_snooze_past_the_due_date_wins(self):
        self.assertEqual(self.at(status="deferred", due=timedelta(hours=1), defer=timedelta(days=1)), 86400)

    def test_a_snooze_before_the_due_date_does_not_pull_it_earlier(self):
        self.assertEqual(self.at(status="deferred", due=timedelta(days=1), defer=timedelta(hours=1)), 86400)

    def test_a_snooze_alone_is_when_it_comes_back(self):
        self.assertEqual(self.at(status="deferred", defer=timedelta(hours=2)), 7200)


class BdListOrderTest(unittest.TestCase):
    def test_priority_first_then_soonest_due_with_undated_last(self):
        def row(i, priority, due):
            r = issue(i, due=due)
            r["priority"] = priority
            return r
        rows = [row("p2-late", 2, timedelta(days=3)), row("p1-none", 1, None), row("p2-soon", 2, timedelta(hours=1)),
                row("p1-soon", 1, timedelta(hours=2)), row("p2-none", 2, None)]
        import json
        with mock.patch.object(todos, "_bd_run", return_value=json.dumps(rows)):
            ids = [r["id"] for r in todos.bd_list("/repo")]
        self.assertEqual(ids, ["p1-soon", "p1-none", "p2-soon", "p2-late", "p2-none"])


class ResolveWhenTest(unittest.TestCase):
    def test_minutes_become_an_absolute_timestamp_because_bd_reads_m_as_months(self):
        for text in ("30m", "+30m", "30 min", "30 minutes"):
            when = datetime.fromisoformat(todos.resolve_when(text))
            delta = when - datetime.now().astimezone()
            self.assertAlmostEqual(delta.total_seconds(), 1800, delta=5, msg=text)

    def test_bare_hours_days_weeks_get_the_plus_bd_needs(self):
        self.assertEqual(todos.resolve_when("2h"), "+2h")
        self.assertEqual(todos.resolve_when("3D"), "+3d")
        self.assertEqual(todos.resolve_when("1w"), "+1w")

    def test_everything_else_passes_through_to_bd(self):
        for text in ("+2h", "tomorrow 9am", "friday", "2026-11-02", "2026-11-02T09:00:00-06:00",
                     "2026-11-02T15:00:00Z"):
            self.assertEqual(todos.resolve_when(f"  {text} "), text)


class DueLogicTest(unittest.TestCase):
    def test_only_open_overdue_unsnoozed_todos_are_due_most_overdue_first(self):
        issues = [
            issue("late", due=timedelta(hours=-1)),
            issue("later", due=timedelta(hours=1)),
            issue("nodue"),
            issue("done", status="closed", due=timedelta(days=-1)),
            issue("snoozed", status="deferred", due=timedelta(hours=-1), defer=timedelta(hours=2)),
            issue("worse", due=timedelta(days=-2)),
        ]
        self.assertEqual([i["id"] for i in todos.todos_due(issues, NOW)], ["worse", "late"])

    def test_an_expired_snooze_no_longer_hides_a_todo(self):
        woke = issue("woke", status="deferred", due=timedelta(minutes=-5), defer=timedelta(minutes=-5))
        self.assertFalse(todos.todo_is_snoozed(woke, NOW))
        self.assertEqual([i["id"] for i in todos.todos_due([woke], NOW)], ["woke"])

    def test_format_due(self):
        cases = [
            (issue("a", due=timedelta(hours=2, minutes=5)), "due in 2h"),
            (issue("a", due=timedelta(days=-3)), "overdue 3d"),
            (issue("a", due=timedelta(seconds=-10)), "overdue <1m"),
            (issue("a", status="deferred", defer=timedelta(days=1, hours=1)), "snoozed 1d"),
            (issue("a"), ""),
        ]
        for i, expected in cases:
            self.assertEqual(todos.format_due(i, NOW), expected)


class ReminderStateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.issues = [issue("a", due=timedelta(hours=-1))]

    def announce(self, issue_id, ago):
        todos._reminder_save({**todos._reminder_load(), issue_id: (NOW - ago).isoformat()})

    def test_unannounced_due_todo_is_sent(self):
        self.assertEqual([i["id"] for i in todos.reminders_to_send(self.issues, now=NOW)], ["a"])

    def test_announced_todo_stays_quiet_until_the_repeat_interval(self):
        self.announce("a", timedelta(hours=1))
        self.assertEqual(todos.reminders_to_send(self.issues, repeat_hours=4, now=NOW), [])
        self.announce("a", timedelta(hours=5))
        self.assertEqual(len(todos.reminders_to_send(self.issues, repeat_hours=4, now=NOW)), 1)

    def test_forget_rearms_a_todo_that_was_just_announced(self):
        self.announce("a", timedelta(minutes=1))
        todos.reminder_forget("a")
        self.assertEqual(len(todos.reminders_to_send(self.issues, now=NOW)), 1)

    def test_snooze_and_due_changes_rearm_the_reminder(self):
        for call in (lambda: todos.bd_snooze("/r", "a", "1h"),
                     lambda: todos.bd_update("/r", "a", due="tomorrow")):
            self.announce("a", timedelta(minutes=1))
            with mock.patch.object(todos, "_bd_run", return_value="ok"):
                call()
            self.assertNotIn("a", todos._reminder_load())

    def test_corrupt_state_file_is_treated_as_empty(self):
        path = todos._reminder_state_path()
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("{not json")
        self.assertEqual(todos._reminder_load(), {})


class BdCommandsTest(unittest.TestCase):
    def test_snooze_defers_and_moves_the_due_date_so_a_dueless_todo_still_fires(self):
        with mock.patch.object(todos, "_bd_run", return_value="ok") as run, \
                mock.patch.object(todos, "reminder_forget"):
            todos.bd_snooze("/r", "t-1", "2h")
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ["bd", "-C", "/r", "defer", "t-1", "--until", "+2h"],
            ["bd", "-C", "/r", "update", "t-1", "--due", "+2h"],
        ])

    def test_create_and_update_pass_due_through_resolve_when(self):
        with mock.patch.object(todos, "_bd_run", return_value="ok") as run, \
                mock.patch.object(todos, "reminder_forget"):
            todos.bd_create("/r", "email", due="2h")
            todos.bd_update("/r", "t-1", due="")
        self.assertEqual(run.call_args_list[0].args[0][-2:], ["--due", "+2h"])
        self.assertEqual(run.call_args_list[1].args[0][-2:], ["--due", ""])


class RemindCheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for patch in (
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp.name}),
            mock.patch.object(kb_cli.shutil, "which", return_value="/usr/bin/bd"),
            mock.patch.object(kb_cli.pc, "get_repo_root", return_value="/r"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def run_check(self, issues):
        with mock.patch.object(todos, "bd_list", return_value=issues), \
                mock.patch.object(notifier, "notify", return_value=True) as notify, \
                redirect_stdout(io.StringIO()):
            rc = kb_cli.cmd_remind(mock.Mock(action="check", repeat_hours=4, ask=False))
        return rc, notify

    def test_notifies_once_then_stays_quiet(self):
        issues = [issue("a", due=timedelta(hours=-1), title="review email")]
        _, notify = self.run_check(issues)
        self.assertEqual(notify.call_count, 1)
        self.assertIn("review email", notify.call_args.args[1])
        self.assertIn("kb todo snooze a", notify.call_args.args[1])
        _, notify = self.run_check(issues)
        notify.assert_not_called()

    def test_caps_individual_notifications_and_summarizes_the_rest(self):
        issues = [issue(f"t{n}", due=timedelta(hours=-n - 1)) for n in range(8)]
        _, notify = self.run_check(issues)
        self.assertEqual(notify.call_count, 6)  # 5 individual + 1 summary
        self.assertIn("and 3 more", notify.call_args.args[1])
        _, notify = self.run_check(issues)  # the summarized ones count as announced too
        notify.assert_not_called()

    def test_nothing_due_sends_nothing(self):
        _, notify = self.run_check([issue("a", due=timedelta(days=36500))])  # real clock is used here, so far-future
        notify.assert_not_called()


class NotifyTest(unittest.TestCase):
    def test_text_is_passed_as_argv_not_interpolated_into_the_script(self):
        evil = 'x" & do shell script "touch /tmp/pwned" & "'
        with mock.patch.object(kb_cli.sys, "platform", "darwin"), \
                mock.patch.object(notifier, "_notifier_binary", return_value=None), \
                mock.patch.object(kb_cli.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(kb_cli.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(notifier.notify("title", evil))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[-2:], [evil, "title"])
        self.assertNotIn(evil, " ".join(cmd[:-2]))

    def test_returns_false_without_a_notifier(self):
        with mock.patch.object(kb_cli.sys, "platform", "linux"), \
                mock.patch.object(notifier, "_notifier_binary", return_value=None), \
                mock.patch.object(kb_cli.shutil, "which", return_value=None):
            self.assertFalse(notifier.notify("t", "m"))
            self.assertEqual(notifier.notify_status("t", "m"), "unavailable")


class NotifierTest(unittest.TestCase):
    def run_status(self, code, **kwargs):
        with mock.patch.object(notifier, "_notifier_binary", return_value="/n/terminal-notifier"), \
                mock.patch.object(kb_cli.subprocess, "run", return_value=mock.Mock(returncode=code)) as run:
            return notifier.notify_status("title", "msg", **kwargs), run

    def test_notifier_gets_text_as_separate_argv_entries(self):
        evil = '"; rm -rf ~ #'
        with mock.patch.object(notifier, "_notifier_binary", return_value="/n/terminal-notifier"), \
                mock.patch.object(kb_cli.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertEqual(notifier.notify_status("title", evil, group="g"), "sent")
        self.assertEqual(run.call_args.args[0],
                         ["/n/terminal-notifier", "-title", "title", "-message", evil, "-group", "g"])

    def test_exit_3_means_denied_and_fallback_false_does_not_hide_it(self):
        status, run = self.run_status(3, fallback=False)
        self.assertEqual(status, "denied")
        self.assertEqual(run.call_count, 1)  # no osascript attempt

    def test_denied_falls_back_to_osascript_for_real_reminders(self):
        with mock.patch.object(notifier, "_notifier_binary", return_value="/n/terminal-notifier"), \
                mock.patch.object(kb_cli.sys, "platform", "darwin"), \
                mock.patch.object(kb_cli.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(kb_cli.subprocess, "run",
                                  side_effect=[mock.Mock(returncode=3), mock.Mock(returncode=0)]) as run:
            self.assertEqual(notifier.notify_status("t", "m"), "sent")
        self.assertEqual(run.call_args_list[1].args[0][0], "osascript")

    def test_authorization_parsing_and_allowed(self):
        out = "terminal-notifier 3.1.0\n  authorization       denied\n  alerts              enabled\n"
        with mock.patch.object(notifier, "_notifier_binary", return_value="/n"), \
                mock.patch.object(kb_cli.subprocess, "run", return_value=mock.Mock(stdout=out)):
            self.assertEqual(notifier.notifier_authorization(), "denied")
        with mock.patch.object(notifier, "_notifier_binary", return_value=None):
            self.assertIsNone(notifier.notifier_authorization())
        self.assertTrue(notifier.notifier_allowed("authorized"))
        self.assertFalse(notifier.notifier_allowed("denied"))
        self.assertFalse(notifier.notifier_allowed("not requested yet"))
        self.assertFalse(notifier.notifier_allowed(None))

    def test_test_notification_reports_each_outcome(self):
        cases = {"sent": True, "denied": False, "failed": False, "unavailable": False}
        for status, expected in cases.items():
            with mock.patch.object(notifier, "notify_status", return_value=status), \
                    mock.patch.object(notifier, "open_notification_settings") as opened:
                ok, msg = notifier.send_test_notification()
            self.assertEqual(ok, expected, status)
            self.assertTrue(msg)
            self.assertEqual(opened.called, status == "denied")  # denial opens System Settings
            if status == "denied":
                self.assertIn("System Settings", msg)
                self.assertIn("kb remind test", msg)
                self.assertIn(notifier.NOTIFIER_BUNDLE_ID, msg)

    def test_finish_install_builds_the_app_then_tests(self):
        with mock.patch.object(kb_cli.sys, "platform", "darwin"), \
                mock.patch.object(notifier, "_terminal_notifier_app", return_value="/tn.app"), \
                mock.patch.object(notifier, "build_notifier_app", return_value=(True, "built it")) as build, \
                mock.patch.object(notifier, "send_test_notification", return_value=(False, "NOT ALLOWED")):
            ok, msg = notifier.finish_reminder_install()
        build.assert_called_once()
        self.assertFalse(ok)
        self.assertEqual(msg.splitlines(), ["built it", "NOT ALLOWED"])

    def test_finish_install_explains_the_osascript_fallback_without_terminal_notifier(self):
        with mock.patch.object(kb_cli.sys, "platform", "darwin"), \
                mock.patch.object(notifier, "_terminal_notifier_app", return_value=None), \
                mock.patch.object(notifier, "build_notifier_app") as build, \
                mock.patch.object(notifier, "send_test_notification", return_value=(True, "sent")):
            ok, msg = notifier.finish_reminder_install()
        build.assert_not_called()
        self.assertIn("brew install terminal-notifier", msg)

    def test_remind_test_action_sets_the_exit_code(self):
        for ok, code in ((True, 0), (False, 1)):
            with mock.patch.object(notifier, "send_test_notification", return_value=(ok, "m")), \
                    redirect_stdout(io.StringIO()), mock.patch.object(kb_cli.sys, "stderr", io.StringIO()):
                self.assertEqual(kb_cli.cmd_remind(mock.Mock(action="test", ask=False)), code)


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("terminal-notifier") and shutil.which("codesign"),
                     "needs macOS with terminal-notifier installed")
class BuildNotifierAppTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"HOME": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.app = os.path.join(tmp.name, "Applications", "kb reminders.app")

    def test_builds_a_branded_copy_and_skips_an_identical_rebuild(self):
        import plistlib
        ok, msg = notifier.build_notifier_app()
        self.assertTrue(ok, msg)
        with open(os.path.join(self.app, "Contents", "Info.plist"), "rb") as f:
            info = plistlib.load(f)
        self.assertEqual(info["CFBundleIdentifier"], notifier.NOTIFIER_BUNDLE_ID)
        self.assertEqual(info["CFBundleName"], "kb reminders")
        self.assertTrue(os.path.exists(os.path.join(self.app, "Contents", "Resources", "kb-reminder.icns")))
        self.assertTrue(os.access(notifier._notifier_binary(), os.X_OK))
        before = os.stat(os.path.join(self.app, "Contents", "Info.plist")).st_mtime_ns
        ok, msg = notifier.build_notifier_app()
        self.assertTrue(ok)
        self.assertIn("up to date", msg)  # re-signing would risk losing a granted permission
        self.assertEqual(before, os.stat(os.path.join(self.app, "Contents", "Info.plist")).st_mtime_ns)


class RemindersInfoTest(unittest.TestCase):
    def info(self, kind, state, auth="denied", built=True, stock=True):
        with mock.patch.object(service, "_service_kind", return_value=kind), \
                mock.patch.object(service, "_service_state", return_value=state), \
                mock.patch.object(reminders, "reminder_status_line", return_value=(False, "msg")), \
                mock.patch.object(service, "_timer_interval_minutes", return_value=5), \
                mock.patch.object(notifier, "notifier_authorization", return_value=auth), \
                mock.patch.object(notifier, "_notifier_binary", return_value="/n" if built else None), \
                mock.patch.object(notifier, "_terminal_notifier_app", return_value="/t.app" if stock else None):
            return reminders.reminders_info()

    def test_unsupported_platform(self):
        self.assertEqual(self.info(None, {}), {"supported": False})

    def test_macos_reports_permission_and_interval(self):
        info = self.info("launchd", {"installed": True, "running": False, "detail": None})
        self.assertEqual((info["installed"], info["interval_minutes"]), (True, 5))
        self.assertEqual(info["notifier"], {"app_built": True, "terminal_notifier": True,
                                            "authorization": "denied", "allowed": False})
        info = self.info("launchd", {"installed": True, "running": False, "detail": None}, auth="authorized")
        self.assertTrue(info["notifier"]["allowed"])

    def test_not_installed_has_no_interval_and_linux_has_no_notifier_block(self):
        info = self.info("launchd", {"installed": False, "running": False, "detail": "not installed"})
        self.assertIsNone(info["interval_minutes"])
        self.assertNotIn("notifier", self.info("systemd", {"installed": True, "running": True, "detail": None}))


class OpenSettingsTest(unittest.TestCase):
    def test_only_opens_for_a_terminal_unless_forced(self):
        with mock.patch.object(kb_cli.sys, "platform", "darwin"), \
                mock.patch.object(kb_cli.sys.stdout, "isatty", return_value=False), \
                mock.patch.object(kb_cli.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertFalse(notifier.open_notification_settings())
            run.assert_not_called()
            self.assertTrue(notifier.open_notification_settings(force=True))
            self.assertEqual(run.call_args.args[0], ["open", notifier.NOTIFICATION_SETTINGS_URL])

    def test_never_on_linux(self):
        with mock.patch.object(kb_cli.sys, "platform", "linux"):
            self.assertFalse(notifier.open_notification_settings(force=True))


class TodoWordCommandTest(unittest.TestCase):
    def run_words(self, *words):
        err = io.StringIO()
        with mock.patch.object(todos, "bd_snooze") as snooze, \
                mock.patch.object(todos, "bd_update") as update, \
                mock.patch.object(todos, "bd_unsnooze", return_value="woken") as unsnooze, \
                redirect_stdout(io.StringIO()), mock.patch.object(kb_cli.sys, "stderr", err):
            rc = kb_cli._todo_word_command("/r", list(words))
        return rc, snooze, update, unsnooze

    def test_snooze_due_unsnooze(self):
        rc, snooze, _, _ = self.run_words("snooze", "t-1", "1h")
        self.assertEqual((rc, snooze.call_args.args), (0, ("/r", "t-1", "1h")))
        _, _, update, _ = self.run_words("due", "t-1", "tomorrow")
        self.assertEqual(update.call_args.kwargs, {"due": "tomorrow"})
        _, _, update, _ = self.run_words("due", "t-1", "none")
        self.assertEqual(update.call_args.kwargs, {"due": ""})
        _, _, _, unsnooze = self.run_words("unsnooze", "t-1")
        unsnooze.assert_called_once_with("/r", "t-1")

    def test_bad_usage_exits_2_and_changes_nothing(self):
        for words in (("snooze", "t-1"), ("snooze",), ("bogus", "t-1", "1h")):
            rc, snooze, update, unsnooze = self.run_words(*words)
            self.assertEqual(rc, 2, words)
            for m in (snooze, update, unsnooze):
                m.assert_not_called()


class ReminderStatusLineTest(unittest.TestCase):
    def status(self, kind, state, have_notifier=True, bundle=None, stock_app=None):
        """bundle: the built kb reminders app's authorization string (None = not built)."""
        with mock.patch.object(service, "_service_state", return_value=state), \
                mock.patch.object(kb_cli.shutil, "which", return_value="/x" if have_notifier else None), \
                mock.patch.object(notifier, "_notifier_binary", return_value="/n" if bundle else None), \
                mock.patch.object(notifier, "notifier_authorization", return_value=bundle), \
                mock.patch.object(notifier, "_terminal_notifier_app", return_value=stock_app):
            return reminders.reminder_status_line(kind)

    def test_built_app_needs_notification_permission(self):
        up = {"installed": True, "running": False, "detail": None}
        self.assertTrue(self.status("launchd", up, bundle="authorized")[0])
        for auth in ("denied", "not requested yet"):
            ok, msg = self.status("launchd", up, bundle=auth)
            self.assertFalse(ok, auth)
            self.assertIn("kb remind test", msg)
            self.assertIn(auth, msg)

    def test_stock_notifier_without_the_built_app_points_at_install(self):
        ok, msg = self.status("launchd", {"installed": True, "running": False, "detail": None},
                              stock_app="/opt/tn.app")
        self.assertFalse(ok)
        self.assertIn("kb remind install", msg)

    def test_not_installed_is_optional_and_names_the_command(self):
        ok, msg = self.status("launchd", {"installed": False, "running": False, "detail": "not installed"})
        self.assertFalse(ok)
        self.assertIn("kb remind install", msg)
        self.assertIn("optional", msg)

    def test_healthy_launchd_and_systemd(self):
        self.assertTrue(self.status("launchd", {"installed": True, "running": False, "detail": None})[0])
        self.assertTrue(self.status("systemd", {"installed": True, "running": True, "detail": None})[0])

    def test_installed_but_not_loaded_or_inactive_needs_attention(self):
        ok, msg = self.status("launchd", {"installed": True, "running": False, "detail": "installed (p) but not loaded"})
        self.assertFalse(ok)
        self.assertIn("not loaded", msg)
        ok, msg = self.status("systemd", {"installed": True, "running": False, "detail": "failed"})
        self.assertFalse(ok)
        self.assertIn("failed", msg)

    def test_scheduled_without_a_notifier_is_flagged(self):
        ok, msg = self.status("launchd", {"installed": True, "running": False, "detail": None}, have_notifier=False)
        self.assertFalse(ok)
        self.assertIn("osascript", msg)

    def test_unsupported_platform(self):
        with mock.patch.object(service, "_service_kind", return_value=None):
            ok, msg = reminders.reminder_status_line()
        self.assertFalse(ok)
        self.assertIn("kb remind check", msg)


if __name__ == "__main__":
    unittest.main()
