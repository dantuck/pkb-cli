"""Tests for `kb remind push deploy` (pkb_cli/worker_deploy.py): the order of wrangler
calls, that secrets only travel in a private temp file, first-time vs update, and the
offer `kb update` makes. wrangler, Node and the network are all faked."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from pkb_cli import remind_push as rp
from pkb_cli import worker_deploy as wd

DB_ID = "0a1b2c3d-0000-4000-8000-123456789abc"
KEYS = {"VAPID_PUBLIC_KEY": "pub", "VAPID_PRIVATE_KEY": "priv", "SYNC_TOKEN": "sync-tok", "SUBSCRIBE_TOKEN": "sub-tok"}


class DeployTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        env = {"XDG_DATA_HOME": os.path.join(tmp, "data"), "KB_CONFIG_DIR": os.path.join(tmp, "cfg")}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("KB_PUSH_URL", "KB_PUSH_TOKEN"):
            os.environ.pop(name, None)
        self.dest = rp.worker_dir()
        self.calls = []            # every wrangler/npm/node call, minus the `cd` wrapper
        self.secrets_seen = None   # the --secrets-file contents at deploy time
        self.secrets_path = None
        self.databases = []        # what `wrangler d1 list` reports
        self.logged_in = True
        self.interactive = True
        self.answers = []          # replies to y/N questions
        self.questions = []

    def run_deploy(self, ask=False):
        def stream(argv):
            self.calls.append(argv[4:])
            if argv[4:7] == ["pnpm", "exec", "wrangler"] and argv[7:9] == ["d1", "create"]:
                self.databases = [{"name": wd.DB_NAME, "uuid": DB_ID}]
            return 0

        def capture(argv):
            args = argv[4:]
            self.calls.append(args)
            if "whoami" in args:
                return (0 if self.logged_in else 1), "{}"
            if "list" in args:
                return 0, "⛅️ wrangler 4\n" + json.dumps(self.databases)
            return 0, "".join(f"{k}={v}\n" for k, v in KEYS.items())

        def tee(argv):
            args = argv[4:]
            self.calls.append(args)
            if "--secrets-file" in args:
                self.secrets_path = args[args.index("--secrets-file") + 1]
                with open(self.secrets_path) as f:
                    self.secrets_seen = json.load(f)
            return 0, "Deployed kb-push\n  https://kb-push.me.workers.dev\n"

        def confirm(question, default=False):
            self.questions.append(question)
            return self.answers.pop(0)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(wd, "_tools", return_value=(["pnpm", "install"], ["pnpm", "exec", "wrangler"])), \
                mock.patch.object(wd, "_stream", stream), mock.patch.object(wd, "_capture", capture), \
                mock.patch.object(wd, "_tee", tee), mock.patch.object(wd.pc, "confirm", confirm), \
                mock.patch.object(wd.pc, "is_interactive", return_value=self.interactive), \
                mock.patch.object(rp, "verify", return_value=(True, "", {})), \
                mock.patch.object(rp, "status", return_value=(True, "phone reminders: 1 phone(s) subscribed")), \
                mock.patch.object(wd.time, "sleep"), \
                redirect_stdout(out), redirect_stderr(err):
            rc = wd.deploy(ask=ask)
        self.out, self.err = out.getvalue(), err.getvalue()
        return rc

    def wrangler_calls(self):
        return [c[3:] for c in self.calls if c[:3] == ["pnpm", "exec", "wrangler"]]

    # --- first time ---

    def test_first_deploy_runs_the_steps_in_order_and_saves_the_config(self):
        self.assertEqual(self.run_deploy(), 0, self.err)
        steps = self.wrangler_calls()
        self.assertEqual([s[0] for s in steps],
                         ["whoami", "d1", "d1", "d1", "d1", "deploy"])   # whoami, list, create, list, migrate, deploy
        self.assertEqual(steps[2][:2], ["d1", "create"])
        self.assertEqual(steps[4], ["d1", "migrations", "apply", wd.DB_NAME, "--remote"])
        self.assertEqual(rp.get_config(), ("https://kb-push.me.workers.dev", "sync-tok"))
        self.assertEqual(rp.get_database_id(os.path.join(self.dest, "wrangler.jsonc")), DB_ID)
        self.assertTrue(wd._is_current(self.dest))

    def test_secrets_travel_only_in_a_temp_file_that_is_deleted(self):
        self.run_deploy()
        self.assertEqual(self.secrets_seen, KEYS)
        self.assertFalse(os.path.exists(self.secrets_path))
        argv = " ".join(" ".join(c) for c in self.calls)
        for value in KEYS.values():
            self.assertNotIn(value, argv)

    def test_the_subscribe_token_is_shown_once_and_the_sync_token_never(self):
        self.run_deploy()
        self.assertIn("sub-tok", self.out)
        self.assertNotIn("sync-tok", self.out + self.err)

    def test_an_existing_database_is_reused_not_recreated(self):
        self.databases = [{"name": wd.DB_NAME, "uuid": DB_ID}]
        self.run_deploy()
        self.assertFalse(any(s[:2] == ["d1", "create"] for s in self.wrangler_calls()))
        self.assertEqual(rp.get_database_id(os.path.join(self.dest, "wrangler.jsonc")), DB_ID)

    def test_not_logged_in_without_a_terminal_stops_before_changing_anything(self):
        self.logged_in = False
        self.interactive = False
        self.assertEqual(self.run_deploy(), 1)
        self.assertIn("not logged in", self.err)
        self.assertFalse(any(s[0] in ("deploy", "d1") for s in self.wrangler_calls()))
        self.assertIsNone(rp.get_config())

    def test_a_reformatted_wrangler_config_stops_the_deploy_with_instructions(self):
        self.databases = [{"name": wd.DB_NAME, "uuid": DB_ID}]
        real_init = rp.init_worker

        def init_then_reformat(dest=None):
            path = real_init(dest)
            config = os.path.join(path, "wrangler.jsonc")
            with open(config) as f:
                text = f.read()
            with open(config, "w") as f:
                f.write(text.replace(rp._DB_NAME_LINE, '"database_name": "other",'))
            return path

        with mock.patch.object(rp, "init_worker", init_then_reformat):
            self.assertEqual(self.run_deploy(), 1)
        self.assertIn("database_id", self.err)
        self.assertFalse(any(s[0] == "deploy" for s in self.wrangler_calls()))

    def test_a_missing_node_is_a_clear_error(self):
        with mock.patch.object(wd.shutil, "which", return_value=None), \
                redirect_stderr(io.StringIO()) as err:
            self.assertEqual(wd.deploy(), 1)
        self.assertIn("Node.js", err.getvalue())

    # --- updates ---

    def configure(self):
        rp.save_config("https://kb-push.me.workers.dev", "sync-tok")

    def test_an_update_redeploys_without_new_secrets_or_a_new_config(self):
        self.configure()
        self.assertEqual(self.run_deploy(), 0, self.err)
        deploy = [s for s in self.wrangler_calls() if s[0] == "deploy"]
        self.assertEqual(deploy, [["deploy"]])
        self.assertIsNone(self.secrets_seen)
        self.assertEqual(rp.get_config(), ("https://kb-push.me.workers.dev", "sync-tok"))
        self.assertNotIn("subscribe token", self.out)

    def test_from_update_skips_when_the_deployed_source_is_current(self):
        self.configure()
        os.makedirs(self.dest)
        wd._mark_deployed(self.dest)
        self.assertEqual(self.run_deploy(ask=True), 0)
        self.assertEqual(self.calls, [])

    def test_from_update_redeploys_when_the_source_changed(self):
        self.configure()
        os.makedirs(self.dest)
        with open(os.path.join(self.dest, wd.DEPLOYED_FILE), "w") as f:
            f.write("an older hash\n")
        self.assertEqual(self.run_deploy(ask=True), 0)
        self.assertTrue(any(s[0] == "deploy" for s in self.wrangler_calls()))
        self.assertIn("new version", self.out)
        self.assertTrue(wd._is_current(self.dest))

    # --- the offer ---

    def test_from_update_offers_setup_and_does_it_on_yes(self):
        self.answers = [True]
        self.assertEqual(self.run_deploy(ask=True), 0, self.err)
        self.assertEqual(len(self.questions), 1)
        self.assertIsNotNone(rp.get_config())

    def test_a_no_is_remembered_so_update_asks_only_once(self):
        self.answers = [False]
        self.run_deploy(ask=True)
        self.assertEqual(self.calls, [])
        self.assertTrue(rp.offer_declined())
        self.run_deploy(ask=True)
        self.assertEqual(len(self.questions), 1)

    def test_the_explicit_command_sets_up_without_asking_even_after_a_no(self):
        rp.decline_offer()
        self.assertEqual(self.run_deploy(), 0, self.err)
        self.assertEqual(self.questions, [])
        self.assertIsNotNone(rp.get_config())


class PwaAssetsTest(unittest.TestCase):
    """Every image the phone page, manifest and notifications name is actually shipped."""
    PUBLIC = os.path.join(rp.WORKER_SRC, "public")

    def read(self, name):
        with open(os.path.join(self.PUBLIC, name), encoding="utf-8") as f:
            return f.read()

    def assertShipped(self, path):
        self.assertTrue(os.path.isfile(os.path.join(self.PUBLIC, path.lstrip("/"))), path)

    def test_notification_icon_and_badge_exist(self):
        import re
        for path in re.findall(r'(?:icon|badge): "(/[^"]+)"', self.read("sw.js")):
            self.assertShipped(path)

    def test_manifest_icons_exist_and_include_a_maskable_one(self):
        icons = json.loads(self.read("manifest.webmanifest"))["icons"]
        for icon in icons:
            self.assertShipped(icon["src"])
        self.assertIn("maskable", {i["purpose"] for i in icons})
        self.assertIn("192x192", {i["sizes"] for i in icons})

    def test_page_icon_exists(self):
        import re
        self.assertShipped(re.search(r'rel="icon" href="([^"]+)"', self.read("index.html")).group(1))

    def test_offline_shell_files_are_shipped(self):
        # cache.addAll is all-or-nothing: one missing file fails the service worker install
        import re
        files = re.search(r"const FILES = \[([^\]]*)\]", self.read("sw.js")).group(1)
        for path in re.findall(r'"(/[^"]*)"', files):
            self.assertShipped("index.html" if path == "/" else path)

    def test_the_badge_is_a_small_png_with_an_alpha_channel(self):
        # Android uses only a badge's alpha channel, so it must have one (PNG colour type 6, RGBA)
        import struct
        with open(os.path.join(self.PUBLIC, "badge.png"), "rb") as f:
            header = f.read(26)
        self.assertEqual(header[:8], b"\x89PNG\r\n\x1a\n")
        width, height, _depth, colour_type = struct.unpack(">IIBB", header[16:26])
        self.assertEqual((width, height, colour_type), (96, 96, 6))


class UpdateFlowTest(unittest.TestCase):
    """What `kb update` does about the Worker, from the freshly installed kb."""

    def run_refresh(self, capture=False, defer=False, tty=True, configured=False):
        from pkb_cli import cli as kb_cli
        opts = kb_cli._UpdateOpts(False, capture, defer)
        out = io.StringIO()
        with mock.patch.object(wd.pc, "is_interactive", return_value=tty), \
                mock.patch.object(kb_cli, "installed_kb_argv", side_effect=lambda *a: ["kb", *a]), \
                mock.patch.object(kb_cli.remind_push, "get_config", return_value=("u", "t") if configured else None), \
                mock.patch.object(kb_cli, "_run_update_step") as step, redirect_stdout(out):
            kb_cli._refresh_worker_after_update(opts)
        return step, out.getvalue()

    def test_in_a_terminal_it_runs_deploy_in_ask_mode(self):
        step, _ = self.run_refresh()
        step.assert_called_once_with(["kb", "remind", "push", "deploy", "--ask"], mock.ANY)

    def test_without_a_terminal_it_only_says_what_to_do(self):
        for kwargs in ({"capture": True}, {"defer": True}, {"tty": False}):
            step, out = self.run_refresh(configured=True, **kwargs)
            step.assert_not_called()
            self.assertIn("kb remind push deploy", out)

    def test_without_a_terminal_and_no_worker_it_stays_silent(self):
        step, out = self.run_refresh(tty=False)
        step.assert_not_called()
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
