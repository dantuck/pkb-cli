"""`kb remind push deploy`: put the phone-reminder Worker live on the user's own
Cloudflare account, or update it. Drives `wrangler` (login, D1 database, migrations,
secrets, deploy) from the copy `kb remind push init` makes, and is what `kb update`
runs, so a new kb release carries the Worker along.

Every wrangler call goes through _stream / _capture / _tee so tests can fake them."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from . import pkb_common as pc
from . import remind_push as rp

DB_NAME = "kb-reminders"
DEPLOYED_FILE = ".kb-deployed"  # in the copied-out Worker: hash of the source last deployed
URL_RE = re.compile(r"https://[A-Za-z0-9.-]+\.workers\.dev")
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
VERIFY_TRIES = 6
VERIFY_WAIT_SECONDS = 3


class DeployError(Exception):
    pass


# --- process seams -----------------------------------------------------------------

def _in(dest, argv):
    """argv, run from `dest`."""
    return ["sh", "-c", 'cd "$0" && exec "$@"', dest, *argv]


def _stream(argv):
    """Run with the terminal attached. Returns the exit code."""
    return subprocess.run(argv).returncode


def _capture(argv):
    """Run quietly. Returns (exit code, stdout)."""
    result = subprocess.run(argv, capture_output=True, text=True)
    return result.returncode, result.stdout


def _tee(argv):
    """Run with stdout/stderr on a pseudo-terminal, so wrangler behaves as it does in a
    terminal and its prompts show, while keeping a copy of what it printed. stdin stays the
    real terminal, so answers are typed straight to it. Returns (exit code, output)."""
    import pty  # POSIX only, so not at import time

    master, slave = pty.openpty()
    proc = subprocess.Popen(argv, stdout=slave, stderr=slave)
    os.close(slave)
    chunks = []
    try:
        while True:
            try:
                data = os.read(master, 4096)
            except OSError:  # Linux reports the child's exit as EIO
                break
            if not data:
                break
            chunks.append(data)
            os.write(1, data)
    finally:
        os.close(master)
    return proc.wait(), ANSI_RE.sub("", b"".join(chunks).decode("utf-8", "replace"))


# --- steps -------------------------------------------------------------------------

def _tools():
    """(install argv, wrangler argv prefix): pnpm if there is one, else npm."""
    if not shutil.which("node"):
        raise DeployError("Node.js is needed to deploy the Worker -- install it (https://nodejs.org), "
                          "then run `kb remind push deploy` again")
    if shutil.which("pnpm"):
        return ["pnpm", "install"], ["pnpm", "exec", "wrangler"]
    if shutil.which("npm"):
        return ["npm", "install"], ["npx", "wrangler"]
    raise DeployError("pnpm or npm is needed to deploy the Worker -- install one, then run "
                      "`kb remind push deploy` again")


def _json_from(text):
    """The first JSON value in `text`, which wrangler may print after a banner."""
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                return json.JSONDecoder().raw_decode(text[i:])[0]
            except ValueError:
                continue
    return None


def _must(rc, what):
    if rc != 0:
        raise DeployError(f"{what} failed (exit {rc})")


def _ensure_login(dest, wrangler):
    def logged_in():
        return _capture(_in(dest, [*wrangler, "whoami", "--json"]))[0] == 0

    if logged_in():
        return
    if not pc.is_interactive():
        raise DeployError("not logged in to Cloudflare -- run `kb remind push deploy` in a terminal "
                          "(or `wrangler login` first)")
    print("Logging in to Cloudflare...")
    _must(_stream(_in(dest, [*wrangler, "login"])), "wrangler login")
    if not logged_in():
        raise DeployError("still not logged in to Cloudflare")


def _find_database(dest, wrangler):
    """The id of the account's `kb-reminders` D1 database, or None."""
    rc, out = _capture(_in(dest, [*wrangler, "d1", "list", "--json"]))
    for db in (_json_from(out) or []) if rc == 0 else []:
        if isinstance(db, dict) and db.get("name") == DB_NAME:
            return next((db[k] for k in ("uuid", "id", "database_id") if db.get(k)), None)
    return None


def _ensure_database(dest, wrangler):
    config = os.path.join(dest, "wrangler.jsonc")
    if rp.get_database_id(config):
        return
    database_id = _find_database(dest, wrangler)
    if database_id is None:
        _must(_stream(_in(dest, [*wrangler, "d1", "create", DB_NAME])), "creating the D1 database")
        database_id = _find_database(dest, wrangler)
    if database_id is None:
        raise DeployError(f"couldn't find the id of the '{DB_NAME}' D1 database -- add it to "
                          f"{config} as \"database_id\" (see `wrangler d1 list`) and run this again")
    if not rp.set_database_id(config, database_id):
        raise DeployError(f"couldn't add the D1 database id to {config} -- add \"database_id\": "
                          f"\"{database_id}\" to its d1_databases entry and run this again")


def _generate_secrets(dest):
    """The Worker's secrets, as its own gen-keys script prints them (Node has the P-256
    support). kb itself needs only the two tokens."""
    rc, out = _capture(_in(dest, ["node", "scripts/gen-keys.mjs"]))
    secrets = dict(line.split("=", 1) for line in out.splitlines() if re.match(r"^[A-Z_]+=", line))
    if rc != 0 or not {"SYNC_TOKEN", "SUBSCRIBE_TOKEN"} <= set(secrets):
        raise DeployError("couldn't generate the Worker's keys")
    return secrets


def _deploy(dest, wrangler, secrets):
    """`wrangler deploy`, uploading `secrets` with it on a first deploy. Returns its output."""
    argv = [*wrangler, "deploy"]
    tmp = None
    try:
        if secrets:
            tmp = tempfile.mkdtemp(prefix="kb-secrets-")  # mode 0700
            path = os.path.join(tmp, "secrets.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(secrets, f)
            argv += ["--secrets-file", path]
        rc, out = _tee(_in(dest, argv))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    _must(rc, "wrangler deploy")
    return out


def _worker_url(output):
    found = URL_RE.search(output)
    if found:
        return found.group(0)
    try:
        url = input("Couldn't read the Worker's URL from wrangler's output. Paste it: ").strip().rstrip("/")
    except EOFError:
        url = ""
    if not rp.valid_url(url):
        raise DeployError("no Worker URL -- run `kb remind push setup <url>` once you have it")
    return url


def _wait_until_live(url, token):
    """A fresh workers.dev URL can take a few seconds to answer."""
    message = ""
    for attempt in range(VERIFY_TRIES):
        ok, message, _ = rp.verify(url, token)
        if ok:
            return True, ""
        if attempt < VERIFY_TRIES - 1:
            time.sleep(VERIFY_WAIT_SECONDS)
    return False, message


def _mark_deployed(dest):
    with open(os.path.join(dest, DEPLOYED_FILE), "w", encoding="utf-8") as f:
        f.write(rp.source_hash() + "\n")


def _is_current(dest):
    try:
        with open(os.path.join(dest, DEPLOYED_FILE), encoding="utf-8") as f:
            return f.read().strip() == rp.source_hash()
    except OSError:
        return False


def _run(dest, configured):
    install, wrangler = _tools()
    print(f"Worker source: {rp.init_worker(dest)}")
    _must(_stream(_in(dest, install)), "installing the Worker's dependencies")
    _ensure_login(dest, wrangler)
    _ensure_database(dest, wrangler)
    _must(_stream(_in(dest, [*wrangler, "d1", "migrations", "apply", DB_NAME, "--remote"])), "applying migrations")

    secrets = None if configured else _generate_secrets(dest)
    output = _deploy(dest, wrangler, secrets)
    _mark_deployed(dest)

    if configured:
        print(rp.status()[1])
        return
    url = _worker_url(output)
    rp.save_config(url, secrets["SYNC_TOKEN"])
    live, message = _wait_until_live(url, secrets["SYNC_TOKEN"])
    print(f"Phone reminders are deployed: {url}")
    if not live:
        print(f"note: the Worker isn't answering yet ({message}) -- `kb remind push status` checks again")
    print(f"On your Android phone, open {url}, enter this subscribe token and tap Enable "
          f"notifications. kb doesn't keep it, so save it somewhere:\n\n    {secrets['SUBSCRIBE_TOKEN']}\n\n"
          f"Then `kb remind test` sends a test push. (A phone that was subscribed to an earlier "
          f"deploy of this Worker needs to enable notifications again.)")


def deploy(ask=False):
    """Deploy the Worker, or update it when kb is already pointed at one. With `ask` (from
    `kb update`) an update happens only if the source changed since the last deploy, and a
    first-time setup is offered rather than started. Returns an exit code."""
    configured = rp.get_config() is not None
    dest = rp.worker_dir()
    if ask:
        if configured:
            if _is_current(dest):
                return 0
            print("The phone-reminder Worker has a new version -- updating it on Cloudflare.")
        else:
            if rp.offer_declined():
                return 0
            if not pc.confirm("Set up phone reminders? This deploys a small Worker to your Cloudflare "
                              "account (needs Node and a login).", False):
                rp.decline_offer()
                print("ok -- `kb remind push deploy` sets it up any time")
                return 0
    try:
        _run(dest, configured)
    except DeployError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0
