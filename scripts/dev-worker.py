#!/usr/bin/env python3
"""Preview the phone page (pkb_cli/templates/worker/public) with fake TODOs.

Serves the real files and fakes the Worker's API, so no Cloudflare account, token or
push key is needed. Connect with the token `demo`. Done/Snooze are accepted but do
nothing, and turning notifications on fails (the fake push key isn't real).
  KB_DEV_PORT   port to bind   (default: 4199 is kb web's, so this uses 4198)
"""
import functools
import http.server
import json
import os
import time

PUBLIC = os.path.join(os.path.dirname(__file__), "..", "pkb_cli", "templates", "worker", "public")
TOKEN = "demo"
HOUR = 3600


def todos():
    """Fresh each call so the groups (overdue, today, ...) stay right whenever you look."""
    n = int(time.time())
    return [
        {"id": "kb-qjp", "title": "Renew passport", "priority": 0, "due_at": n - 26 * HOUR},
        {"id": "kb-a1x", "title": "Reply to landlord about the lease", "priority": 1, "due_at": n - 2 * HOUR},
        {"id": "kb-b22", "title": "Review PR for worker redesign", "priority": 2, "due_at": n + 2 * HOUR},
        {"id": "kb-c33", "title": "Buy oat milk", "priority": 3, "due_at": n + 5 * HOUR},
        {"id": "kb-d44", "title": "Dentist appointment", "priority": 1, "due_at": n + 30 * HOUR},
        {"id": "kb-e55", "title": "Write quarterly notes", "priority": 2, "due_at": n + 4 * 86400},
        {"id": "kb-f66", "title": "Plan the autumn trip", "priority": 3, "due_at": n + 20 * 86400},
    ]


class Handler(http.server.SimpleHTTPRequestHandler):
    def reply(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/todos":
            if self.headers.get("authorization") != f"Bearer {TOKEN}":
                return self.reply({"error": "unauthorized"}, 401)
            return self.reply({"todos": todos(), "now": int(time.time())})
        if self.path == "/api/vapid-key":
            return self.reply({"publicKey": "AAAA"})
        super().do_GET()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self.reply({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("KB_DEV_PORT", "4198"))
    handler = functools.partial(Handler, directory=PUBLIC)
    print(f"phone page at http://localhost:{port}  (token: {TOKEN})")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
