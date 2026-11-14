"""Desktop notifications for `kb remind`: the macOS "kb reminders" app bundle, terminal-notifier,
osascript and notify-send fallbacks, and the permission check."""
import os
import re
import shutil
import subprocess
import sys
import tempfile


PKG_DIR = os.path.dirname(os.path.abspath(__file__))


# --- how it works --------------------------------------------------
# macOS shows a notification's icon and name from the *app bundle* that posted it,
# and `osascript` is always "Script Editor". To get kb's own icon and its own
# entry in System Settings -> Notifications, `kb remind install` builds a copy of
# terminal-notifier.app ("kb reminders.app", its own bundle id and icon) in
# ~/Applications and posts through that. Without terminal-notifier, or on Linux,
# it falls back to osascript / notify-send.

NOTIFIER_BUNDLE_ID = "dev.pkb-cli.notifier"
NOTIFICATION_SETTINGS_URL = "x-apple.systempreferences:com.apple.Notifications-Settings.extension"


def _notifier_app_path():
    return os.path.expanduser("~/Applications/kb reminders.app")


def _notifier_binary():
    """Path to the built "kb reminders" notifier, or None (not macOS / not built)."""
    path = os.path.join(_notifier_app_path(), "Contents", "MacOS", "terminal-notifier")
    return path if sys.platform == "darwin" and os.path.exists(path) else None


def _terminal_notifier_app():
    """The stock terminal-notifier.app (e.g. brew's) to copy from, or None."""
    exe = shutil.which("terminal-notifier")
    if not exe:
        return None
    for path in (exe, os.path.realpath(exe)):
        candidate = os.path.join(os.path.dirname(os.path.dirname(path)), "terminal-notifier.app")
        if os.path.isdir(candidate):
            return candidate
    return None


def build_notifier_app():
    """Build ~/Applications/kb reminders.app: a copy of terminal-notifier.app with
    kb's icon and its own bundle id (which is what gives it its own icon, name and
    notification permission). Skips the rebuild when the copy already matches the
    source and icon -- re-signing changes the code signature, which can make macOS
    forget a permission the user already granted. Returns (ok, message)."""
    if sys.platform != "darwin":
        return False, "the kb reminders app is macOS-only"
    src = _terminal_notifier_app()
    if src is None:
        return False, "terminal-notifier isn't installed -- `brew install terminal-notifier`"
    icon_png = os.path.join(PKG_DIR, "templates", "notifier", "kb-reminder.png")
    dest = _notifier_app_path()

    import hashlib
    import plistlib
    with open(os.path.join(src, "Contents", "Info.plist"), "rb") as f:
        src_version = plistlib.load(f).get("CFBundleShortVersionString", "?")
    with open(icon_png, "rb") as f:
        stamp = f"{src_version}+{hashlib.sha256(f.read()).hexdigest()[:12]}"
    dest_plist = os.path.join(dest, "Contents", "Info.plist")
    try:
        with open(dest_plist, "rb") as f:
            if plistlib.load(f).get("KBBuiltFrom") == stamp and _notifier_binary():
                return True, f"kb reminders app is up to date ({dest})"
    except (OSError, plistlib.InvalidFileException):
        pass

    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "kb.iconset")
        os.makedirs(iconset)
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                r = subprocess.run(["sips", "-z", str(size * scale), str(size * scale), icon_png,
                                    "--out", os.path.join(iconset, name)], capture_output=True, text=True)
                if r.returncode != 0:
                    return False, f"sips failed: {r.stderr.strip() or r.stdout.strip()}"
        icns = os.path.join(tmp, "kb-reminder.icns")
        r = subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icns], capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"iconutil failed: {r.stderr.strip() or r.stdout.strip()}"

        staged = os.path.join(tmp, "kb reminders.app")
        shutil.copytree(src, staged, symlinks=True)
        resources = os.path.join(staged, "Contents", "Resources")
        for name in os.listdir(resources):
            if name.endswith(".icns"):
                os.remove(os.path.join(resources, name))
        shutil.copy(icns, os.path.join(resources, "kb-reminder.icns"))
        plist_path = os.path.join(staged, "Contents", "Info.plist")
        with open(plist_path, "rb") as f:
            info = plistlib.load(f)
        info.update({"CFBundleIdentifier": NOTIFIER_BUNDLE_ID, "CFBundleName": "kb reminders",
                     "CFBundleIconFile": "kb-reminder", "KBBuiltFrom": stamp})
        with open(plist_path, "wb") as f:
            plistlib.dump(info, f)
        r = subprocess.run(["codesign", "--force", "--deep", "-s", "-", staged], capture_output=True, text=True)
        if r.returncode != 0:
            return False, f"codesign failed: {r.stderr.strip()}"

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.lexists(dest):
            shutil.rmtree(dest)
        shutil.move(staged, dest)
    return True, f"built the kb reminders app ({dest})"


def notifier_authorization():
    """The "kb reminders" app's notification permission as macOS reports it --
    'authorized', 'denied', 'not requested yet', ... -- or None if there's no built
    notifier to ask. Read-only: asking doesn't prompt the user."""
    exe = _notifier_binary()
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "-diagnose"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^\s*authorization\s+(.+?)\s*$", out, re.M)
    return m.group(1).lower() if m else None


def notifier_allowed(authorization):
    return authorization is not None and (authorization.startswith("authorized") or authorization == "provisional")


def notify_status(title, message, group=None, fallback=True):
    """Post a desktop notification and say what happened: 'sent', 'denied' (macOS
    permission for the kb reminders app is off), 'failed', or 'unavailable' (no
    notifier at all). Text goes in as argv, never interpolated into a script, so a
    todo title can't break out of one. With fallback=False the kb reminders app is
    the only thing tried, so a permission problem isn't hidden by osascript working."""
    status = "unavailable"
    exe = _notifier_binary()
    if exe:
        cmd = [exe, "-title", title, "-message", message] + (["-group", group] if group else [])
        code = subprocess.run(cmd, capture_output=True).returncode
        if code == 0:
            return "sent"
        status = "denied" if code == 3 else "failed"
        if not fallback:
            return status
    if sys.platform == "darwin" and shutil.which("osascript"):
        cmd = ["osascript", "-e", "on run argv", "-e",
               "display notification (item 1 of argv) with title (item 2 of argv)",
               "-e", "end run", message, title]
    elif shutil.which("notify-send"):
        cmd = ["notify-send", title, message]
    else:
        return status
    return "sent" if subprocess.run(cmd, capture_output=True).returncode == 0 else "failed"


def notify(title, message):
    """Post a desktop notification; True if one was handed to the OS."""
    return notify_status(title, message) == "sent"


def notification_help():
    return ("macOS isn't allowing notifications from \"kb reminders\" yet.\n"
            "  System Settings -> Notifications -> kb reminders -> Allow Notifications\n"
            "  (if you clicked Don't Allow earlier, this is the only place to undo it, or run:\n"
            f"   tccutil reset UserNotification {NOTIFIER_BUNDLE_ID})\n"
            "  then check with: kb remind test")


def open_notification_settings(force=False):
    """Open System Settings at the Notifications pane. By default only for a person at
    a terminal (a background job opening windows would be rude); kb web passes
    force=True because the person asked by clicking a button. True if it was opened."""
    if sys.platform == "darwin" and (force or sys.stdout.isatty()):
        return subprocess.run(["open", NOTIFICATION_SETTINGS_URL], capture_output=True).returncode == 0
    return False


def send_test_notification():
    """Post a test notification through the same path real reminders use and return
    (ok, message) -- the message says what to do when it didn't work."""
    status = notify_status("kb reminders are on", "You'll get a notification like this when a TODO comes due.",
                            group="kb-remind-test", fallback=False)
    if status == "sent":
        return True, ("sent a test notification -- if you don't see it, a Focus / Do Not Disturb "
                      "mode may be holding it back")
    if status == "denied":
        open_notification_settings()
        return False, notification_help()
    if status == "failed":
        return False, "the notifier ran but failed -- run `kb remind test` again, or see `terminal-notifier -diagnose`"
    if sys.platform == "darwin":
        return False, ("no notifier available -- `brew install terminal-notifier`, then `kb remind install` "
                       "builds the kb reminders app")
    return False, "no notifier available -- install `notify-send` (libnotify)"


def finish_reminder_install():
    """After the reminder timer is installed: on macOS build the kb reminders app
    (when terminal-notifier is there), then send a test notification so a missing
    permission shows up now, with instructions, not silently at the first real
    reminder. Returns (ok, message). Run from the user's terminal -- the permission
    prompt needs a GUI session, which the background job doesn't have."""
    lines = []
    if sys.platform == "darwin":
        if _terminal_notifier_app():
            built, msg = build_notifier_app()
            lines.append(msg)
            if not built:
                lines.append("falling back to osascript notifications (Script Editor's icon)")
        else:
            lines.append("terminal-notifier isn't installed, so notifications will use osascript "
                         "(Script Editor's icon) -- `brew install terminal-notifier` and re-run "
                         "`kb remind install` for kb's own icon")
    ok, msg = send_test_notification()
    lines.append(msg)
    return ok, "\n".join(lines)
