"""TODO logic on top of the bd (beads) CLI: listing, creating, updating, snoozing, due dates, and the reminder de-duplication state."""
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta

from . import pkb_common as pc

def bd_available():
    return shutil.which("bd") is not None


def _bd_run(bd_args):
    """Run a `bd` subcommand, returning its stripped stdout. Raises
    RuntimeError(stderr) on failure. Shared by every bd_* helper below so the
    run/error/strip contract lives in exactly one place."""
    result = subprocess.run(bd_args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def bd_list(root, include_all=False):
    """Open (or all, with include_all) bd issues as parsed JSON, sorted by priority
    and, within one, soonest due first with undated last. Raises RuntimeError(stderr)
    if bd itself errors. Shared by cmd_todo's non-add path and kb web's GET /api/todo."""
    bd_args = ["bd", "-C", root, "list", "--json", "--sort", "priority", "--limit", "0"]
    if include_all:
        bd_args.append("--all")
    issues = json.loads(_bd_run(bd_args))

    def order(issue):
        when = pc.todo_remind_at(issue)
        return (int(issue.get("priority", 2)), when.timestamp() if when else float("inf"))

    # bd only sorts by one key; list.sort is stable, so ties keep bd's order
    return sorted(issues, key=order)


def bd_create(root, title, description=None, priority="2", issue_type="task", labels=None, due=None,
              as_json=False):
    """Create a bd issue, returning bd's own confirmation text (its issue JSON
    with `as_json`). Raises
    RuntimeError(stderr) on failure. Shared by _todo_add (kb todo -a TITLE)
    and kb web's POST /api/todo. `due` is anything resolve_when() accepts."""
    bd_args = ["bd", "-C", root, "create", "--title", title, "--type", issue_type, "--priority", priority]
    if description:
        bd_args += ["--description", description]
    if labels:
        bd_args += ["--labels", labels]
    if due:
        bd_args += ["--due", resolve_when(due)]
    if as_json:
        bd_args.append("--json")
    return _bd_run(bd_args)


def bd_update(root, issue_id, priority=None, issue_type=None, description=None,
              assignee=None, add_label=None, remove_label=None, due=None):
    """Update fields on an existing bd issue, returning bd's own stdout. Raises
    RuntimeError(stderr) on failure. Used by kb web's PATCH /api/todo/{id} for
    editing an already-created todo. add_label/remove_label touch one label at
    a time, mirroring the tag chip add/remove UX used elsewhere in kb web. `due`
    is anything resolve_when() accepts, or "" to clear it; changing it re-arms the
    reminder."""
    bd_args = ["bd", "-C", root, "update", issue_id]
    if priority is not None:
        bd_args += ["--priority", str(priority)]
    if issue_type is not None:
        bd_args += ["--type", issue_type]
    if description is not None:
        bd_args += ["--description", description]
    if assignee is not None:
        bd_args += ["--assignee", assignee]
    if add_label:
        bd_args += ["--add-label", add_label]
    if remove_label:
        bd_args += ["--remove-label", remove_label]
    if due is not None:
        bd_args += ["--due", resolve_when(due) if due else ""]
        reminder_forget(issue_id)
    return _bd_run(bd_args)


def resolve_when(text):
    """Turn a human "when" into something `bd --due/--until` reads correctly.
    bd already takes +6h, +1d, +2w, tomorrow, friday, 5pm, 2026-01-15 and RFC 3339
    timestamps, but reads a bare `m` as *months* -- so minutes are resolved here to
    an absolute local timestamp, and a bare `2h`/`3d`/`1w` gets its missing `+`."""
    text = text.strip()
    m = re.fullmatch(r"\+?(\d+)\s*(m|min|mins|minute|minutes)", text, re.I)
    if m:
        return (datetime.now().astimezone() + timedelta(minutes=int(m.group(1)))).isoformat(timespec="seconds")
    m = re.fullmatch(r"(\d+)\s*([hdw])", text, re.I)
    if m:
        return f"+{m.group(1)}{m.group(2).lower()}"
    return text


def bd_snooze(root, issue_id, until):
    """Hide a todo until `until` (anything resolve_when() accepts), then bring it
    back and remind again. Sets bd's defer date (hides it from `bd ready`, auto-wakes
    it) *and* its due date, so a todo that never had a due date still comes due the
    moment the snooze ends. Returns bd's own stdout; raises RuntimeError on failure."""
    when = resolve_when(until)
    output = _bd_run(["bd", "-C", root, "defer", issue_id, "--until", when])
    _bd_run(["bd", "-C", root, "update", issue_id, "--due", when])
    reminder_forget(issue_id)
    return output


def bd_unsnooze(root, issue_id):
    """End a snooze early, returning bd's own stdout. Raises RuntimeError on failure."""
    return _bd_run(["bd", "-C", root, "undefer", issue_id])


_bd_time = pc.bd_time


def todo_is_snoozed(issue, now=None):
    """True while a todo's snooze is still running (deferred with a future date)."""
    until = _bd_time(issue.get("defer_until"))
    return issue.get("status") == "deferred" and (until is None or until > (now or datetime.now().astimezone()))


def todos_due(issues, now=None):
    """Open todos whose due date has passed, most overdue first. Snoozed todos
    don't count until their snooze ends (bd_snooze moves the due date there too)."""
    now = now or datetime.now().astimezone()
    due = []
    for i in issues:
        when = pc.todo_remind_at(i)
        if when is None or when > now or todo_is_snoozed(i, now):
            continue
        due.append((when, i))
    return [i for _, i in sorted(due, key=lambda pair: pair[0])]


def format_due(issue, now=None):
    """Short human label for a todo's due/snooze state ('due in 2h', 'overdue 3d',
    'snoozed 1d'), or '' when it has neither."""
    now = now or datetime.now().astimezone()
    snoozed = todo_is_snoozed(issue, now)
    when = pc.todo_remind_at(issue)
    if when is None:
        return ""
    seconds = int((when - now).total_seconds())
    span = abs(seconds)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if span >= size:
            amount = f"{span // size}{unit}"
            break
    else:
        amount = "<1m"
    if snoozed:
        return f"snoozed {amount}"
    return f"due in {amount}" if seconds > 0 else f"overdue {amount}"


# Reminder state: when each todo was last announced, so `kb remind check` (run on a
# timer) doesn't re-fire the same notification every few minutes. Lives outside the
# data repo -- it's per-machine and must never be committed.

def _reminder_state_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "pkb-cli", "reminded.json")


def _reminder_load():
    try:
        with open(_reminder_state_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _reminder_save(state):
    path = _reminder_state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)


def reminder_forget(issue_id):
    """Re-arm one todo's reminder (called when its due date or snooze changes)."""
    state = _reminder_load()
    if state.pop(issue_id, None) is not None:
        _reminder_save(state)


def reminders_to_send(issues, repeat_hours=4, now=None):
    """Due todos that haven't been announced yet, or whose last announcement is more
    than `repeat_hours` old -- so an overdue todo keeps nudging until it's closed or
    snoozed, without firing on every timer tick."""
    now = now or datetime.now().astimezone()
    state = _reminder_load()
    out = []
    for i in todos_due(issues, now):
        last = _bd_time(state.get(i["id"]))
        if last is None or now - last >= timedelta(hours=repeat_hours):
            out.append(i)
    return out


def bd_reopen(root, issue_id):
    """Reopen a closed bd issue, returning bd's own stdout. Raises
    RuntimeError(stderr) on failure. Used by kb web's todo action endpoint."""
    return _bd_run(["bd", "-C", root, "reopen", issue_id])


def bd_delete(root, issue_id):
    """Permanently delete a bd issue, returning bd's own stdout. Raises
    RuntimeError(stderr) on failure. Used by kb web's DELETE /api/todo/{id}.
    --force is required since kb web already confirms the action client-side."""
    return _bd_run(["bd", "-C", root, "delete", issue_id, "--force"])


def bd_deps(root, issue_id, direction="down"):
    """List an issue's dependencies (direction='down', what it's blocked by) or
    dependents (direction='up', what it blocks) as parsed JSON. Raises
    RuntimeError(stderr) on failure. Used by kb web's GET /api/todo/{id}/deps."""
    bd_args = ["bd", "-C", root, "dep", "list", issue_id, "--direction", direction, "--json"]
    stdout = _bd_run(bd_args)
    return json.loads(stdout) if stdout else []


def bd_dep_add(root, from_id, to_id):
    """Record that `from_id` depends on (is blocked by) `to_id`, returning bd's
    own stdout. Raises RuntimeError(stderr) on failure."""
    return _bd_run(["bd", "-C", root, "dep", "add", from_id, to_id])


def bd_dep_remove(root, from_id, to_id):
    """Remove the dependency recorded by bd_dep_add(from_id, to_id), returning
    bd's own stdout. Raises RuntimeError(stderr) on failure."""
    return _bd_run(["bd", "-C", root, "dep", "remove", from_id, to_id])


def bd_action(root, action, issue_id, comment_text=None):
    """Run `bd show|close|comment|reopen` for one issue, returning bd's own
    stdout. Raises RuntimeError(stderr) on failure. Used by kb web's todo
    action endpoint -- the CLI's interactive picker (_todo_interactive) calls
    bd directly via subprocess instead, since it streams straight to the
    terminal rather than needing a return value."""
    bd_args = ["bd", "-C", root, action, issue_id]
    if action == "comment":
        bd_args.append(comment_text or "")
    result = subprocess.run(bd_args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()
