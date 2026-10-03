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


if __name__ == "__main__":
    unittest.main()
