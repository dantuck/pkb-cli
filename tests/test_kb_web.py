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

SCRIPT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
sys.path.insert(0, SCRIPT_DIR)

import kb_web  # noqa: E402
import pkb_common as pc  # noqa: E402
import pkb_entries  # noqa: E402


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

    def test_make_snippet_strips_title_and_truncates(self):
        self.assertEqual(kb_web._make_snippet("# Title\n\nbody"), "body")
        out = kb_web._make_snippet("word " * 400, limit=50)
        self.assertTrue(out.endswith("…") and len(out) <= 52)


if __name__ == "__main__":
    unittest.main()
