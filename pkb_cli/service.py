"""kb service / kb sync-service: run kb web or kb sync as a login-scoped background job via launchd (macOS) or systemd --user (Linux)."""
import os
import re
import shlex
import subprocess
import sys
import time

from . import pkb_common as pc
from . import proc

# --- `kb service` / `kb sync-service` -- run `kb web` or `kb sync` as a
# login-scoped background job via launchd (macOS) or systemd --user (Linux).
# Both stay strictly per-user (no root, no system-wide daemon) and just wrap
# the same `kb web`/`kb sync` invocation a person would run by hand -- this
# only automates when it runs, nothing the underlying command doesn't already
# do. `kb service` keeps `kb web` alive continuously (RunAtLoad+KeepAlive /
# a plain systemd service); `kb sync-service` fires `kb sync` on a timer
# (StartInterval / a oneshot service driven by a systemd timer unit). Those
# two shapes differ enough (persistent vs interval-triggered) that both go
# through one shared installer, parameterized by everything else that's
# actually identical: label/unit naming, the launchctl/systemctl dance, PATH,
# and status/uninstall.

AGENTS = {
    "web": {
        "launchd_label": "dev.pkb-cli.web",
        "systemd_unit": "kb-web.service",
        "log_name": "kb-web",
    },
    "sync": {
        "launchd_label": "dev.pkb-cli.sync",
        "systemd_unit": "kb-sync.service",
        "systemd_timer": "kb-sync.timer",
        "log_name": "kb-sync",
    },
    "remind": {
        "launchd_label": "dev.pkb-cli.remind",
        "systemd_unit": "kb-remind.service",
        "systemd_timer": "kb-remind.timer",
        "log_name": "kb-remind",
    },
}


def _service_kind():
    if sys.platform == "darwin":
        return "launchd"
    if sys.platform.startswith("linux"):
        return "systemd"
    return None


def _agent_launchd_plist_path(agent):
    return os.path.expanduser(f"~/Library/LaunchAgents/{AGENTS[agent]['launchd_label']}.plist")


def _agent_systemd_unit_path(agent):
    return os.path.expanduser(f"~/.config/systemd/user/{AGENTS[agent]['systemd_unit']}")


def _agent_systemd_timer_path(agent):
    return os.path.expanduser(f"~/.config/systemd/user/{AGENTS[agent]['systemd_timer']}")


def _service_path_env():
    """PATH for a background `kb` job: os.defpath (the bare minimum
    launchd/systemd --user give a service) plus the standard Homebrew/local
    prefixes, so bd/glab/gh/rg/fzf resolve the same as they do under `kb
    setup`/`kb doctor`. Deterministic on purpose -- unlike snapshotting
    whoever ran `install`'s ambient PATH, this doesn't bake in transient
    shims (pyenv, an active venv) or go stale once install-time is behind us."""
    extra = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/local/sbin"]
    return os.pathsep.join(extra + [os.defpath])


def _resolve_agent_repo(args):
    if args.repo:
        root = os.path.abspath(os.path.expanduser(args.repo))
        if not os.path.isdir(root):
            print(f"error: --repo {root} does not exist", file=sys.stderr)
            return None
        return root
    try:
        return pc.get_repo_root()
    except SystemExit:
        print("error: no pkb data repo found -- pass --repo DIR, or cd into one "
              "first (see `kb setup`)", file=sys.stderr)
        return None


def _launchd_load(label, plist_path):
    """(re)load a plist -- bootout first (ignore failure, fine if nothing was
    loaded yet) so a re-install picks up changes instead of bootstrap
    erroring "already loaded". Returns an error string, or None on success."""
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
    # bootout returns before launchd has finished tearing the old job down, so an
    # immediate bootstrap can fail with "Input/output error" (exit 5) -- retry briefly.
    error = None
    for attempt in range(5):
        if attempt:
            time.sleep(0.5)
        result = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", plist_path],
                                 capture_output=True, text=True)
        if result.returncode == 0:
            return None
        error = result.stderr.strip()
    return error


def _launchd_unload_and_remove(label, plist_path):
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)
    if os.path.exists(plist_path):
        os.remove(plist_path)
        return True
    return False


def _launchd_status(label, plist_path):
    """(installed, running, detail) -- detail is a human string for the
    not-running/not-loaded cases."""
    if not os.path.exists(plist_path):
        return False, False, "not installed"
    uid = os.getuid()
    result = subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                             capture_output=True, text=True)
    if result.returncode != 0:
        return True, False, f"installed ({plist_path}) but not loaded -- run `install` again"
    return True, "state = running" in result.stdout, None


def _systemd_enable_now(unit_name):
    """daemon-reload then enable --now one unit. Returns an error string, or
    None on success."""
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    result = subprocess.run(["systemctl", "--user", "enable", "--now", unit_name],
                             capture_output=True, text=True)
    if result.returncode != 0:
        return result.stderr.strip()
    return None


def _systemd_disable_and_remove(unit_name, *paths):
    subprocess.run(["systemctl", "--user", "disable", "--now", unit_name], capture_output=True)
    removed = False
    for path in paths:
        if os.path.exists(path):
            os.remove(path)
            removed = True
    if removed:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return removed


def _systemd_active_state(unit_name):
    result = subprocess.run(["systemctl", "--user", "is-active", unit_name],
                             capture_output=True, text=True)
    return result.stdout.strip()


def _linger_note():
    """None, or a note to print when the current user isn't lingering --
    without it a systemd --user timer/service dies at logout."""
    user = os.environ.get("USER", "")
    result = subprocess.run(["loginctl", "show-user", user, "-p", "Linger"],
                             capture_output=True, text=True)
    if result.returncode == 0 and "Linger=no" in result.stdout:
        return (f"  note: enable lingering so this survives logout/reboot without an active "
                f"session: loginctl enable-linger {user}")
    return None


def cmd_service(args):
    kind = _service_kind()
    if kind is None:
        print(f"error: `kb service` isn't supported on {sys.platform} -- only macOS "
              f"(launchd) and Linux (systemd --user) are supported. See `kb web` to "
              f"run it by hand instead.", file=sys.stderr)
        return 1
    if args.action == "install":
        return _service_install(kind, args)
    if args.action == "uninstall":
        return _service_uninstall(kind, "web")
    if args.action == "refresh":
        return _service_refresh(kind)
    return _service_status(kind, "web")


def _service_install(kind, args):
    root = _resolve_agent_repo(args)
    if root is None:
        return 1

    port = args.port
    agent = AGENTS["web"]

    if kind == "launchd":
        import plistlib
        log_dir = os.path.expanduser("~/Library/Logs")
        os.makedirs(log_dir, exist_ok=True)
        plist_path = _agent_launchd_plist_path("web")
        os.makedirs(os.path.dirname(plist_path), exist_ok=True)
        plist = {
            "Label": agent["launchd_label"],
            "ProgramArguments": proc.kb_cmd("web", "--port", str(port), "--no-open"),
            "WorkingDirectory": root,
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": os.path.join(log_dir, f"{agent['log_name']}.log"),
            "StandardErrorPath": os.path.join(log_dir, f"{agent['log_name']}.err.log"),
            # launchd defaults to /usr/bin:/bin:/usr/sbin:/sbin (no Homebrew) --
            # without this, bd/glab/gh/rg/fzf all read as "not found" under the
            # service even though `kb setup`/`kb doctor` find them fine in a shell.
            "EnvironmentVariables": {"PATH": _service_path_env()},
        }
        with open(plist_path, "wb") as f:
            plistlib.dump(plist, f)

        error = _launchd_load(agent["launchd_label"], plist_path)
        if error:
            print(f"error: launchctl bootstrap failed: {error}", file=sys.stderr)
            return 1
        print(f"installed and started {agent['launchd_label']} -> http://127.0.0.1:{port}/")
        print(f"  repo:  {root}")
        print(f"  plist: {plist_path}")
        print(f"  logs:  {log_dir}/{agent['log_name']}.log")
        return 0

    # systemd --user
    unit_path = _agent_systemd_unit_path("web")
    os.makedirs(os.path.dirname(unit_path), exist_ok=True)
    unit = (
        "[Unit]\n"
        "Description=kb web -- pkb-cli local web UI\n"
        "\n"
        "[Service]\n"
        f"ExecStart={shlex.join(proc.kb_cmd('web', '--port', str(port), '--no-open'))}\n"
        f"WorkingDirectory={root}\n"
        # systemd --user services get a minimal PATH by default too -- see
        # _service_path_env() for why this is a fixed Homebrew-aware PATH
        # rather than a snapshot of the installer's shell PATH.
        f'Environment="PATH={_service_path_env()}"\n'
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    with open(unit_path, "w", encoding="utf-8") as f:
        f.write(unit)
    error = _systemd_enable_now(agent["systemd_unit"])
    if error:
        print(f"error: systemctl enable --now failed: {error}", file=sys.stderr)
        return 1
    print(f"installed and started {agent['systemd_unit']} -> http://127.0.0.1:{port}/")
    print(f"  repo: {root}")
    print(f"  unit: {unit_path}")
    print(f"  logs: journalctl --user -u {agent['systemd_unit']} -f")
    note = _linger_note()
    if note:
        print(note)
    return 0


def _service_uninstall(kind, agent_name):
    agent = AGENTS[agent_name]
    if kind == "launchd":
        plist_path = _agent_launchd_plist_path(agent_name)
        if _launchd_unload_and_remove(agent["launchd_label"], plist_path):
            print(f"stopped and removed {agent['launchd_label']} ({plist_path})")
        else:
            print(f"{agent['launchd_label']} wasn't installed")
        return 0

    unit_path = _agent_systemd_unit_path(agent_name)
    if _systemd_disable_and_remove(agent["systemd_unit"], unit_path):
        print(f"stopped and removed {agent['systemd_unit']} ({unit_path})")
    else:
        print(f"{agent['systemd_unit']} wasn't installed")
    return 0


def _service_state(kind, name):
    """{installed, running, detail} for one installed-or-not service --
    what `kb service status`, `kb sync-service status` and kb web's Manage panel
    all report, so they can't disagree. `detail` is a human string for the
    not-installed / not-loaded / not-active cases; On systemd it's the unit's active state ("inactive", "failed", ...)."""
    agent = AGENTS[name]
    if kind == "launchd":
        installed, running, detail = _launchd_status(agent["launchd_label"], _agent_launchd_plist_path(name))
        return {"installed": installed, "running": bool(running), "detail": detail}
    # the sync/remind services are driven by their timer unit, not the oneshot service
    path, unit = ((_agent_systemd_timer_path(name), agent["systemd_timer"]) if "systemd_timer" in agent
                  else (_agent_systemd_unit_path(name), agent["systemd_unit"]))
    if not os.path.exists(path):
        return {"installed": False, "running": False, "detail": "not installed"}
    state = _systemd_active_state(unit)
    return {"installed": True, "running": state == "active", "detail": None if state == "active" else state}


def _service_status(kind, agent_name):
    info = _service_state(kind, agent_name)
    if not info["installed"]:
        print(info["detail"])
        return 1
    if kind == "launchd":
        path = _agent_launchd_plist_path(agent_name)
        if info["detail"]:
            print(info["detail"])
            return 1
        print(f"installed and {'running' if info['running'] else 'loaded but not running'} ({path})")
        return 0 if info["running"] else 1
    print(f"installed ({_agent_systemd_unit_path(agent_name)}), {info['detail'] or 'active'}")
    return 0 if info["running"] else 1


def _timer_interval_minutes(kind, name):
    """Minutes between runs of an installed interval service (sync, remind), or None
    if it can't be read."""
    try:
        if kind == "launchd":
            import plistlib
            with open(_agent_launchd_plist_path(name), "rb") as f:
                return plistlib.load(f).get("StartInterval", 0) // 60 or None
        with open(_agent_systemd_timer_path(name), encoding="utf-8") as f:
            m = re.search(r"^OnUnitActiveSec=(\d+)min", f.read(), re.M)
        return int(m.group(1)) if m else None
    except (OSError, ValueError):
        return None


def service_info():
    """Structured `kb service status` / `kb sync-service status` for kb web's Manage
    panel: {supported, kind, web: {...}, sync: {...}}, each service reporting
    {installed, running, detail}. Never writes anything."""
    kind = _service_kind()
    if kind is None:
        return {"supported": False, "kind": None}
    out = {"supported": True, "kind": kind}
    for name in AGENTS:
        out[name] = _service_state(kind, name)
    if out["sync"]["installed"]:
        out["sync"]["interval_minutes"] = _timer_interval_minutes(kind, "sync")
    return out


def _unit_args(argv):
    """The kb subcommand + args from an installed unit's command line, whichever
    launcher it was written with (`python -m pkb_cli ...`, or the pre-package
    `python <checkout>/scripts/kb ...`)."""
    return argv[3:] if argv[1:3] == ["-m", __package__] else argv[2:]


def _service_refresh(kind):
    """Repoint each installed kb service unit at this install and restart it, keeping
    its repo/port/interval. Run after an update so units written by an older install
    (a stale launcher path) work again and a long-running `kb web` picks up the new
    code. Units that aren't installed are skipped."""
    import plistlib
    for name, agent in AGENTS.items():
        if kind == "launchd":
            path = _agent_launchd_plist_path(name)
            if not os.path.exists(path):
                continue
            with open(path, "rb") as f:
                plist = plistlib.load(f)
            plist["ProgramArguments"] = proc.kb_cmd(*_unit_args(plist["ProgramArguments"]))
            with open(path, "wb") as f:
                plistlib.dump(plist, f)
            error = _launchd_load(agent["launchd_label"], path)
            label = agent["launchd_label"]
        else:
            path = _agent_systemd_unit_path(name)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as f:
                unit = f.read()
            unit = re.sub(r"^ExecStart=(.*)$",
                          lambda m: "ExecStart=" + shlex.join(proc.kb_cmd(*_unit_args(shlex.split(m.group(1))))),
                          unit, count=1, flags=re.M)
            with open(path, "w", encoding="utf-8") as f:
                f.write(unit)
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            error = None
            if name == "web":  # the timer-driven services pick up the new unit next run
                result = subprocess.run(["systemctl", "--user", "restart", agent["systemd_unit"]],
                                        capture_output=True, text=True)
                error = result.stderr.strip() if result.returncode != 0 else None
            label = agent["systemd_unit"]
        print(f"refreshed {label}" if error is None else f"error: couldn't restart {label}: {error}")
    return 0


# --- `kb sync-service` -- runs `kb sync` on a recurring interval via launchd
# (macOS, StartInterval) or a systemd --user timer (Linux), instead of
# `kb service`'s persistent process. Shares everything above but the plist/
# unit shape and status/uninstall need the extra timer unit on systemd.


def cmd_sync_service(args):
    kind = _service_kind()
    if kind is None:
        print(f"error: `kb sync-service` isn't supported on {sys.platform} -- only macOS "
              f"(launchd) and Linux (systemd --user) are supported. See `kb sync` to "
              f"run it by hand instead.", file=sys.stderr)
        return 1
    if args.action == "install":
        return _sync_service_install(kind, args)
    if args.action == "uninstall":
        return _sync_service_uninstall(kind)
    return _sync_service_status(kind)


def _sync_service_install(kind, args):
    root = _resolve_agent_repo(args)
    if root is None:
        return 1
    return _interval_service_install(kind, "sync", root, ["sync"], args.interval_minutes,
                                      "kb sync -- pull in pkb-cli sync sources")


def _interval_service_install(kind, name, root, kb_args, interval_minutes, description):
    """Install a job that runs `kb <kb_args>` every interval_minutes: a launchd
    LaunchAgent with StartInterval, or a oneshot systemd --user service driven
    by a timer unit. Shared by `kb sync-service` and `kb remind install`."""
    agent = AGENTS[name]
    shown = " ".join(kb_args)

    if kind == "launchd":
        import plistlib
        log_dir = os.path.expanduser("~/Library/Logs")
        os.makedirs(log_dir, exist_ok=True)
        plist_path = _agent_launchd_plist_path(name)
        os.makedirs(os.path.dirname(plist_path), exist_ok=True)
        plist = {
            "Label": agent["launchd_label"],
            "ProgramArguments": proc.kb_cmd(*kb_args),
            "WorkingDirectory": root,
            "StartInterval": interval_minutes * 60,
            "StandardOutPath": os.path.join(log_dir, f"{agent['log_name']}.log"),
            "StandardErrorPath": os.path.join(log_dir, f"{agent['log_name']}.err.log"),
            # see _service_path_env() -- launchd's default PATH has no Homebrew
            "EnvironmentVariables": {"PATH": _service_path_env()},
        }
        with open(plist_path, "wb") as f:
            plistlib.dump(plist, f)

        error = _launchd_load(agent["launchd_label"], plist_path)
        if error:
            print(f"error: launchctl bootstrap failed: {error}", file=sys.stderr)
            return 1
        print(f"installed and started {agent['launchd_label']} -> `kb {shown}` every {interval_minutes}m")
        print(f"  repo:  {root}")
        print(f"  plist: {plist_path}")
        print(f"  logs:  {log_dir}/{agent['log_name']}.log")
        return 0

    # systemd --user: a oneshot service triggered by a timer
    service_path = _agent_systemd_unit_path(name)
    timer_path = _agent_systemd_timer_path(name)
    os.makedirs(os.path.dirname(service_path), exist_ok=True)
    service_unit = (
        "[Unit]\n"
        f"Description={description}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={shlex.join(proc.kb_cmd(*kb_args))}\n"
        f"WorkingDirectory={root}\n"
        f'Environment="PATH={_service_path_env()}"\n'
    )
    with open(service_path, "w", encoding="utf-8") as f:
        f.write(service_unit)
    timer_unit = (
        "[Unit]\n"
        f"Description=run {agent['systemd_unit']} on an interval\n"
        "\n"
        "[Timer]\n"
        f"OnBootSec={interval_minutes}min\n"
        f"OnUnitActiveSec={interval_minutes}min\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    with open(timer_path, "w", encoding="utf-8") as f:
        f.write(timer_unit)
    error = _systemd_enable_now(agent["systemd_timer"])
    if error:
        print(f"error: systemctl enable --now failed: {error}", file=sys.stderr)
        return 1
    print(f"installed and started {agent['systemd_timer']} -> `kb {shown}` every {interval_minutes}m")
    print(f"  repo:    {root}")
    print(f"  service: {service_path}")
    print(f"  timer:   {timer_path}")
    print(f"  logs:    journalctl --user -u {agent['systemd_unit']} -f")
    note = _linger_note()
    if note:
        print(note)
    return 0


def _interval_service_uninstall(kind, name):
    agent = AGENTS[name]
    if kind == "launchd":
        plist_path = _agent_launchd_plist_path(name)
        if _launchd_unload_and_remove(agent["launchd_label"], plist_path):
            print(f"stopped and removed {agent['launchd_label']} ({plist_path})")
        else:
            print(f"{agent['launchd_label']} wasn't installed")
        return 0

    service_path = _agent_systemd_unit_path(name)
    timer_path = _agent_systemd_timer_path(name)
    if _systemd_disable_and_remove(agent["systemd_timer"], timer_path, service_path):
        print(f"stopped and removed {agent['systemd_timer']} ({timer_path})")
    else:
        print(f"{agent['systemd_timer']} wasn't installed")
    return 0


def _interval_service_status(kind, name, install_cmd):
    info = _service_state(kind, name)
    if not info["installed"]:
        print(info["detail"])
        return 1
    if kind == "launchd":
        if info["detail"]:
            print(info["detail"].replace("`install`", f"`{install_cmd}`"))
            return 1
        # an interval job is only "running" while it's actually firing, so scheduled is healthy
        print(f"installed and scheduled ({_agent_launchd_plist_path(name)})")
        return 0
    print(f"installed ({_agent_systemd_timer_path(name)}), timer {info['detail'] or 'active'}")
    return 0 if info["running"] else 1


def _sync_service_uninstall(kind):
    return _interval_service_uninstall(kind, "sync")


def _sync_service_status(kind):
    return _interval_service_status(kind, "sync", "kb sync-service install")
