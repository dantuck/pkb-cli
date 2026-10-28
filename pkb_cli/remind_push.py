"""Phone reminders: upload each TODO's id + due time to the kb-push Cloudflare Worker
(templates/worker), which sends the Web Push when it comes due -- so a reminder still
arrives while this machine is asleep. Only ids, due times and priorities leave the
machine; titles and descriptions never do.

The Worker's URL and sync token live in kb's machine-local config (never a data repo,
which is committed), with KB_PUSH_URL / KB_PUSH_TOKEN as overrides. The Worker's source
ships inside this package (templates/worker); `init_worker` copies it out to deploy."""
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import __version__
from . import pkb_common as pc

TIMEOUT_SECONDS = 15
WORKER_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "worker")
VERSION_FILE = ".kb-version"
# not part of the source: a dev checkout may have installed or built these in place
_NOT_SOURCE = shutil.ignore_patterns("node_modules", ".wrangler", ".dev.vars", "pnpm-lock.yaml",
                                      "worker-configuration.d.ts", "__pycache__")
# the one line of wrangler.jsonc that `wrangler d1 create`'s id is added to
_DB_NAME_LINE = '"database_name": "kb-reminders",'
_DB_ID = re.compile(r'"database_id"\s*:\s*"([0-9A-Fa-f-]{36})"')


def worker_dir():
    """Where `kb remind push init` puts the Worker by default, kept across kb upgrades."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "pkb-cli", "worker")


def _migrations(root):
    path = os.path.join(root, "migrations")
    return set(os.listdir(path)) if os.path.isdir(path) else set()


def init_worker(dest=None):
    """Copy the packaged Worker source into `dest` (default: worker_dir()), refreshing
    it on a later run: sources are overwritten, while node_modules and the database id
    in wrangler.jsonc (added by `wrangler d1 create`) are kept. Returns
    {dest, previous (kb version it was last copied by, or None), new_migrations}."""
    dest = os.path.abspath(os.path.expanduser(dest or worker_dir()))
    config = os.path.join(dest, "wrangler.jsonc")
    database_id = None
    if os.path.exists(config):
        with open(config, encoding="utf-8") as f:
            found = _DB_ID.search(f.read())
        database_id = found.group(1) if found else None
    previous = None
    try:
        with open(os.path.join(dest, VERSION_FILE), encoding="utf-8") as f:
            previous = f.read().strip() or None
    except OSError:
        pass
    before = _migrations(dest)

    shutil.copytree(WORKER_SRC, dest, ignore=_NOT_SOURCE, dirs_exist_ok=True)
    if database_id:
        with open(config, encoding="utf-8") as f:
            text = f.read()
        with open(config, "w", encoding="utf-8") as f:
            f.write(text.replace(_DB_NAME_LINE, f'{_DB_NAME_LINE} "database_id": "{database_id}",', 1))
    with open(os.path.join(dest, VERSION_FILE), "w", encoding="utf-8") as f:
        f.write(__version__ + "\n")
    return {"dest": dest, "previous": previous, "new_migrations": sorted(_migrations(dest) - before)}


def _norm(url):
    return (url or "").rstrip("/")


def get_config():
    """(url, token), or None unless both are set."""
    saved = pc.load_kb_config().get("push") or {}
    url = _norm(os.environ.get("KB_PUSH_URL") or saved.get("url"))
    token = os.environ.get("KB_PUSH_TOKEN") or saved.get("sync_token") or ""
    return (url, token) if url and token else None


def valid_url(url):
    """https only -- the token goes in a header -- except localhost, for `wrangler dev`."""
    parts = urlparse(url)
    local = parts.hostname in ("localhost", "127.0.0.1")
    return bool(parts.hostname) and (parts.scheme == "https" or (local and parts.scheme == "http"))


def save_config(url, token):
    """Store the Worker URL + sync token. The file holds a secret, so it's created
    owner-only (save_kb_config writes a temp file, hence the umask, not a later chmod)."""
    cfg = pc.load_kb_config()
    cfg["push"] = {"url": _norm(url), "sync_token": token}
    old = os.umask(0o077)
    try:
        pc.save_kb_config(cfg)
    finally:
        os.umask(old)


def disable():
    cfg = pc.load_kb_config()
    if cfg.pop("push", None) is not None:
        pc.save_kb_config(cfg)


def snapshot(issues):
    """What the Worker should know: every unclosed TODO with a due time, as
    {id, due_at (unix seconds), priority}. A snoozed TODO reports when its snooze
    ends -- the moment it should come due again, however its due date reads."""
    out = []
    for i in issues:
        when = pc.todo_remind_at(i)
        if when is None:
            continue
        try:
            priority = max(0, min(4, int(i.get("priority", 2))))
        except (TypeError, ValueError):
            priority = 2
        out.append({"id": i["id"], "due_at": int(when.timestamp()), "priority": priority})
    return out


def request(config, method, path, body=None):
    """(status, parsed JSON or {}) from the Worker. Raises OSError on a network failure."""
    url, token = config
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url + path, data=data, method=method, headers={
        "authorization": f"Bearer {token}", "content-type": "application/json",
        # Cloudflare's bot rules reject urllib's default agent
        "user-agent": "kb-cli",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS, context=pc.ssl_context()) as res:
            status, raw = res.status, res.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except urllib.error.URLError as e:
        raise OSError(str(e.reason)) from e
    try:
        return status, json.loads(raw or b"{}")
    except ValueError:
        return status, {}


def _call(method, path, body=None, config=None):
    """(payload, None) on a 200 from the Worker, else (None, what went wrong)."""
    config = config or get_config()
    if config is None:
        return None, "phone reminders aren't set up -- `kb remind push setup <worker-url>`"
    try:
        status, payload = request(config, method, path, body)
    except OSError as e:
        return None, f"couldn't reach the Worker: {e}"
    if status == 401:
        return None, "the Worker rejected the sync token -- run `kb remind push setup` again"
    if status != 200:
        return None, payload.get("error") or f"the Worker answered HTTP {status}"
    return payload, None


def sync(issues):
    """Replace the Worker's reminders with `issues`' current ones. (ok, message)."""
    reminders = snapshot(issues)
    _, err = _call("PUT", "/api/sync", {"reminders": reminders})
    return (False, err) if err else (True, f"synced {len(reminders)} reminder(s) to your phone")


def verify(url, token):
    """Ask a Worker whether the URL + token work, before they're saved.
    (ok, message, status payload)."""
    payload, err = _call("GET", "/api/status", config=(_norm(url), token))
    return (False, err, {}) if err else (True, "", payload)


def status():
    """(ok, message) describing the Worker: reachable, token accepted, and whether a
    phone is subscribed. Read-only."""
    config = get_config()
    if config is None:
        return False, "phone reminders aren't set up (optional) -- `kb remind push setup <worker-url>`"
    ok, msg, payload = verify(*config)
    if not ok:
        return False, f"phone reminders: {msg}"
    phones = payload.get("subscriptions", 0)
    if not phones:
        return False, (f"phone reminders: the Worker is up but no phone is subscribed -- open "
                       f"{config[0]} on your phone and tap Enable notifications")
    return True, f"phone reminders: {phones} phone(s) subscribed, {payload.get('reminders', 0)} reminder(s) tracked"


def send_test():
    """Ask the Worker to push a test notification to every subscribed phone."""
    payload, err = _call("POST", "/api/test")
    if err:
        return False, err
    if payload.get("delivered"):
        return True, f"sent a test push to {payload['delivered']} phone(s)"
    if not payload.get("failed") and not payload.get("removed"):
        return False, f"no phone is subscribed -- open {get_config()[0]} on your phone and tap Enable notifications"
    return False, "the push service didn't accept the test -- re-subscribe from your phone's page and try again"
