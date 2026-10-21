"""Tests for the TODO reminder/snooze logic in pkb_cli/cli.py: due-date parsing,
what counts as due, the announce-once-then-repeat state, and `kb remind check`.
bd and the desktop notifier are stubbed; nothing here touches a real beads database."""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

from pkb_cli import cli as kb_cli  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def iso(delta):
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def issue(issue_id, status="open", due=None, defer=None, title="t"):
    return {"id": issue_id, "title": title, "status": status, "priority": 2,
            "due_at": iso(due) if due is not None else None,
            "defer_until": iso(defer) if defer is not None else None}


class ResolveWhenTest(unittest.TestCase):
    def test_minutes_become_an_absolute_timestamp_because_bd_reads_m_as_months(self):
        for text in ("30m", "+30m", "30 min", "30 minutes"):
            when = datetime.fromisoformat(kb_cli.resolve_when(text))
            delta = when - datetime.now().astimezone()
            self.assertAlmostEqual(delta.total_seconds(), 1800, delta=5, msg=text)

    def test_bare_hours_days_weeks_get_the_plus_bd_needs(self):
        self.assertEqual(kb_cli.resolve_when("2h"), "+2h")
        self.assertEqual(kb_cli.resolve_when("3D"), "+3d")
        self.assertEqual(kb_cli.resolve_when("1w"), "+1w")

    def test_everything_else_passes_through_to_bd(self):
        for text in ("+2h", "tomorrow 9am", "friday", "2026-11-02", "2026-11-02T09:00:00-06:00"):
            self.assertEqual(kb_cli.resolve_when(f"  {text} "), text)


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
        self.assertEqual([i["id"] for i in kb_cli.todos_due(issues, NOW)], ["worse", "late"])

    def test_an_expired_snooze_no_longer_hides_a_todo(self):
        woke = issue("woke", status="deferred", due=timedelta(minutes=-5), defer=timedelta(minutes=-5))
        self.assertFalse(kb_cli.todo_is_snoozed(woke, NOW))
        self.assertEqual([i["id"] for i in kb_cli.todos_due([woke], NOW)], ["woke"])

    def test_format_due(self):
        cases = [
            (issue("a", due=timedelta(hours=2, minutes=5)), "due in 2h"),
            (issue("a", due=timedelta(days=-3)), "overdue 3d"),
            (issue("a", due=timedelta(seconds=-10)), "overdue <1m"),
            (issue("a", status="deferred", defer=timedelta(days=1, hours=1)), "snoozed 1d"),
            (issue("a"), ""),
        ]
        for i, expected in cases:
            self.assertEqual(kb_cli.format_due(i, NOW), expected)


class ReminderStateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.issues = [issue("a", due=timedelta(hours=-1))]

    def announce(self, issue_id, ago):
        kb_cli._reminder_save({**kb_cli._reminder_load(), issue_id: (NOW - ago).isoformat()})

    def test_unannounced_due_todo_is_sent(self):
        self.assertEqual([i["id"] for i in kb_cli.reminders_to_send(self.issues, now=NOW)], ["a"])

    def test_announced_todo_stays_quiet_until_the_repeat_interval(self):
        self.announce("a", timedelta(hours=1))
        self.assertEqual(kb_cli.reminders_to_send(self.issues, repeat_hours=4, now=NOW), [])
        self.announce("a", timedelta(hours=5))
        self.assertEqual(len(kb_cli.reminders_to_send(self.issues, repeat_hours=4, now=NOW)), 1)

    def test_forget_rearms_a_todo_that_was_just_announced(self):
        self.announce("a", timedelta(minutes=1))
        kb_cli.reminder_forget("a")
        self.assertEqual(len(kb_cli.reminders_to_send(self.issues, now=NOW)), 1)

    def test_snooze_and_due_changes_rearm_the_reminder(self):
        for call in (lambda: kb_cli.bd_snooze("/r", "a", "1h"),
                     lambda: kb_cli.bd_update("/r", "a", due="tomorrow")):
            self.announce("a", timedelta(minutes=1))
            with mock.patch.object(kb_cli, "_bd_run", return_value="ok"):
                call()
            self.assertNotIn("a", kb_cli._reminder_load())

    def test_corrupt_state_file_is_treated_as_empty(self):
        path = kb_cli._reminder_state_path()
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write("{not json")
        self.assertEqual(kb_cli._reminder_load(), {})


class BdCommandsTest(unittest.TestCase):
    def test_snooze_defers_and_moves_the_due_date_so_a_dueless_todo_still_fires(self):
        with mock.patch.object(kb_cli, "_bd_run", return_value="ok") as run, \
                mock.patch.object(kb_cli, "reminder_forget"):
            kb_cli.bd_snooze("/r", "t-1", "2h")
        self.assertEqual([c.args[0] for c in run.call_args_list], [
            ["bd", "-C", "/r", "defer", "t-1", "--until", "+2h"],
            ["bd", "-C", "/r", "update", "t-1", "--due", "+2h"],
        ])

    def test_create_and_update_pass_due_through_resolve_when(self):
        with mock.patch.object(kb_cli, "_bd_run", return_value="ok") as run, \
                mock.patch.object(kb_cli, "reminder_forget"):
            kb_cli.bd_create("/r", "email", due="2h")
            kb_cli.bd_update("/r", "t-1", due="")
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
        with mock.patch.object(kb_cli, "bd_list", return_value=issues), \
                mock.patch.object(kb_cli, "notify", return_value=True) as notify, \
                redirect_stdout(io.StringIO()):
            rc = kb_cli.cmd_remind(mock.Mock(action="check", repeat_hours=4))
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
                mock.patch.object(kb_cli.shutil, "which", return_value="/usr/bin/osascript"), \
                mock.patch.object(kb_cli.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(kb_cli.notify("title", evil))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[-2:], [evil, "title"])
        self.assertNotIn(evil, " ".join(cmd[:-2]))

    def test_returns_false_without_a_notifier(self):
        with mock.patch.object(kb_cli.sys, "platform", "linux"), \
                mock.patch.object(kb_cli.shutil, "which", return_value=None):
            self.assertFalse(kb_cli.notify("t", "m"))


class TodoWordCommandTest(unittest.TestCase):
    def run_words(self, *words):
        err = io.StringIO()
        with mock.patch.object(kb_cli, "bd_snooze") as snooze, \
                mock.patch.object(kb_cli, "bd_update") as update, \
                mock.patch.object(kb_cli, "bd_unsnooze", return_value="woken") as unsnooze, \
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
    def status(self, kind, state, have_notifier=True):
        with mock.patch.object(kb_cli, "_service_state", return_value=state), \
                mock.patch.object(kb_cli.shutil, "which", return_value="/x" if have_notifier else None):
            return kb_cli.reminder_status_line(kind)

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
        with mock.patch.object(kb_cli, "_service_kind", return_value=None):
            ok, msg = kb_cli.reminder_status_line()
        self.assertFalse(ok)
        self.assertIn("kb remind check", msg)


if __name__ == "__main__":
    unittest.main()
