"""End-to-end tests for kb web: starts the real server on an ephemeral
localhost port against a throwaway data repo and talks to it over HTTP."""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock


from pkb_cli import kb_web  # noqa: E402
from pkb_cli import pkb_common as pc  # noqa: E402
from pkb_cli import pkb_entries  # noqa: E402


class KbWebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.realpath(cls._tmp.name)
        for d in list(pc.TYPE_DIR.values()) + ["inbox", "journal", "sources/memos", ".pkb"]:
            os.makedirs(os.path.join(cls.root, d), exist_ok=True)
        # no FTS database in the throwaway repo; reindexing isn't under test here
        cls._patch = mock.patch.object(pkb_entries, "_reindex_fast", lambda root: None)
        cls._patch.start()
        with open(os.path.join(cls.root, ".pkb", "secret.txt"), "w") as f:
            f.write("nope")
        with open(os.path.join(cls.root, "sources", "memos", "clip.bin"), "wb") as f:
            f.write(bytes(range(100)))
        kb_web.Handler.root = cls.root
        cls.httpd = kb_web.Server(("127.0.0.1", 0), kb_web.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._patch.stop()
        cls._tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers=headers or {})
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, resp.getheaders(), raw

    def json(self, method, path, body=None):
        status, _, raw = self.request(method, path, body)
        return status, json.loads(raw)

    def test_health(self):
        self.assertEqual(self.json("GET", "/api/health"), (200, {"ok": True, "root": self.root}))

    def test_create_entry_validates_and_roundtrips(self):
        self.assertEqual(self.json("POST", "/api/entries", {"type": "how-to", "title": " "})[0], 400)
        self.assertEqual(self.json("POST", "/api/entries", {"type": "bogus", "title": "T"})[0], 400)
        status, created = self.json("POST", "/api/entries", {"type": "how-to", "title": "Web made", "tags": ["a"]})
        self.assertEqual(status, 201)
        status, shown = self.json("GET", f"/api/entries/{created['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(shown["frontmatter"]["tags"], ["a"])
        self.assertIsNone(shown["readonly"])
        status, tags = self.json("PATCH", f"/api/entries/{created['id']}/tags", {"add": ["b"]})
        self.assertEqual((status, tags), (200, {"tags": ["a", "b"]}))

    def test_unknown_entry_is_404(self):
        self.assertEqual(self.json("GET", "/api/entries/nope")[0], 404)
        self.assertEqual(self.json("PATCH", "/api/entries/nope/tags", {"add": ["x"]})[0], 404)

    def test_readonly_mirror_rejects_edits_with_409(self):
        path = os.path.join(self.root, "sources", "memos", "m1.md")
        fm = {"id": "20260101-0000", "created": pc.now_iso(), "updated": pc.now_iso(), "type": "source",
              "extension": "source", "source": "memos", "source_id": "1", "tags": [], "links": [], "title": "m"}
        pc.write_entry_readonly(path, fm, "body\n\n[Memo](https://memos.example/memos/1)\n")
        status, shown = self.json("GET", "/api/entries/20260101-0000")
        self.assertEqual(shown["readonly"], {"label": "Memo", "url": "https://memos.example/memos/1"})
        status, err = self.json("PATCH", "/api/entries/20260101-0000/tags", {"add": ["x"]})
        self.assertEqual(status, 409)
        self.assertTrue(err["readonly"])

    def test_unknown_route_is_404(self):
        self.assertEqual(self.json("POST", "/api/nope", {})[0], 404)

    def test_static_serving_blocks_traversal_and_dot_pkb(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/.pkb/secret.txt")[0], 404)
        self.assertEqual(self.request("GET", "/sources/../.pkb/secret.txt")[0], 404)
        self.assertEqual(self.request("GET", "/sources/%2e%2e/.pkb/secret.txt")[0], 404)
        self.assertEqual(self.request("GET", "/../../etc/passwd")[0], 404)

    def test_range_requests(self):
        status, headers, raw = self.request("GET", "/sources/memos/clip.bin", headers={"Range": "bytes=10-19"})
        self.assertEqual(status, 206)
        self.assertEqual(raw, bytes(range(10, 20)))
        self.assertEqual(dict(headers)["Content-Range"], "bytes 10-19/100")
        status, _, raw = self.request("GET", "/sources/memos/clip.bin", headers={"Range": "bytes=-5"})
        self.assertEqual((status, raw), (206, bytes(range(95, 100))))
        status, _, raw = self.request("GET", "/sources/memos/clip.bin")
        self.assertEqual((status, len(raw)), (200, 100))

    def test_serve_on_busy_port_opens_existing_instance(self):
        with mock.patch.object(kb_web.webbrowser, "open") as opened:
            rc = kb_web.serve(self.root, self.port, open_browser=True)
        self.assertEqual(rc, 0)
        opened.assert_called_once_with(f"http://127.0.0.1:{self.port}/")

    def test_serve_on_port_held_by_something_else_fails(self):
        import socket
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            with mock.patch.object(kb_web.webbrowser, "open") as opened:
                rc = kb_web.serve(self.root, sock.getsockname()[1], open_browser=True)
        self.assertEqual(rc, 1)
        opened.assert_not_called()

    # ---------- admin ----------

    def test_admin_status_reports_version_and_sources(self):
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.push_status.return_value = {"is_git": False, "upstream": None, "ahead": None, "behind": None}
            kb.return_value.doctor_checks.return_value = (["fine"], [])
            kb.return_value.inbox_list.return_value = []
            kb.return_value.bd_available.return_value = False
            kb.return_value.discover_sync_sources.return_value = {"memos": ("sync_memos.py", {})}
            kb.return_value.__version__ = "9.9.9"
            status, data = self.json("GET", "/api/admin/status")
        self.assertEqual(status, 200)
        self.assertEqual((data["version"], data["sources"], data["busy"], data["root"]),
                         ("9.9.9", ["memos"], False, self.root))

    def test_mutating_requests_refuse_cross_origin(self):
        for headers in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}):
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            conn.request("POST", "/api/admin/setup", body="{}", headers=headers)
            resp = conn.getresponse()
            resp.read()
            conn.close()
            self.assertEqual(resp.status, 403, headers)

    def test_same_origin_post_is_allowed(self):
        headers = {"Origin": f"http://127.0.0.1:{self.port}"}
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.cmd_validate.return_value = 0
            status, _, raw = self.request("POST", "/api/validate", {}, headers)
        self.assertEqual((status, json.loads(raw)["ok"]), (200, True))

    def test_admin_task_returns_output_and_failure(self):
        def noisy(args):
            print("checked 3 files")
            return 1
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.cmd_validate = noisy
            status, data = self.json("POST", "/api/validate", {})
        self.assertEqual((status, data), (200, {"ok": False, "output": "checked 3 files"}))

    def test_admin_task_reports_system_exit_instead_of_hanging(self):
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.cmd_validate.side_effect = SystemExit("no pkb data repo found")
            status, data = self.json("POST", "/api/validate", {})
        self.assertEqual((status, data["ok"]), (500, False))
        self.assertIn("no pkb data repo", data["error"])
        self.assertFalse(kb_web._ADMIN_LOCK.locked())

    def test_admin_tasks_do_not_overlap(self):
        self.assertTrue(kb_web._ADMIN_LOCK.acquire(blocking=False))
        try:
            status, data = self.json("POST", "/api/validate", {})
        finally:
            kb_web._ADMIN_LOCK.release()
        self.assertEqual(status, 409)
        self.assertIn("still running", data["error"])

    def test_journal_months_lists_only_months_with_entries_newest_first(self):
        with tempfile.TemporaryDirectory() as root:
            for rel in ("2026/07/2026-07-02.md", "2026/09/2026-09-01.md", "2025/12/2025-12-30.md", "2026/08/notes.txt"):
                path = os.path.join(root, "journal", rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").close()
            from pkb_cli import cli
            self.assertEqual(cli.journal_months(root), ["2026-09", "2026-07", "2025-12"])
            self.assertEqual(cli.journal_months(os.path.join(root, "nowhere")), [])

    def test_journal_months_endpoint(self):
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.journal_months.return_value = ["2026-09"]
            self.assertEqual(self.json("GET", "/api/admin/journal-months"), (200, {"months": ["2026-09"]}))

    def test_rollup_rejects_bad_month(self):
        status, data = self.json("POST", "/api/admin/rollup", {"month": "July"})
        self.assertEqual(status, 400)

    def test_rollup_passes_month_through(self):
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value._journal_rollup.return_value = 0
            status, data = self.json("POST", "/api/admin/rollup", {"month": "2026-07"})
        self.assertEqual((status, data["ok"]), (200, True))
        kb.return_value._journal_rollup.assert_called_once_with(self.root, "2026-07")

    def test_setup_runs_non_interactively(self):
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.cmd_setup.return_value = 0
            status, data = self.json("POST", "/api/admin/setup", {})
        self.assertEqual((status, data["ok"]), (200, True))
        self.assertTrue(kb.return_value.cmd_setup.call_args.args[0].yes)

    def test_update_check_returns_structured_info(self):
        info = {"mode": "release", "current": "0.2.0", "latest": "0.3.0", "update_available": True}
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.update_info.return_value = info
            status, data = self.json("GET", "/api/admin/update")
        self.assertEqual((status, data), (200, info))

    def test_update_defers_service_refresh_until_after_response(self):
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.apply_update.return_value = (0, True)
            kb.return_value.service_info.return_value = {
                "supported": True, "web": {"installed": True}, "sync": {"installed": False}}
            status, data = self.json("POST", "/api/admin/update", {})
        self.assertEqual((status, data["ok"], data["restart_scheduled"]), (200, True, True))
        kb.return_value.apply_update.assert_called_once_with(capture=True, defer_refresh=True)
        kb.return_value.schedule_kb_command.assert_called_once_with("service", "refresh")

    def test_update_schedules_nothing_without_services_or_new_code(self):
        for updated, web_installed in ((True, False), (False, True)):
            with mock.patch.object(kb_web, "_kb") as kb:
                kb.return_value.apply_update.return_value = (0, updated)
                kb.return_value.service_info.return_value = {
                    "supported": True, "web": {"installed": web_installed}, "sync": {"installed": False}}
                _, data = self.json("POST", "/api/admin/update", {})
            self.assertFalse(data["restart_scheduled"], (updated, web_installed))
            kb.return_value.schedule_kb_command.assert_not_called()

    def test_concurrent_captures_do_not_steal_each_other_output(self):
        barrier = threading.Barrier(2)
        results = {}

        def task(name):
            def run():
                barrier.wait()
                for _ in range(200):
                    print(name)
                return 0
            results[name] = kb_web._capture_stdout(run)[1]

        threads = [threading.Thread(target=task, args=(n,)) for n in ("a", "b")]
        real = sys.stdout
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIs(sys.stdout, real)
        for name in ("a", "b"):
            self.assertEqual(set(results[name].split("\n")), {name})

    def _services(self, web_installed=True, supported=True):
        return {"supported": supported, "kind": "launchd",
                "web": {"installed": web_installed, "running": web_installed, "detail": None},
                "sync": {"installed": False, "running": False, "detail": None}}

    def test_service_action_validation(self):
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.service_info.return_value = self._services()
            self.assertEqual(self.json("POST", "/api/admin/services/db/install", {})[0], 404)
            self.assertEqual(self.json("POST", "/api/admin/services/web/install", {})[0], 400)
            self.assertEqual(self.json("POST", "/api/admin/services/sync/install", {"interval_minutes": 0})[0], 400)
            self.assertEqual(self.json("POST", "/api/admin/services/sync/install", {"interval_minutes": "x"})[0], 400)
            self.assertEqual(self.json("POST", "/api/admin/services/sync/refresh", {})[0], 400)
            kb.return_value.service_info.return_value = self._services(web_installed=False)
            self.assertEqual(self.json("POST", "/api/admin/services/web/refresh", {})[0], 400)
            kb.return_value.service_info.return_value = self._services()
            kb.return_value.service_info.return_value = self._services(supported=False)
            self.assertEqual(self.json("POST", "/api/admin/services/sync/uninstall", {})[0], 400)

    def test_sync_service_install_runs_inline_for_this_repo(self):
        with mock.patch.object(kb_web, "_kb") as kb:
            kb.return_value.service_info.return_value = self._services()
            kb.return_value.cmd_sync_service.return_value = 0
            status, data = self.json("POST", "/api/admin/services/sync/install", {"interval_minutes": 15})
        self.assertEqual((status, data["ok"]), (200, True))
        args = kb.return_value.cmd_sync_service.call_args.args[0]
        self.assertEqual((args.action, args.interval_minutes, args.repo), ("install", 15, self.root))

    def test_web_service_restart_and_uninstall_are_detached(self):
        for action in ("refresh", "uninstall"):
            with mock.patch.object(kb_web, "_kb") as kb:
                kb.return_value.service_info.return_value = self._services()
                status, data = self.json("POST", f"/api/admin/services/web/{action}", {})
            self.assertEqual((status, data["restart_scheduled"]), (200, True), action)
            kb.return_value.schedule_kb_command.assert_called_once_with("service", action)

    def test_editor_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as cfg_dir, \
                mock.patch.dict(os.environ, {"KB_CONFIG_DIR": cfg_dir}):
            os.environ.pop("EDITOR", None)
            self.assertEqual(self.json("GET", "/api/admin/config")[1]["source"], None)
            status, data = self.json("PATCH", "/api/admin/config", {"editor": "code --wait"})
            self.assertEqual((status, data["saved"], data["source"]), (200, "code --wait", "saved"))
            self.assertEqual(self.json("PATCH", "/api/admin/config", {"editor": "'unterminated"})[0], 400)
            self.assertEqual(self.json("GET", "/api/admin/config")[1]["saved"], "code --wait")
            status, data = self.json("PATCH", "/api/admin/config", {"editor": ""})
            self.assertEqual((status, data["saved"], data["source"]), (200, "", None))

    def test_make_snippet_strips_title_and_truncates(self):
        self.assertEqual(kb_web._make_snippet("# Title\n\nbody"), "body")
        out = kb_web._make_snippet("word " * 400, limit=50)
        self.assertTrue(out.endswith("…") and len(out) <= 52)


if __name__ == "__main__":
    unittest.main()
