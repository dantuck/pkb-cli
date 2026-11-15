"""Guards the installed-package layout: modules that run as subprocesses
(`python -m pkb_cli.<module>`), in-process imports of sibling modules, and
bundled data files all have to resolve the same way from a wheel as from a
source checkout."""
import contextlib
import importlib.resources
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pkb_cli import __version__, cli, updater, service, pkb_common as pc, pkb_entries, proc


class PackagingTest(unittest.TestCase):
    def setUp(self):
        # kb's own progress lines ("refreshed dev.pkb-cli.web", "kb 999.0.0 is available", ...)
        # come from code these tests mock out; printed, they read like a real service restart
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def test_python_dash_m_entry_point_runs(self):
        out = subprocess.run([sys.executable, "-m", "pkb_cli", "--version"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(__version__, out.stdout)

    def test_bundled_data_files_are_present(self):
        pkg = importlib.resources.files("pkb_cli")
        for rel in ("templates/schema.sql", "templates/web/index.html", "templates/web/vendor/alpine.min.js",
                    "skills/kb/SKILL.md", "templates/notifier/kb-reminder.png",
                    "templates/worker/wrangler.jsonc", "templates/worker/src/index.ts",
                    "templates/worker/migrations/0001_init.sql", "templates/worker/migrations/0002_phone_todos.sql",
                    "templates/worker/public/index.html",
                    "templates/worker/public/badge.png", "templates/worker/public/icon-192.png"):
            self.assertTrue(pkg.joinpath(rel).is_file(), rel)

    def test_reindex_runs_in_process_and_as_subprocess(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, ".pkb"))
            for d in pc.TYPE_DIR.values():
                os.makedirs(os.path.join(root, d))
            pkb_entries._reindex_fast(root)
            # cwd is the data repo, so a source checkout (not pip-installed) needs the
            # package's parent on PYTHONPATH to be importable there
            env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(pkb_entries.__file__)))
            out = subprocess.run([sys.executable, "-m", "pkb_cli.index_fts"], cwd=root, env=env,
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)

    def test_update_reinstalls_by_tag_with_whichever_tool_owns_the_venv(self):
        # `uv/pipx upgrade` never moves a pinned git ref, so kb update must reinstall by tag
        with tempfile.TemporaryDirectory() as prefix, mock.patch.object(sys, "prefix", prefix):
            self.assertIsNone(updater._installer())
            for marker, tool in (("uv-receipt.toml", "uv"), ("pipx_metadata.json", "pipx")):
                open(os.path.join(prefix, marker), "w").close()
                self.assertEqual(updater._installer(), tool)
                os.remove(os.path.join(prefix, marker))
        self.assertEqual(updater._install_command("uv", "v1.2.3")[:3], ["uv", "tool", "install"])
        self.assertEqual(updater._install_command("pipx", "v1.2.3")[:2], ["pipx", "install"])
        self.assertTrue(updater._install_command("uv", "v1.2.3")[-1].endswith("@v1.2.3"))
        self.assertEqual(updater._install_command("pipx", "v1.2.3", "semantic")[-1],
                         f"pkb-cli[semantic] @ git+https://github.com/{updater.TOOL_REPO_SLUG}@v1.2.3")

    def test_install_hint_uses_the_installer_that_owns_the_venv_and_the_running_version(self):
        with mock.patch.object(updater, "_installer", return_value="pipx"), mock.patch.object(updater, "__version__", "1.2.3"):
            hint = updater.install_hint("semantic")
        self.assertTrue(hint.startswith("pipx install --force 'pkb-cli[semantic] @ git+https://"), hint)
        self.assertTrue(hint.endswith("@v1.2.3'"), hint)
        with mock.patch.object(updater, "_installer", return_value=None), mock.patch.object(updater, "__version__", "0.0.0+unknown"):
            self.assertTrue(updater.install_hint("semantic").startswith("uv tool install --force "))
            self.assertIn("@main'", updater.install_hint("semantic"))

    def test_launchd_load_retries_a_bootstrap_that_races_the_bootout(self):
        # launchctl bootout returns before the old job is gone; bootstrap then fails with exit 5
        results = [mock.Mock(returncode=0, stderr="")]                       # bootout
        results += [mock.Mock(returncode=5, stderr="Input/output error")] * 2  # raced twice
        results += [mock.Mock(returncode=0, stderr="")]                       # then fine
        with mock.patch.object(cli.subprocess, "run", side_effect=results) as run, \
                mock.patch.object(service.time, "sleep") as sleep:
            self.assertIsNone(service._launchd_load("dev.pkb-cli.web", "/p.plist"))
        self.assertEqual(run.call_count, 4)
        self.assertEqual(sleep.call_count, 2)

    def test_launchd_load_gives_up_with_the_error_after_repeated_failures(self):
        failing = mock.Mock(returncode=5, stderr="Input/output error")
        with mock.patch.object(cli.subprocess, "run", side_effect=[mock.Mock(returncode=0)] + [failing] * 5) as run, \
                mock.patch.object(service.time, "sleep"):
            self.assertEqual(service._launchd_load("dev.pkb-cli.web", "/p.plist"), "Input/output error")
        self.assertEqual(run.call_count, 6)

    def test_service_refresh_repoints_stale_units_and_keeps_their_args(self):
        import plistlib
        old_launchers = {
            "dev.pkb-cli.web": ["/usr/bin/python3", "/old/checkout/scripts/kb", "web", "--port", "4173", "--no-open"],
            "dev.pkb-cli.sync": [sys.executable, "-m", "pkb_cli", "sync"],
        }
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(service, "_launchd_load", return_value=None) as load:
            agents = os.path.join(home, "Library", "LaunchAgents")
            os.makedirs(agents)
            for label, argv in old_launchers.items():
                with open(os.path.join(agents, f"{label}.plist"), "wb") as f:
                    plistlib.dump({"Label": label, "ProgramArguments": argv, "WorkingDirectory": "/data"}, f)
            self.assertEqual(service._service_refresh("launchd"), 0)
            got = {}
            for label in old_launchers:
                with open(os.path.join(agents, f"{label}.plist"), "rb") as f:
                    got[label] = plistlib.load(f)
        self.assertEqual(got["dev.pkb-cli.web"]["ProgramArguments"],
                         proc.kb_cmd("web", "--port", "4173", "--no-open"))
        self.assertEqual(got["dev.pkb-cli.sync"]["ProgramArguments"], proc.kb_cmd("sync"))
        self.assertEqual(got["dev.pkb-cli.web"]["WorkingDirectory"], "/data")
        self.assertEqual(load.call_count, 2)


    def test_update_info_release_install(self):
        with mock.patch.object(updater, "_checkout_root", return_value=None), \
                mock.patch.object(updater, "_installer", return_value="pipx"), \
                mock.patch.object(updater, "_latest_release_version", return_value="999.0.0"):
            info = updater.update_info()
            self.assertEqual((info["mode"], info["update_available"], info["can_update"]), ("release", True, True))
            self.assertEqual(info["command"].split()[:2], ["pipx", "install"])
            self.assertTrue(info["command"].endswith("@v999.0.0"))
        with mock.patch.object(updater, "_checkout_root", return_value=None), \
                mock.patch.object(updater, "__version__", "1.0.0"), \
                mock.patch.object(updater, "_latest_release_version", return_value="1.0.0"):
            self.assertFalse(updater.update_info()["update_available"])

    def test_update_info_reports_offline_as_error_not_exception(self):
        with mock.patch.object(updater, "_checkout_root", return_value=None), \
                mock.patch.object(updater, "_latest_release_version", side_effect=OSError("offline")):
            info = updater.update_info()
        self.assertIn("offline", info["error"])
        self.assertFalse(info["can_update"])

    def _release_update(self, **kw):
        with mock.patch.object(updater, "_checkout_root", return_value=None), \
                mock.patch.object(updater, "_installer", return_value="uv"), \
                mock.patch.object(updater, "_latest_release_version", return_value="999.0.0"), \
                mock.patch.object(service, "service_kind", return_value="launchd"), \
                mock.patch.object(cli.shutil, "which", return_value="/bin/kb"), \
                mock.patch.object(proc, "run_captured", return_value=0) as captured, \
                mock.patch.object(cli.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            result = updater.apply_update(**kw)
        return result, captured, run

    def test_apply_update_defers_service_refresh_and_captures_installer_output(self):
        result, captured, run = self._release_update(capture=True, defer_refresh=True)
        self.assertEqual(result, (0, True))
        captured.assert_called_once()  # just the installer -- no `kb service refresh`
        run.assert_not_called()

    def test_apply_update_refreshes_services_by_default(self):
        result, _captured, run = self._release_update()
        self.assertEqual(result, (0, True))
        self.assertEqual([c.args[0] for c in run.call_args_list][-1][-2:], ["service", "refresh"])

    def test_apply_update_check_only_installs_nothing(self):
        result, captured, run = self._release_update(check=True)
        self.assertEqual(result, (1, False))
        captured.assert_not_called()
        run.assert_not_called()

    def test_schedule_kb_command_runs_as_separate_job_on_launchd(self):
        with mock.patch.object(cli.sys, "platform", "darwin"), \
                mock.patch.object(cli.shutil, "which", side_effect=lambda n: f"/bin/{n}"), \
                mock.patch.object(cli.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                mock.patch.object(cli.subprocess, "Popen") as popen:
            updater.schedule_kb_command("service", "refresh")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["launchctl", "submit"])
        self.assertEqual(argv[-3:], ["/bin/kb", "service", "refresh"])
        popen.assert_not_called()

    def test_schedule_kb_command_falls_back_to_new_session(self):
        with mock.patch.object(cli.sys, "platform", "darwin"), \
                mock.patch.object(cli.shutil, "which", return_value=None), \
                mock.patch.object(cli.subprocess, "Popen") as popen:
            updater.schedule_kb_command("service", "refresh")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.args[0][-5:], [cli.sys.executable, "-m", "pkb_cli", "service", "refresh"])

    def _git_checkout(self, behind=2, dirty=False, upstream="origin/main"):
        status = {"is_git": True, "upstream": upstream, "local": "a" * 40, "remote": "b" * 40,
                  "behind": behind, "dirty": dirty}
        return (mock.patch.object(updater, "_checkout_root", return_value="/checkout"),
                mock.patch.object(updater, "_git", return_value=mock.Mock(returncode=0, stdout="pulled", stderr="")),
                mock.patch.object(updater, "update_status", return_value=status))

    def _run_git_update(self, **kw):
        from contextlib import ExitStack
        check = kw.pop("check", False)
        with ExitStack() as stack:
            patches = [stack.enter_context(p) for p in self._git_checkout(**kw)]
            git = patches[1]
            result = updater.apply_update(check=check)
            info = updater.update_info()
        return result, info, git

    def test_git_update_pulls_when_behind(self):
        result, info, git = self._run_git_update()
        self.assertEqual(result, (0, True))
        self.assertTrue(any(c.args[:2] == ("pull", "--ff-only") for c in git.call_args_list))
        self.assertEqual((info["mode"], info["update_available"], info["can_update"]), ("git", True, True))

    def test_git_update_refuses_dirty_checkout_and_info_agrees(self):
        result, info, git = self._run_git_update(dirty=True)
        self.assertEqual(result, (1, False))
        self.assertFalse(any(c.args[:1] == ("pull",) for c in git.call_args_list))
        self.assertEqual((info["update_available"], info["can_update"]), (True, False))
        self.assertIn("local changes", info["error"])

    def test_git_update_check_reports_behind_without_pulling(self):
        result, _info, git = self._run_git_update(check=True)
        self.assertEqual(result, (1, False))
        self.assertFalse(any(c.args[:1] == ("pull",) for c in git.call_args_list))

    def test_git_update_up_to_date(self):
        result, info, _git = self._run_git_update(behind=0)
        self.assertEqual(result, (0, False))
        self.assertEqual((info["update_available"], info["can_update"]), (False, False))

    def test_release_update_without_known_installer_is_blocked_everywhere(self):
        with mock.patch.object(updater, "_checkout_root", return_value=None), \
                mock.patch.object(updater, "_installer", return_value=None), \
                mock.patch.object(updater, "_latest_release_version", return_value="999.0.0"):
            info = updater.update_info()
            rc = updater.apply_update()
        self.assertEqual((info["update_available"], info["can_update"], rc), (True, False, (1, False)))
        self.assertIn("can't tell how kb was installed", info["error"])

    def test_service_info_unsupported_platform(self):
        with mock.patch.object(service, "service_kind", return_value=None):
            self.assertEqual(service.service_info(), {"supported": False, "kind": None})

    def test_service_info_launchd_reads_installed_state_and_interval(self):
        import plistlib
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(service, "service_kind", return_value="launchd"):
            agents = os.path.join(home, "Library", "LaunchAgents")
            os.makedirs(agents)
            with open(os.path.join(agents, "dev.pkb-cli.sync.plist"), "wb") as f:
                plistlib.dump({"Label": "dev.pkb-cli.sync", "StartInterval": 900}, f)
            with mock.patch.object(service, "_launchd_status", side_effect=lambda label, path: (
                    (os.path.exists(path), os.path.exists(path), None) if os.path.exists(path)
                    else (False, False, "not installed"))):
                info = service.service_info()
        self.assertEqual(info["web"], {"installed": False, "running": False, "detail": "not installed"})
        self.assertEqual((info["sync"]["installed"], info["sync"]["interval_minutes"]), (True, 15))


if __name__ == "__main__":
    unittest.main()
