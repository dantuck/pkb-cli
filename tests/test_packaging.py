"""Guards the installed-package layout: modules that run as subprocesses
(`python -m pkb_cli.<module>`), in-process imports of sibling modules, and
bundled data files all have to resolve the same way from a wheel as from a
source checkout."""
import importlib.resources
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pkb_cli import __version__, cli, pkb_common as pc, pkb_entries


class PackagingTest(unittest.TestCase):
    def test_python_dash_m_entry_point_runs(self):
        out = subprocess.run([sys.executable, "-m", "pkb_cli", "--version"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(__version__, out.stdout)

    def test_legacy_scripts_kb_shim_forwards_to_the_package(self):
        # old installs' `kb` symlink and service units point at scripts/kb
        shim = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "kb")
        with tempfile.TemporaryDirectory() as cwd:
            link = os.path.join(cwd, "kb")
            os.symlink(shim, link)
            out = subprocess.run([sys.executable, link, "--version"], cwd=cwd, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(__version__, out.stdout)

    def test_bundled_data_files_are_present(self):
        pkg = importlib.resources.files("pkb_cli")
        for rel in ("templates/schema.sql", "templates/web/index.html", "templates/web/vendor/alpine.min.js",
                    "skills/kb/SKILL.md"):
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
            self.assertIsNone(cli._installer())
            for marker, tool in (("uv-receipt.toml", "uv"), ("pipx_metadata.json", "pipx")):
                open(os.path.join(prefix, marker), "w").close()
                self.assertEqual(cli._installer(), tool)
                os.remove(os.path.join(prefix, marker))
        self.assertEqual(cli._install_command("uv", "v1.2.3")[:3], ["uv", "tool", "install"])
        self.assertEqual(cli._install_command("pipx", "v1.2.3")[:2], ["pipx", "install"])
        self.assertTrue(cli._install_command("uv", "v1.2.3")[-1].endswith("@v1.2.3"))

    def test_service_refresh_repoints_stale_units_and_keeps_their_args(self):
        import plistlib
        old_launchers = {
            "dev.pkb-cli.web": ["/usr/bin/python3", "/old/checkout/scripts/kb", "web", "--port", "4173", "--no-open"],
            "dev.pkb-cli.sync": [sys.executable, "-m", "pkb_cli", "sync"],
        }
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(cli, "_launchd_load", return_value=None) as load:
            agents = os.path.join(home, "Library", "LaunchAgents")
            os.makedirs(agents)
            for label, argv in old_launchers.items():
                with open(os.path.join(agents, f"{label}.plist"), "wb") as f:
                    plistlib.dump({"Label": label, "ProgramArguments": argv, "WorkingDirectory": "/data"}, f)
            self.assertEqual(cli._service_refresh("launchd"), 0)
            got = {}
            for label in old_launchers:
                with open(os.path.join(agents, f"{label}.plist"), "rb") as f:
                    got[label] = plistlib.load(f)
        self.assertEqual(got["dev.pkb-cli.web"]["ProgramArguments"],
                         cli._kb_cmd("web", "--port", "4173", "--no-open"))
        self.assertEqual(got["dev.pkb-cli.sync"]["ProgramArguments"], cli._kb_cmd("sync"))
        self.assertEqual(got["dev.pkb-cli.web"]["WorkingDirectory"], "/data")
        self.assertEqual(load.call_count, 2)


    def test_update_info_release_install(self):
        with mock.patch.object(cli, "LEGACY_VERSION_PATH", "/nonexistent"), \
                mock.patch.object(cli, "TOOL_ROOT", tempfile.gettempdir()), \
                mock.patch.object(cli, "_installer", return_value="pipx"), \
                mock.patch.object(cli, "_latest_release_version", return_value="999.0.0"):
            info = cli.update_info()
            self.assertEqual((info["mode"], info["update_available"], info["can_update"]), ("release", True, True))
            self.assertEqual(info["command"].split()[:2], ["pipx", "install"])
            self.assertTrue(info["command"].endswith("@v999.0.0"))
        with mock.patch.object(cli, "LEGACY_VERSION_PATH", "/nonexistent"), \
                mock.patch.object(cli, "TOOL_ROOT", tempfile.gettempdir()), \
                mock.patch.object(cli, "__version__", "1.0.0"), \
                mock.patch.object(cli, "_latest_release_version", return_value="1.0.0"):
            self.assertFalse(cli.update_info()["update_available"])

    def test_update_info_reports_offline_as_error_not_exception(self):
        with mock.patch.object(cli, "LEGACY_VERSION_PATH", "/nonexistent"), \
                mock.patch.object(cli, "TOOL_ROOT", tempfile.gettempdir()), \
                mock.patch.object(cli, "_latest_release_version", side_effect=OSError("offline")):
            info = cli.update_info()
        self.assertIn("offline", info["error"])
        self.assertFalse(info["can_update"])

    def _release_update(self, **kw):
        with mock.patch.object(cli, "LEGACY_VERSION_PATH", "/nonexistent"), \
                mock.patch.object(cli, "TOOL_ROOT", tempfile.gettempdir()), \
                mock.patch.object(cli, "_installer", return_value="uv"), \
                mock.patch.object(cli, "_latest_release_version", return_value="999.0.0"), \
                mock.patch.object(cli, "_service_kind", return_value="launchd"), \
                mock.patch.object(cli.shutil, "which", return_value="/bin/kb"), \
                mock.patch.object(cli, "_run_captured", return_value=0) as captured, \
                mock.patch.object(cli.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            result = cli.apply_update(**kw)
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
            cli.schedule_kb_command("service", "refresh")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["launchctl", "submit"])
        self.assertEqual(argv[-3:], ["/bin/kb", "service", "refresh"])
        popen.assert_not_called()

    def test_schedule_kb_command_falls_back_to_new_session(self):
        with mock.patch.object(cli.sys, "platform", "darwin"), \
                mock.patch.object(cli.shutil, "which", return_value=None), \
                mock.patch.object(cli.subprocess, "Popen") as popen:
            cli.schedule_kb_command("service", "refresh")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.args[0][-5:], [cli.sys.executable, "-m", "pkb_cli", "service", "refresh"])

    def _git_checkout(self, behind=2, dirty=False, upstream="origin/main"):
        status = {"is_git": True, "upstream": upstream, "local": "a" * 40, "remote": "b" * 40,
                  "behind": behind, "dirty": dirty}
        return (mock.patch.object(cli, "LEGACY_VERSION_PATH", "/nonexistent"),
                mock.patch.object(cli.os.path, "isdir", return_value=True),
                mock.patch.object(cli, "_git", return_value=mock.Mock(returncode=0, stdout="pulled", stderr="")),
                mock.patch.object(cli, "update_status", return_value=status))

    def _run_git_update(self, **kw):
        from contextlib import ExitStack
        check = kw.pop("check", False)
        with ExitStack() as stack:
            patches = [stack.enter_context(p) for p in self._git_checkout(**kw)]
            git = patches[2]
            result = cli.apply_update(check=check)
            info = cli.update_info()
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

    def test_legacy_install_migrates_with_available_installer_and_errors_without(self):
        base = (mock.patch.object(cli, "LEGACY_VERSION_PATH", __file__),
                mock.patch.object(cli, "_latest_release_version", return_value="9.9.9"))
        with base[0], base[1], mock.patch.object(cli.shutil, "which", side_effect=lambda t: t if t == "pipx" else None):
            plan = cli.plan_update()
        self.assertEqual((plan["mode"], plan["installer"], plan["can_update"]), ("legacy", "pipx", True))
        self.assertTrue(plan["command"][-1].endswith("@v9.9.9"))
        with base[0], base[1], mock.patch.object(cli.shutil, "which", return_value=None):
            plan = cli.plan_update()
            rc = cli.apply_update()
        self.assertEqual((plan["can_update"], rc), (False, (1, False)))
        self.assertIn("old tarball installer", plan["error"])

    def test_release_update_without_known_installer_is_blocked_everywhere(self):
        with mock.patch.object(cli, "LEGACY_VERSION_PATH", "/nonexistent"), \
                mock.patch.object(cli, "TOOL_ROOT", tempfile.gettempdir()), \
                mock.patch.object(cli, "_installer", return_value=None), \
                mock.patch.object(cli, "_latest_release_version", return_value="999.0.0"):
            info = cli.update_info()
            rc = cli.apply_update()
        self.assertEqual((info["update_available"], info["can_update"], rc), (True, False, (1, False)))
        self.assertIn("can't tell how kb was installed", info["error"])

    def test_service_info_unsupported_platform(self):
        with mock.patch.object(cli, "_service_kind", return_value=None):
            self.assertEqual(cli.service_info(), {"supported": False, "kind": None})

    def test_service_info_launchd_reads_installed_state_and_interval(self):
        import plistlib
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(cli, "_service_kind", return_value="launchd"):
            agents = os.path.join(home, "Library", "LaunchAgents")
            os.makedirs(agents)
            with open(os.path.join(agents, "dev.pkb-cli.sync.plist"), "wb") as f:
                plistlib.dump({"Label": "dev.pkb-cli.sync", "StartInterval": 900}, f)
            with mock.patch.object(cli, "_launchd_status", side_effect=lambda label, path: (
                    (os.path.exists(path), os.path.exists(path), None) if os.path.exists(path)
                    else (False, False, "not installed"))):
                info = cli.service_info()
        self.assertEqual(info["web"], {"installed": False, "running": False, "detail": "not installed"})
        self.assertEqual((info["sync"]["installed"], info["sync"]["interval_minutes"]), (True, 15))


if __name__ == "__main__":
    unittest.main()
