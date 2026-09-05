"""kb web -- local-only web UI server. Stdlib only (no Flask/etc.), matching the
rest of this tool's offline/portable philosophy. Runs in the foreground for as
long as the browser tab needs it -- same one-shot-per-invocation spirit as every
other kb command, just longer-lived. Binds 127.0.0.1 only; never listens on a
network-reachable interface.
"""
import contextlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import mimetypes
import os
import re
import sqlite3
import sys
import types
import webbrowser
from datetime import datetime
from urllib.parse import urlsplit, parse_qs

WEB_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "templates", "web")
)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

_kb_module = None


def _kb():
    """Lazily load scripts/kb (extensionless, so it isn't import-able by name)
    as a module, to reuse its logic (journal_append, etc.) instead of duplicating
    it here. Safe to load repeatedly -- kb's own `if __name__ == "__main__"`
    guard means importing it never runs main()."""
    global _kb_module
    if _kb_module is None:
        # kb has no .py suffix, so spec_from_file_location can't infer a loader
        # for it on its own -- build one explicitly instead.
        loader = importlib.machinery.SourceFileLoader("kb_cli", os.path.join(SCRIPT_DIR, "kb"))
        spec = importlib.util.spec_from_loader("kb_cli", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        _kb_module = mod
    return _kb_module

# [(method, compiled_path_regex, handler), ...]; handler(h, root, *path_params)
# writes its own response via h.send_json(...). Routes are added with @route as
# the API surface grows.
ROUTES = []


def route(method, path):
    """Register a handler for `method` + `path`. Segments wrapped in {name} are
    captured and passed to the handler as positional args in order, e.g.
    '/api/inbox/{item_id}/promote' -> handler(h, root, item_id)."""
    pattern = re.compile("^" + re.sub(r"{\w+}", r"([^/]+)", path) + "$")
    def deco(fn):
        ROUTES.append((method, pattern, fn))
        return fn
    return deco


_SNIPPET_TITLE_RE = re.compile(r"^#{1,6}\s+\S.*(?:\n+|$)")


def _make_snippet(body, limit=600):
    """Markdown preview of an entry's body for feed/search cards -- like
    usememo's timeline, most notes are short enough to show almost in full,
    rendered with real formatting (lists, bold, code) rather than flattened
    to a plain-text teaser. Only strips the redundant leading '# Title' line
    (the card already shows the title) and truncates on a line/word boundary;
    the client renders whatever markdown survives via the same renderer used
    for the full entry, clamped visually with a fade if it overflows."""
    text = _SNIPPET_TITLE_RE.sub("", (body or "").lstrip("\n"), count=1).lstrip("\n")
    if len(text) <= limit:
        return text.rstrip()
    cut = text[:limit]
    cut = cut.rsplit("\n", 1)[0] if "\n" in cut else cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def _capture_stdout(fn, *a, **kw):
    """Run `fn`, capturing whatever it prints instead of letting it hit kb
    web's own stdout. Several kb internals (inbox promote/redirect/discard)
    only communicate their result via a print() -- this lets the web layer
    reuse them as-is instead of duplicating their logic just to get a
    structured return value."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue().strip()


@route("GET", "/api/health")
def api_health(h, root):
    h.send_json({"ok": True, "root": root})


@route("POST", "/api/capture")
def api_capture(h, root):
    """Quick-capture: append `text` to today's journal entry -- the same thing
    `kb journal -m TEXT` does on the command line, reused via journal_append()."""
    payload = h.read_json()
    text = (payload.get("text") or "").strip()
    if not text:
        h.send_json({"error": "empty"}, status=400)
        return
    result = _kb().journal_append(root, datetime.now().date(), text)
    if result is None:
        h.send_json({"error": "empty"}, status=400)
        return
    h.send_json(result, status=201)


@route("GET", "/api/search")
def api_search(h, root):
    """Relevance-ranked search, reusing scripts/kb's search_entries() -- the same
    query `kb search --json` runs. ?q= is required; ?type=, ?tag=, ?all=1 mirror
    the CLI's --type/--tag/--all flags."""
    query = (h.query.get("q", [""])[0] or "").strip()
    if not query:
        h.send_json({"error": "missing ?q="}, status=400)
        return

    entry_type = h.query.get("type", [None])[0]
    tag = h.query.get("tag", [None])[0]
    include_all = h.query.get("all", ["0"])[0] in ("1", "true")

    try:
        rows = _kb().search_entries(root, query, entry_type=entry_type, tag=tag,
                                     include_all=include_all)
    except sqlite3.OperationalError as e:
        h.send_json({"error": str(e)}, status=400)
        return
    if rows is None:
        h.send_json({"error": "no search index found -- run `kb index` first"}, status=503)
        return

    # search_entries() already ranked+joined everything it needs from fts, but
    # its row shape is shared with the CLI's plain-text output -- fetching
    # bodies here instead of widening that shared query keeps this snippet
    # concern purely a kb web thing.
    db_path = os.path.join(root, ".pkb", "fts.db")
    ids = [r[0] for r in rows]
    snippets = {}
    if ids:
        conn = sqlite3.connect(db_path)
        placeholders = ",".join("?" for _ in ids)
        snippets = dict(conn.execute(
            f"SELECT id, body FROM fts WHERE id IN ({placeholders})", ids
        ).fetchall())
        conn.close()

    h.send_json({"items": [
        {"id": r[0], "title": r[1], "type": r[2], "path": r[3],
         "snippet": _make_snippet(snippets.get(r[0]))}
        for r in rows
    ]})


@route("GET", "/api/feed")
def api_feed(h, root):
    """Reverse-chronological feed over everything in the index (journal entries
    and core content alike), the same `files` table `kb search` reads from.
    Cursor-paginated on `created` (?before=<ISO created>), optionally filtered
    by ?tag= and/or ?type= -- the latter is how the feed's type chips browse
    the repo without needing a search term."""
    db_path = os.path.join(root, ".pkb", "fts.db")
    if not os.path.exists(db_path):
        h.send_json({"error": "no search index found -- run `kb index` first"}, status=503)
        return

    limit = min(int(h.query.get("limit", ["20"])[0]), 100)
    before = h.query.get("before", [None])[0]
    tag = h.query.get("tag", [None])[0]
    entry_type = h.query.get("type", [None])[0]

    where, params = [], []
    if before:
        where.append("f.created < ?")
        params.append(before)
    if tag:
        where.append("(' ' || f.tags || ' ') LIKE ?")
        params.append(f"% {tag} %")
    if entry_type:
        where.append("f.type = ?")
        params.append(entry_type)
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        f"""SELECT f.id, f.title, f.type, f.path, f.created, f.updated, f.tags, fts.body
            FROM files f JOIN fts ON fts.id = f.id
            {clause} ORDER BY f.created DESC LIMIT ?""",
        params + [limit],
    ).fetchall()
    conn.close()

    items = [
        {
            "id": r[0], "title": r[1], "type": r[2], "path": r[3],
            "created": r[4], "updated": r[5],
            "tags": [t for t in (r[6] or "").split(" ") if t],
            "snippet": _make_snippet(r[7]),
        }
        for r in rows
    ]
    next_cursor = items[-1]["created"] if len(items) == limit else None
    h.send_json({"items": items, "next": next_cursor})


@route("GET", "/api/tags")
def api_tags(h, root):
    """Every tag used anywhere in the repo, for the tag editor's autocomplete
    -- reuses the same listing `kb tag add` offers through fzf."""
    h.send_json({"tags": _kb()._all_tags(root)})


@route("GET", "/api/entries")
def api_entries_list(h, root):
    """Every entry's id/title/type, for the link editor's autocomplete -- reuses
    the same listing `kb link add`'s fzf picker is built from. ?exclude=<id>
    drops one id (the entry being edited, so it can't link to itself)."""
    exclude = h.query.get("exclude", [None])[0]
    entries = _kb()._iter_all_entries(root)
    h.send_json({"items": [
        {"id": e["id"], "title": e["title"], "type": e["type"]}
        for e in entries if e["id"] != exclude
    ]})


@route("GET", "/api/entries/{entry_id}")
def api_entry_show(h, root, entry_id):
    found = _kb()._find_entry(root, entry_id)
    if found is None:
        h.send_json({"error": f"no entry with id '{entry_id}'"}, status=404)
        return
    path, fm, body = found
    h.send_json({"id": entry_id, "path": os.path.relpath(path, root), "frontmatter": fm, "body": body})


@route("GET", "/api/entries/{entry_id}/links")
def api_entry_links(h, root, entry_id):
    rc, message = _capture_stdout(_kb().show_links, root, entry_id, json_out=True)
    if rc != 0:
        h.send_json({"error": f"no entry with id '{entry_id}'"}, status=404)
        return
    h.send_json(json.loads(message))


@route("PATCH", "/api/entries/{entry_id}/tags")
def api_entry_tags_patch(h, root, entry_id):
    payload = h.read_json()
    result = _kb().entry_set_tags(root, entry_id, add=payload.get("add"), rm=payload.get("rm"))
    if result is None:
        h.send_json({"error": f"no entry with id '{entry_id}'"}, status=404)
        return
    h.send_json({"tags": result})


@route("PATCH", "/api/entries/{entry_id}/links")
def api_entry_links_patch(h, root, entry_id):
    payload = h.read_json()
    result = _kb().entry_set_links(root, entry_id, add=payload.get("add"), rm=payload.get("rm"))
    if result is None:
        h.send_json({"error": f"no entry with id '{entry_id}'"}, status=404)
        return
    if isinstance(result, dict):  # {"error": "unknown id(s) ..."}
        h.send_json(result, status=400)
        return
    h.send_json({"links": result})


@route("PATCH", "/api/entries/{entry_id}")
def api_entry_content_patch(h, root, entry_id):
    payload = h.read_json()
    result = _kb().entry_update_content(root, entry_id, title=payload.get("title"), body=payload.get("body"))
    if result is None:
        h.send_json({"error": f"no entry with id '{entry_id}'"}, status=404)
        return
    h.send_json(result)


@route("GET", "/api/inbox")
def api_inbox_list(h, root):
    h.send_json({"items": _kb().inbox_list(root)})


@route("POST", "/api/inbox/{item_id}/promote")
def api_inbox_promote(h, root, item_id):
    kb = _kb()
    target_type = (h.read_json() or {}).get("type")
    if target_type not in kb.pc.CORE_TYPES:
        h.send_json({"error": f"type must be one of {list(kb.pc.CORE_TYPES)}"}, status=400)
        return
    found = kb._inbox_find(os.path.join(root, "inbox"), item_id)
    if found is None:
        h.send_json({"error": f"no inbox item with id '{item_id}'"}, status=404)
        return
    path, fm, body = found
    _, message = _capture_stdout(kb._inbox_promote, root, path, fm, body, target_type)
    h.send_json({"message": message})


@route("POST", "/api/inbox/{item_id}/redirect")
def api_inbox_redirect(h, root, item_id):
    kb = _kb()
    found = kb._inbox_find(os.path.join(root, "inbox"), item_id)
    if found is None:
        h.send_json({"error": f"no inbox item with id '{item_id}'"}, status=404)
        return
    path, fm, body = found
    _, message = _capture_stdout(kb._inbox_redirect, root, path, fm, body)
    h.send_json({"message": message})


@route("DELETE", "/api/inbox/{item_id}")
def api_inbox_discard(h, root, item_id):
    kb = _kb()
    found = kb._inbox_find(os.path.join(root, "inbox"), item_id)
    if found is None:
        h.send_json({"error": f"no inbox item with id '{item_id}'"}, status=404)
        return
    path, _fm, _body = found
    _, message = _capture_stdout(kb._inbox_discard, root, path)
    h.send_json({"message": message})


@route("GET", "/api/todo")
def api_todo_list(h, root):
    kb = _kb()
    if not kb.bd_available():
        h.send_json({"error": "bd CLI not found"}, status=503)
        return
    include_all = h.query.get("all", ["0"])[0] in ("1", "true")
    try:
        issues = kb.bd_list(root, include_all=include_all)
    except RuntimeError as e:
        h.send_json({"error": str(e)}, status=502)
        return
    h.send_json({"items": issues})


@route("POST", "/api/todo")
def api_todo_create(h, root):
    kb = _kb()
    if not kb.bd_available():
        h.send_json({"error": "bd CLI not found"}, status=503)
        return
    payload = h.read_json()
    title = (payload.get("title") or "").strip()
    if not title:
        h.send_json({"error": "missing title"}, status=400)
        return
    try:
        message = kb.bd_create(
            root, title, description=payload.get("description"),
            priority=str(payload.get("priority", "2")),
            issue_type=payload.get("type", "task"), labels=payload.get("labels"),
        )
    except RuntimeError as e:
        h.send_json({"error": str(e)}, status=502)
        return
    h.send_json({"message": message}, status=201)


@route("POST", "/api/todo/{issue_id}/{action}")
def api_todo_action(h, root, issue_id, action):
    if action not in ("show", "close", "comment"):
        h.send_json({"error": f"unknown action '{action}'"}, status=404)
        return
    kb = _kb()
    if not kb.bd_available():
        h.send_json({"error": "bd CLI not found"}, status=503)
        return
    comment_text = None
    if action == "comment":
        comment_text = (h.read_json() or {}).get("text", "").strip()
        if not comment_text:
            h.send_json({"error": "missing text"}, status=400)
            return
    try:
        message = kb.bd_action(root, action, issue_id, comment_text=comment_text)
    except RuntimeError as e:
        h.send_json({"error": str(e)}, status=502)
        return
    h.send_json({"message": message})


# ---------- admin: index / validate / sync / doctor ----------
# These call straight through to the same cmd_* functions the CLI uses, via a
# throwaway argparse.Namespace stand-in -- not custom subprocess plumbing --
# so kb web can never drift from what `kb index`/`kb validate`/`kb sync`/`kb
# doctor` actually do. cmd_index/validate/sync shell out to sibling scripts
# with inherited stdio (same as the CLI), so their real output lands in kb
# web's own terminal rather than the JSON response; only cmd_doctor is plain
# Python prints, so its output can be captured and returned in full.

@route("POST", "/api/index")
def api_index(h, root):
    args = types.SimpleNamespace(full=bool((h.read_json() or {}).get("full")))
    rc = _kb().cmd_index(args)
    h.send_json({"ok": rc == 0, "note": "full output printed in kb web's own terminal"})


@route("POST", "/api/validate")
def api_validate(h, root):
    rc = _kb().cmd_validate(types.SimpleNamespace())
    h.send_json({"ok": rc == 0, "note": "full output printed in kb web's own terminal"})


@route("POST", "/api/sync")
def api_sync(h, root):
    source = (h.read_json() or {}).get("source", "all")
    if source not in ("memos", "gitlab", "beads", "all"):
        h.send_json({"error": f"unknown source '{source}'"}, status=400)
        return
    args = types.SimpleNamespace(source=None if source == "all" else source)
    rc = _kb().cmd_sync(args)
    h.send_json({"ok": rc == 0, "note": "full output printed in kb web's own terminal"})


@route("GET", "/api/doctor")
def api_doctor(h, root):
    kb = _kb()
    rc, output = _capture_stdout(kb.cmd_doctor, types.SimpleNamespace())
    h.send_json({"ok": rc == 0, "output": output})


class Handler(http.server.BaseHTTPRequestHandler):
    root = None  # set by serve() before the server starts handling requests

    def log_message(self, fmt, *args):
        pass  # kb web stays quiet on stdout; add real logging if this needs debugging later

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        parsed = urlsplit(self.path)
        path = parsed.path
        self.query = parse_qs(parsed.query)
        for route_method, pattern, handler in ROUTES:
            if route_method != method:
                continue
            match = pattern.match(path)
            if match:
                try:
                    handler(self, self.root, *match.groups())
                except Exception as e:
                    self.send_json({"error": str(e)}, status=500)
                return
        if method == "GET":
            self._serve_static(path)
            return
        self.send_json({"error": f"no route for {method} {path}"}, status=404)

    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("/")
        file_path = os.path.join(WEB_DIR, safe)
        if not file_path.startswith(WEB_DIR) or not os.path.isfile(file_path):
            self.send_json({"error": "not found"}, status=404)
            return
        ctype = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        with open(file_path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True


def serve(root, port):
    Handler.root = root
    try:
        httpd = Server(("127.0.0.1", port), Handler)
    except OSError as e:
        print(f"error: can't bind 127.0.0.1:{port} -- {e}", file=sys.stderr)
        return 1

    url = f"http://127.0.0.1:{port}/"
    print(f"kb web running at {url} (Ctrl-C to stop)")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        httpd.server_close()
    return 0
