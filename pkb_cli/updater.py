"""kb update / kb push helpers: how kb was installed, whether a newer release exists, and applying or scheduling an update."""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import typing
import urllib.error
from importlib import metadata
from urllib.parse import unquote, urlparse

from . import __version__
from . import pkb_common as pc
from . import proc
from . import remind_push
from . import service

_SOURCE_TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the checkout this file sits in, if it's one

# Where kb itself is published, for `kb update`'s release check.
TOOL_REPO_SLUG = os.environ.get("PKB_CLI_REPO", "dantuck/pkb-cli")


def _checkout_root():
    """The git checkout this kb runs from, or None for a regular (release) install.

    Asked of the installed package's metadata (PEP 610 `direct_url.json`) rather than guessed from
    where the code sits: only an editable install points back at a source tree, while a wheel or
    `git+https` install -- what uv/pipx make -- is a release, even if a checkout happens to be nearby.
    With no metadata at all (running `python -m pkb_cli` from an uninstalled tree) fall back to
    whether this tree is itself a checkout."""
    try:
        dist = metadata.distribution("pkb-cli")
    except metadata.PackageNotFoundError:
        return _SOURCE_TREE if os.path.isdir(os.path.join(_SOURCE_TREE, ".git")) else None
    try:
        info = json.loads(dist.read_text("direct_url.json") or "{}")
    except ValueError:
        return None
    url = info.get("url", "")
    if not (info.get("dir_info", {}).get("editable") and url.startswith("file:")):
        return None
    path = unquote(urlparse(url).path)
    return path if os.path.isdir(os.path.join(path, ".git")) else None


def _git(*args, cwd=None):
    return subprocess.run(["git", "-C", cwd or _checkout_root(), *args], capture_output=True, text=True)


def _github_request(url):
    import urllib.request  # deferred: pulls in ssl/http, which most kb commands never need
    req = urllib.request.Request(url, headers={"User-Agent": "pkb-cli"})
    with urllib.request.urlopen(req, timeout=30, context=pc.ssl_context()) as resp:
        return resp.read()


def _latest_release_version():
    """Version of the latest GitHub release, from a tag like "v1.2.3" (or the
    component-prefixed "pkb-cli-v1.2.3" that 0.1.0 was tagged with)."""
    data = json.loads(_github_request(f"https://api.github.com/repos/{TOOL_REPO_SLUG}/releases/latest"))
    return re.sub(r"^(?:pkb-cli-)?v", "", data["tag_name"])


def _version_tuple(version):
    return tuple(int(n) for n in re.findall(r"\d+", version.split("+")[0]))


def _installer():
    """"uv" or "pipx", whichever tool owns this install's venv (each drops a marker
    file in it, wherever its home is configured); None if we can't tell (e.g. a
    plain pip install into some other environment)."""
    if os.path.exists(os.path.join(sys.prefix, "uv-receipt.toml")):
        return "uv"
    if os.path.exists(os.path.join(sys.prefix, "pipx_metadata.json")):
        return "pipx"
    return None


def _install_command(installer, ref, extras=None):
    """Install pkb-cli at `ref` (a tag or branch) with `installer`, optionally with an extra
    (e.g. "semantic"). Reinstalling by ref rather than running `upgrade`: installs are pinned to
    the tag they were made from (see install.sh), and uv/pipx `upgrade` never moves a pinned ref.

    install.sh builds the same command in shell, because it runs before kb exists -- keep the two
    in step (and PKB_CLI_SEMANTIC there is this function's `extras`)."""
    base = [installer, "tool", "install"] if installer == "uv" else [installer, "install"]
    spec = f"git+https://github.com/{TOOL_REPO_SLUG}@{ref}"
    if extras:
        spec = f"pkb-cli[{extras}] @ {spec}"
    return base + ["--force", spec]


def install_hint(extras):
    """The one-line command that reinstalls this kb with `extras`, for the installer that owns
    this venv (uv when that can't be told) -- what a "not installed (optional)" message tells the
    user to run, instead of a hardcoded `uv tool install` that is wrong under pipx."""
    ref = f"v{__version__}" if re.fullmatch(r"\d+(\.\d+)*", __version__) else "main"
    return shlex.join(_install_command(_installer() or "uv", ref, extras))


def update_status(fetch=False):
    """Compare this pkb-cli checkout's HEAD against its upstream.

    fetch=False (used by `kb doctor`) never touches the network -- it only compares
    against whatever origin/<branch> was last fetched, so it can go stale, but stays
    within doctor's "never writes, never hits the network" contract. fetch=True (used
    by `kb update`) refreshes that ref first, so its answer is authoritative.

    Returns a dict: is_git, upstream, local, remote, behind, dirty. Fields are None
    when they don't apply (e.g. not a git checkout, or no upstream configured).
    """
    result = {"is_git": _checkout_root() is not None,
              "upstream": None, "local": None, "remote": None, "behind": None, "dirty": None}
    if not result["is_git"]:
        return result

    if fetch:
        _git("fetch", "--quiet", "origin")

    # one `status --branch` call yields HEAD, the upstream, how far behind it is, and whether the
    # tree is dirty -- instead of a separate git process for each
    status = _git("status", "--porcelain=v2", "--branch")
    if status.returncode != 0:
        return result
    oid = upstream = behind = None
    dirty = False
    for line in status.stdout.splitlines():
        if line.startswith("# branch.oid "):
            oid = line.split(" ", 2)[2]
        elif line.startswith("# branch.upstream "):
            upstream = line.split(" ", 2)[2]
        elif line.startswith("# branch.ab "):
            behind = int(line.split()[3].lstrip("-"))
        elif not line.startswith("#"):
            dirty = True
    if upstream is None or behind is None:  # no upstream configured, or its ref doesn't exist
        return result
    result["upstream"] = upstream
    result["local"] = None if oid == "(initial)" else oid
    result["remote"] = _git("rev-parse", upstream).stdout.strip()
    result["behind"] = behind
    result["dirty"] = dirty
    return result


def plan_update():
    """Decide what `kb update` would do, without doing it -- the single source of
    truth for `kb update`, `kb update --check` and kb web's Manage panel, so they
    can't disagree about whether an update is available or allowed. Touches the
    network (git fetch / GitHub releases API) but never writes to the install.

    Returns {mode, current, latest, update_available, can_update, command,
    installer, detail, error}. `mode` is "git" (source checkout) or "release"
    (uv/pipx/pip install). `detail` is the status line to show; `error` is why
    it can't proceed (or couldn't check); `command` is the argv that installs
    the update (release mode)."""
    plan = {"mode": "release", "current": __version__, "latest": None, "update_available": False,
            "can_update": False, "command": None, "installer": None, "detail": None, "error": None}

    root = _checkout_root()
    if root:
        plan["mode"] = "git"
        fetch = _git("fetch", "--quiet", "origin")
        if fetch.returncode != 0:
            plan["error"] = f"git fetch failed: {fetch.stderr.strip()}"
            return plan
        status = update_status(fetch=False)  # already fetched above, no need to do it twice
        if not status["upstream"]:
            plan["error"] = f"no upstream branch configured for {root}"
            return plan
        local, remote = status["local"][:8], status["remote"][:8]
        plan.update(current=local, latest=remote, update_available=bool(status["behind"]))
        if not status["behind"]:
            plan["detail"] = f"kb is up to date with {status['upstream']} ({local})"
        else:
            plan["detail"] = f"kb is {status['behind']} commit(s) behind {status['upstream']} ({local} -> {remote})"
            if status["dirty"]:
                plan["error"] = f"{root} has local changes -- commit or stash them before updating"
            else:
                plan["can_update"] = True
        return plan

    try:
        latest = _latest_release_version()
    except urllib.error.HTTPError as e:
        if e.code == 404:  # no release has been published yet
            plan["detail"] = f"kb {__version__}: no releases published for {TOOL_REPO_SLUG} yet"
        else:
            plan["error"] = f"couldn't check for updates: {e}"
        return plan
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        plan["error"] = f"couldn't check for updates: {e}"
        return plan

    plan["latest"] = latest
    if _version_tuple(latest) <= _version_tuple(__version__):
        plan["detail"] = f"kb is up to date ({__version__})"
        return plan
    plan["update_available"] = True
    plan["detail"] = f"kb {latest} is available (you have {__version__})"
    plan["installer"] = _installer()
    if plan["installer"]:
        plan.update(can_update=True, command=_install_command(plan["installer"], f"v{latest}"))
    else:
        plan["error"] = ("can't tell how kb was installed -- reinstall it with the tool you used "
                         f"(e.g. `{' '.join(_install_command('pipx', f'v{latest}'))}`)")
    return plan


def update_info():
    """plan_update() as JSON-friendly data for kb web (the command as one string)."""
    info = plan_update()
    info["command"] = " ".join(info["command"]) if info["command"] else None
    return info


class _UpdateOpts(typing.NamedTuple):
    check: bool = False
    # print the installer's output (so a caller can capture it) instead of letting
    # it stream straight to the terminal -- see proc.run_captured
    capture: bool = False
    # leave restarting the login services to the caller: kb web's Manage panel
    # can't have its own service restarted underneath an in-flight request
    defer_refresh: bool = False


def cmd_update(args):
    return apply_update(check=args.check)[0]


def apply_update(check=False, capture=False, defer_refresh=False):
    """Update this pkb-cli install if plan_update() says it's behind. Returns
    (returncode, updated) -- `updated` is True when new code was installed, which
    with `defer_refresh` is the caller's cue to refresh the services itself.

    A source checkout (e.g. a contributor's `git clone` + `pip install -e .`) uses
    git pull; a normal install runs the upgrade command for whichever tool
    installed it (pipx or uv). The only kb command besides `kb sync` that touches
    the network."""
    opts = _UpdateOpts(check, capture, defer_refresh)
    plan = plan_update()
    if plan["detail"]:
        print(plan["detail"])
    if plan["update_available"] and opts.check:
        if plan["mode"] == "git":
            print("run `kb update` (without --check) to pull")
        elif plan["mode"] == "release":
            print(f"run `{' '.join(plan['command'])}` to upgrade" if plan["command"] else "run `kb update` to upgrade")
        return 1, False
    if plan["error"]:
        print(f"error: {plan['error']}", file=sys.stderr)
        return 1, False
    if not plan["update_available"]:
        return 0, False

    if plan["mode"] == "git":
        pull = _git("pull", "--ff-only")
        if pull.returncode != 0:
            print(f"error: git pull failed: {pull.stderr.strip()}", file=sys.stderr)
            return 1, False
        print(pull.stdout.strip())
        print("updated -- run `kb setup` again if this changed onboarding behavior (e.g. hook logic).")
        return 0, True

    rc = _run_update_step(plan["command"], opts)
    if rc != 0:
        return rc, False
    _refresh_services_after_update(opts)
    _refresh_worker_after_update(opts)
    print("updated -- run `kb setup` again if this changed onboarding behavior (e.g. hook logic).")
    return 0, True


def _run_update_step(cmd, opts):
    """Run an install/refresh command for `kb update`, streaming its output to
    the terminal unless `opts.capture`."""
    if opts.capture:
        return proc.run_captured(cmd)
    return subprocess.run(cmd).returncode


def _refresh_services_after_update(opts):
    """Run `kb service refresh` from the freshly installed kb -- this process is still
    the old code (and possibly the old interpreter), so it can't write units that
    point at the new install itself."""
    if not opts.defer_refresh and service.service_kind():
        _run_update_step(installed_kb_argv("service", "refresh"), opts)


def _refresh_worker_after_update(opts):
    """Carry the phone-reminder Worker along with the release, using the freshly installed
    kb: update it if kb is pointed at one and its source changed, else offer to set one up
    (once). It needs a terminal -- wrangler may log in or prompt -- so an update run from
    the web UI or a script only says what to do."""
    if opts.capture or opts.defer_refresh or not pc.is_interactive():
        if remind_push.get_config():
            print("the phone-reminder Worker wasn't updated here -- run `kb remind push deploy` "
                  "in a terminal to update it")
        return
    _run_update_step(installed_kb_argv("remind", "push", "deploy", "--ask"), opts)


def installed_kb_argv(*args):
    """argv for the `kb` on PATH -- the freshly reinstalled one, after an update --
    falling back to this interpreter's `python -m pkb_cli`."""
    exe = shutil.which("kb")
    return [exe, *args] if exe else proc.kb_cmd(*args)


def schedule_kb_command(*args, delay=1):
    """Run `kb <args>` as a separate one-shot job after `delay` seconds, outside the
    caller's own process tree. For anything that restarts or removes the service kb
    web itself runs under: a plain child would be killed with it before finishing,
    and the delay lets the HTTP response that scheduled it flush first. launchd/
    systemd run it as their own job; elsewhere a new session is the best available."""
    wrapped = ["sh", "-c", f'sleep {int(delay)}; exec "$@"', "sh", *installed_kb_argv(*args)]
    if sys.platform == "darwin" and shutil.which("launchctl"):
        label = f"dev.pkb-cli.admin-{os.getpid()}-{int(time.time())}"
        attempt = ["launchctl", "submit", "-l", label, "--", *wrapped]
    elif sys.platform.startswith("linux") and shutil.which("systemd-run"):
        attempt = ["systemd-run", "--user", "--quiet", "--collect", *wrapped]
    else:
        attempt = None
    if attempt and subprocess.run(attempt, capture_output=True).returncode == 0:
        return
    subprocess.Popen(wrapped, start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
