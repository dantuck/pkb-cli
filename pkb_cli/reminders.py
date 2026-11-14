"""Installing, inspecting and summarising the TODO reminder timer."""
import shutil
import sys

from . import notifier
from . import service


def reminders_install(root, interval_minutes):
    """Install the reminder timer and run the post-install notification check
    (`kb remind install`, minus the printing of the check's result). Returns
    (returncode, notifications_ok, message) -- message is the post-install guidance, empty
    if the timer itself couldn't be installed. Shared by the CLI and kb web."""
    kind = service._service_kind()
    if kind is None:
        print(f"error: reminders can't be scheduled on {sys.platform}", file=sys.stderr)
        return 1, False, ""
    rc = service._interval_service_install(kind, "remind", root, ["remind", "check"], interval_minutes,
                                    "kb remind -- notify about due TODOs")
    if rc != 0:
        return rc, False, ""
    ok, msg = notifier.finish_reminder_install()
    return 0, ok, msg


def reminders_info():
    """Structured reminder state for kb web's Reminders card: the timer
    ({installed, running, detail, interval_minutes}) and, on macOS, the "kb reminders"
    notifier ({app_built, terminal_notifier, authorization, allowed}). Read-only --
    never prompts or notifies."""
    kind = service._service_kind()
    if kind is None:
        return {"supported": False}
    out = {"supported": True, "kind": kind, **service._service_state(kind, "remind")}
    out["healthy"], out["message"] = reminder_status_line(kind)
    out["interval_minutes"] = service._timer_interval_minutes(kind, "remind") if out["installed"] else None
    if kind == "launchd":
        auth = notifier.notifier_authorization()
        out["notifier"] = {"app_built": notifier._notifier_binary() is not None,
                           "terminal_notifier": notifier._terminal_notifier_app() is not None,
                           "authorization": auth, "allowed": notifier.notifier_allowed(auth)}
    return out


def reminder_status_line(kind=None):
    """(ok, message) describing the `kb remind` timer for setup/doctor -- ok is True
    when it's scheduled and healthy, False when it needs attention or isn't on.
    Read-only; never installs anything."""
    kind = kind or service._service_kind()
    if kind is None:
        return False, f"reminders can't be scheduled on {sys.platform} -- run `kb remind check` from your own scheduler"
    info = service._service_state(kind, "remind")
    if not info["installed"]:
        return False, ("TODO reminders aren't scheduled (optional) -- `kb remind install` sends a desktop "
                       "notification when a TODO with a due date comes due")
    if kind == "launchd" and info["detail"]:
        return False, f"reminder job is {info['detail']}"
    if kind == "systemd" and not info["running"]:
        return False, f"reminder timer is {info['detail'] or 'inactive'} -- run `kb remind install` again"
    if kind == "launchd" and notifier._notifier_binary():
        auth = notifier.notifier_authorization()
        if not notifier.notifier_allowed(auth):
            return False, (f"reminders are scheduled but notifications for \"kb reminders\" are "
                           f"{auth or 'in an unknown state'} -- System Settings -> Notifications -> "
                           f"kb reminders -> Allow, then `kb remind test`")
    elif kind == "launchd" and notifier._terminal_notifier_app():
        return False, ("terminal-notifier is installed but the \"kb reminders\" app isn't built -- run "
                       "`kb remind install` (gives notifications kb's own icon)")
    else:
        notifier_tool = "osascript" if kind == "launchd" else "notify-send"
        if not shutil.which(notifier_tool):
            return False, f"reminders are scheduled but `{notifier_tool}` isn't installed, so no notification can appear"
    return True, "TODO reminders are scheduled (`kb remind status`)"
