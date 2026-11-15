#!/usr/bin/env python3
"""kb — thin CLI wrapper dispatching to sibling scripts in this tool repo. No daemon;
one-shot per invocation. Operates on a data repo (found by walking up from cwd to a
.pkb/ directory), which lives separately from wherever this tool is installed."""
import argparse
import contextlib
import difflib
import getpass
import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta

from . import index_fts
from . import pkb_common as pc
from . import remind_push
from . import notifier
from . import proc
from . import service
from . import health
from . import journal
from . import project
from . import projdocs
from . import reminders
from . import sources
from . import todos
from . import updater
from . import worker_deploy
from . import search
from . import semantic
# Re-exported because kb_web calls these as `_kb().<name>` (see tests/test_module_contracts.py).
from .notifier import open_notification_settings, send_test_notification  # noqa: F401
from .todos import (  # noqa: F401
    bd_action, bd_available, bd_create, bd_delete, bd_dep_add, bd_dep_remove, bd_deps, bd_list, bd_snooze, bd_unsnooze, bd_update,
)
from .health import doctor_checks, push_status  # noqa: F401
from .sources import discover_sync_sources  # noqa: F401
from .reminders import reminders_info, reminders_install  # noqa: F401
from .journal import journal_append, journal_months  # noqa: F401
from .service import cmd_sync_service, service_info  # noqa: F401
from .updater import apply_update, schedule_kb_command, update_info  # noqa: F401
from . import __version__
from .pkb_entries import (  # noqa: F401  (re-exported)
    _all_tags, _apply_tag_changes, _clean_list, _find_entry, _iter_all_entries, _reindex_fast,
    _readonly_error, _readonly_guard, _readonly_source_info, entry_delete,
    entry_move, entry_new, entry_set_links, entry_set_tags, entry_update_content,
    inbox_list,
)

PKG_DIR = os.path.dirname(os.path.abspath(__file__))


class KBArgumentParser(argparse.ArgumentParser):
    """argparse.ArgumentParser that gives an unknown top-level command a short,
    readable error (with a 'did you mean' suggestion) instead of the default
    'invalid choice: ... (choose from ...)' wall of text listing every command
    on one line."""

    _INVALID_COMMAND_RE = re.compile(r"^argument <command>: invalid choice: '(.+?)'")

    def __init__(self, *args, subcommands=None, **kwargs):
        self._subcommands = subcommands or []
        super().__init__(*args, **kwargs)

    def error(self, message):
        m = self._INVALID_COMMAND_RE.match(message)
        if not m:
            super().error(message)
        bad = m.group(1)
        self.print_usage(sys.stderr)
        suggestion = difflib.get_close_matches(bad, self._subcommands, n=1)
        print(f"kb: unknown command '{bad}'" +
              (f" -- did you mean '{suggestion[0]}'?" if suggestion else ""),
              file=sys.stderr)
        print("run `kb help` to see all commands", file=sys.stderr)
        sys.exit(2)


def cmd_search(args):
    root = pc.get_repo_root()
    notes = []
    mode = "keyword" if args.keyword else "semantic" if args.semantic else "auto"
    try:
        rows = search.search_entries(root, args.query, entry_type=args.type, tag=args.tag,
                               include_all=args.all, mode=mode, notes=notes)
    except sqlite3.DatabaseError as e:  # a bad query (OperationalError) or a corrupt index
        return _fail(str(e) if isinstance(e, index_fts.IndexCorrupt) else f"search error: {e}", args.json)
    except semantic.SemanticUnavailable as e:
        return _fail(f"semantic search unavailable: {e}", args.json)
    finally:
        for note in notes:
            print(note, file=sys.stderr)
    if rows is None:
        print("no FTS index found. Run `kb index` first, or use ripgrep directly:")
        print(f"  rg --heading -n {args.query!r} tutorials how-to reference explanation")
        return 1

    if not rows:
        if args.json:
            print("[]")
            return 0
        print("no results")
        return 0

    if args.json:
        print(json.dumps([
            {"id": entry_id, "title": title, "type": entry_type, "path": path}
            for entry_id, title, entry_type, path, _rank in rows
        ]))
        return 0

    # interactive by default when the tooling supports it, same rule as kb todo:
    # fzf installed, both ends real terminals (never for piped/scripted use).
    interactive = not args.plain and shutil.which("fzf") and sys.stdout.isatty() and sys.stdin.isatty()
    if interactive:
        return _search_interactive(root, rows)

    for entry_id, title, entry_type, path, _rank in rows:
        print(f"[{entry_type:11}] {entry_id}  {title}\n    {path}")
    if not shutil.which("fzf"):
        print("\n(install fzf for an interactive picker)")
    return 0


def _fzf_select(lines, prompt, header, delimiter=None, with_nth=None,
                 preview_cmd=None, preview_window="right:60%", multi=False):
    """Run fzf over `lines`, returning the raw selected line, or None if
    cancelled (esc/ctrl-c) or nothing was selected. Shared by every
    interactive picker in this file -- search/inbox/todo/journal each used to
    hand-roll this same subprocess.run + cancel-check.

    With multi=True (tab to toggle multiple matches in fzf), returns a list of
    selected lines instead of a single string, or None if nothing was picked."""
    cmd = ["fzf", "--prompt", prompt, "--header", header]
    if multi:
        cmd.append("--multi")
    if delimiter is not None:
        cmd += ["--delimiter", delimiter]
    if with_nth is not None:
        cmd += ["--with-nth", with_nth]
    if preview_cmd is not None:
        cmd += ["--preview", preview_cmd, "--preview-window", preview_window]
    result = subprocess.run(cmd, input="\n".join(lines), text=True, stdout=subprocess.PIPE)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    out = result.stdout.rstrip("\n")
    return out.split("\n") if multi else out


def _search_interactive(root, rows):
    """fzf-based picker: select a result (with a live file-content preview), then
    an action -- view (print full content), edit ($EDITOR), links (forward/back),
    or cancel. Same two-stage shape as kb todo's picker, for consistency."""
    lines, by_id = [], {}
    for entry_id, title, entry_type, path, _rank in rows:
        abs_path = os.path.join(root, path)
        lines.append(f"{entry_id}\t{entry_type}\t{title}\t{abs_path}")
        by_id[entry_id] = (title, abs_path)

    selected = _fzf_select(lines, "search> ", "enter: choose an action for this result",
                            delimiter="\t", with_nth="1,2,3", preview_cmd="cat {4}")
    if selected is None:
        return 0  # cancelled
    fields = selected.split("\t")
    chosen_id, chosen_path = fields[0], fields[3]
    title = by_id.get(chosen_id, ("", chosen_path))[0]

    choice = _fzf_select(["view", "edit", "links", "cancel"],
                          f"{chosen_id}> ", f"{title} -- choose an action")
    if choice is None:
        return 0

    if choice == "view":
        subprocess.run(["cat", chosen_path])
    elif choice == "edit":
        editor = pc.get_editor()
        parts = pc.resolve_editor_argv(editor) if editor else None
        if not parts:
            print(f"no editor available -- set $EDITOR or run `kb config editor <cmd>` -- {chosen_path}",
                  file=sys.stderr)
        else:
            try:
                subprocess.run(parts + [chosen_path])
            except FileNotFoundError:
                print(f"error: editor '{parts[0]}' not found on PATH -- fix it with `kb config editor <cmd>`",
                      file=sys.stderr)
    elif choice == "links":
        show_links(root, chosen_id)
    return 0


_fail = pc.report_error
_emit = pc.emit_result


def cmd_new(args):
    root = pc.get_repo_root()
    tags = args.tags.split(",") if args.tags else []
    links = args.links.split(",") if args.links else []
    result = entry_new(root, args.type, args.title, tags=tags, links=links, body=args.body)
    if "error" in result:
        return _fail(result["error"], args.json)
    _emit(args.json, {"id": result["id"], "path": result["path"], "type": args.type,
                      "title": args.title}, result["path"])
    return 0


def _open_in_editor(file_path):
    editor = pc.get_editor()
    parts = pc.resolve_editor_argv(editor) if editor else None
    if not parts:
        print(f"no editor available -- set $EDITOR or run `kb config editor <cmd>` -- {file_path}",
              file=sys.stderr)
        return
    sys.stdout.flush()  # os.execvp below replaces this process without flushing buffered stdout
    try:
        os.execvp(parts[0], parts + [file_path])  # replaces this process; nothing after this runs
    except FileNotFoundError:
        print(f"error: editor '{parts[0]}' not found on PATH -- fix it with `kb config editor <cmd>` "
              f"-- {file_path}", file=sys.stderr)
        sys.exit(1)


def _journal_add(root, target, text, json_out=False):
    result = journal.journal_append(root, target, text)
    if result is None:
        _emit(json_out, {"added": False}, "empty -- nothing added")
        return 0
    _emit(json_out, {"added": True, "date": target.isoformat(), **result}, f"added -> {result['path']}")
    return 0


def _journal_add_editor(root, target, json_out=False):
    """Open a blank scratch buffer in $EDITOR for multiline input, then append
    whatever was written to target's journal entry -- like `git commit` with no
    -m, but scoped to a throwaway temp file so the entry's frontmatter (and any
    earlier entries in the file) never has to be touched or scrolled past."""
    editor = pc.get_editor()
    parts = pc.resolve_editor_argv(editor) if editor else None
    if not parts:
        print("no editor available -- set $EDITOR or run `kb config editor <cmd>`", file=sys.stderr)
        return 1

    fd, tmp_path = tempfile.mkstemp(prefix="kb-journal-", suffix=".md")
    os.close(fd)
    try:
        try:
            subprocess.run(parts + [tmp_path], check=True)
        except FileNotFoundError:
            print(f"error: editor '{parts[0]}' not found on PATH -- fix it with `kb config editor <cmd>`",
                  file=sys.stderr)
            return 1
        except subprocess.CalledProcessError:
            print("editor exited with an error -- nothing added", file=sys.stderr)
            return 1
        with open(tmp_path, "r", encoding="utf-8") as f:
            text = f.read()
    finally:
        os.remove(tmp_path)

    return _journal_add(root, target, text, json_out)


def cmd_journal(args):
    root = pc.get_repo_root()

    if args.date == "rollup":
        return _journal_rollup(root, args.month, args.json)
    if args.tag:
        return _journal_by_tag(root, args.tag)

    explicit_date = args.date not in (None, "today")
    target = (datetime.now().date() if args.date in (None, "today")
              else datetime.strptime(args.date, "%Y-%m-%d").date())

    if args.add is not None:
        if args.add == "":  # -m/--add with no TEXT: open a scratch buffer instead
            return _journal_add_editor(root, target, args.json)
        if args.date is None and journal._looks_like_date(args.add):
            # `-m` typed before DATE: argparse's nargs='?' greedily consumes the
            # date token as -m's own TEXT instead of leaving it for the `date`
            # positional. Silently writing to today under those circumstances
            # would put the note on the wrong day with no indication anything
            # went wrong -- refuse instead, and say what order actually works.
            return _fail(f"`-m {args.add}` was parsed as the note text, not a date -- "
                         f"put the date first: `kb journal {args.add} -m ...`", args.json)
        return _journal_add(root, target, args.add, args.json)

    if not explicit_date:
        # only the default "today" invocation gets the interactive picker -- an
        # explicit date means the user already knows exactly what they want open.
        interactive = not args.plain and shutil.which("fzf") and sys.stdout.isatty() and sys.stdin.isatty()
        if interactive:
            return _journal_interactive(root)

    file_path = journal._ensure_journal_entry(root, target)

    if not explicit_date:
        # no fzf/terminal for a full picker -- still surface nearby entries and
        # this-day-in-the-past ones for context instead of silently jumping
        # straight into today's.
        on_this_day = journal._on_this_day(root, target)
        if on_this_day:
            print("on this day:")
            for label, date_str, path in on_this_day:
                print(f"  {label:12} {date_str}  {os.path.relpath(path, root)}")
            print()

        recent = [(d, p) for d, p in journal.list_journal_entries(root, limit=6) if d != target.isoformat()][:5]
        if recent:
            print("recent entries:")
            for date_str, path in recent:
                print(f"  {date_str}  {os.path.relpath(path, root)}")
            print()

    _open_in_editor(file_path)
    return 0


def _journal_interactive(root):
    """fzf-based picker over journal entries: pick one (with a live content
    preview) to open in $EDITOR. Today is included even if it doesn't exist yet
    (tagged "new"), so this always offers a way to start today's entry too --
    same two-stage-friendly shape as the other pickers, minus the action step,
    since there's only one thing to do with a journal entry: open it."""
    today = datetime.now().date()
    today_iso = today.isoformat()
    entries = journal.list_journal_entries(root)
    if today_iso not in {d for d, _ in entries}:
        entries = [(today_iso, journal._journal_path(root, today))] + entries

    on_this_day = journal._on_this_day(root, today)
    lines = [f"{date_str}\t{label}\t{path}" for label, date_str, path in on_this_day]
    for date_str, path in entries:
        tag = "new" if not os.path.exists(path) else ""
        lines.append(f"{date_str}\t{tag}\t{path}")

    selected = _fzf_select(lines, "journal> ", "enter: open this entry (created if new)",
                            delimiter="\t", with_nth="1,2",
                            preview_cmd="cat {3} 2>/dev/null || echo '(no entry yet)'")
    if selected is None:
        return 0  # cancelled

    date_str = selected.split("\t", 1)[0].strip()
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    file_path = journal._ensure_journal_entry(root, target)
    _open_in_editor(file_path)
    return 0


def _journal_by_tag(root, tag):
    """List journal entries carrying `tag`, newest first -- browsing a theme
    across time (e.g. everything tagged "homelab") rather than kb search's
    relevance ranking, which scatters same-theme entries by match strength
    instead of keeping them in chronological order."""
    matches = []
    for date_str, path in journal.list_journal_entries(root):
        fm, _ = pc.read_entry(path)
        if tag in (fm.get("tags") or []):
            matches.append((date_str, path))

    if not matches:
        print(f"no journal entries tagged '{tag}'")
        return 0
    for date_str, path in matches:
        print(f"{date_str}  {os.path.relpath(path, root)}")
    return 0


def _rollup_month(month_arg):
    """(year, month) for `month_arg` (YYYY-MM), or the previous calendar month
    if not given -- the current month is usually still in progress, so it's a
    poor default for "summarize what happened"."""
    if month_arg:
        try:
            dt = datetime.strptime(month_arg, "%Y-%m")
        except ValueError:
            return None
        return dt.year, dt.month
    today = datetime.now().date()
    year, month = today.year, today.month - 1
    if month == 0:
        year, month = year - 1, 12
    return year, month


def _journal_rollup(root, month_arg, json_out=False):
    """Generate/refresh reference/journal-summaries/YYYY-MM.md: every day's
    entry in the month concatenated under a heading, plus a tag-frequency line,
    so a month of daily files can be browsed as one page instead of opened one
    by one. Deliberately not AI summarization -- just the raw entries assembled
    in one place; the point is browsability, not compression."""
    parsed = _rollup_month(month_arg)
    if parsed is None:
        return _fail(f"invalid month '{month_arg}' -- expected YYYY-MM", json_out)
    year, month = parsed
    month_label = f"{year:04d}-{month:02d}"

    month_dir = os.path.join(root, "journal", f"{year:04d}", f"{month:02d}")
    if not os.path.isdir(month_dir):
        return _fail(f"no journal entries found for {month_label}", json_out)

    days = []
    for name in sorted(os.listdir(month_dir)):
        if not name.endswith(".md"):
            continue
        fm, body = pc.read_entry(os.path.join(month_dir, name))
        days.append((name[:-3], fm, body))
    if not days:
        return _fail(f"no journal entries found for {month_label}", json_out)

    tag_counts, day_ids, sections = {}, [], []
    for date_str, fm, body in days:
        if fm.get("id"):
            day_ids.append(fm["id"])
        for t in fm.get("tags") or []:
            tag_counts[t] = tag_counts.get(t, 0) + 1

        text = body.strip("\n")
        heading = f"# {date_str}"
        if text.startswith(heading):
            text = text[len(heading):].lstrip("\n")
        sections.append(f"## {date_str}\n\n{text.strip() or '(no content)'}\n")

    out_path = os.path.join(root, "reference", "journal-summaries", f"{month_label}.md")
    now = pc.now_iso()
    existing_fm = pc.read_entry(out_path)[0] if os.path.exists(out_path) else None
    if existing_fm and existing_fm.get("id"):
        entry_id, created = existing_fm["id"], existing_fm.get("created", now)
    else:
        entry_id = pc.gen_id(pc.collect_existing_ids(root), dt=datetime(year, month, 1))
        created = now

    fm = {
        "id": entry_id,
        "created": created,
        "updated": now,
        "type": "reference",
        "extension": None,
        "source": "manual",
        "source_id": None,
        "tags": ["journal-summary"],
        "links": day_ids,
        "title": f"Journal summary: {month_label}",
    }

    body = f"# Journal summary: {month_label}\n\n{len(days)} entries.\n"
    if tag_counts:
        top = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        body += "tags: " + ", ".join(f"{t} ({n})" for t, n in top) + "\n"
    body += "\n---\n\n" + "\n".join(sections)

    pc.write_entry(out_path, fm, body)
    _reindex_fast(root)
    _emit(json_out, {"id": entry_id, "path": out_path, "month": month_label, "entries": len(days)}, out_path)
    return 0


def cmd_inbox(args):
    root = pc.get_repo_root()
    inbox_dir = os.path.join(root, "inbox")

    if args.id is not None:
        return _inbox_act(root, inbox_dir, args)

    if args.json:
        print(json.dumps(inbox_list(root)))
        return 0

    now = datetime.now().astimezone()
    files = sorted(
        f for f in os.listdir(inbox_dir) if f.endswith(".md")
    ) if os.path.isdir(inbox_dir) else []
    if not files:
        print("inbox is empty")
        return 0

    items = []
    for name in files:
        path = os.path.join(inbox_dir, name)
        try:
            fm, _ = pc.read_entry(path)
            created = fm.get("created")
            title = fm.get("title") or name
        except ValueError:
            created = None
            title = name
        created_dt = pc.parse_iso(created)
        age = (now - created_dt).days if created_dt else "?"
        items.append((name, age, title))

    interactive = not args.plain and shutil.which("fzf") and sys.stdout.isatty() and sys.stdin.isatty()
    if interactive:
        return _inbox_interactive(root, items)

    for name, age, title in items:
        print(f"{age!s:>4}d  {name:<30} {title}")
    if not shutil.which("fzf"):
        print("\n(install fzf for an interactive picker: promote/redirect/discard)")
    return 0


def _inbox_act(root, inbox_dir, args):
    """Non-interactive triage: `kb inbox <id> promote|redirect|discard`. The
    only way to resolve an inbox item when fzf/a real terminal aren't
    available -- e.g. scripted use or an AI agent driving kb, neither of which
    ever gets the interactive picker."""
    if args.action is None:
        return _fail(f"`kb inbox {args.id}` needs an action: promote <type> | redirect | discard", args.json)
    found = _inbox_find(inbox_dir, args.id)
    if found is None:
        return _fail(f"no inbox item with id '{args.id}'", args.json)
    path, fm, body = found

    if args.action == "promote":
        if not args.type:
            return _fail(f"`kb inbox {args.id} promote` needs a Diataxis type: {', '.join(pc.CORE_TYPES)}",
                         args.json)
        return _inbox_promote(root, path, fm, body, args.type, args.json)
    elif args.action == "redirect":
        return _inbox_redirect(root, path, fm, body, args.json)
    else:
        return _inbox_discard(root, path, args.json)


def _inbox_promote(root, path, fm, body, target_type, json_out=False):
    """Move an inbox item into core content as `target_type`, clearing
    `extension`. Shared by the interactive picker and non-interactive
    `kb inbox <id> promote <type>`."""
    title = fm.get("title") or os.path.basename(path)
    fm["type"] = target_type
    fm["extension"] = None
    fm["updated"] = pc.now_iso()
    new_path = os.path.join(root, pc.TYPE_DIR[target_type], f"{fm['id']}-{pc.slugify(title)}.md")
    pc.write_entry(new_path, fm, body)
    os.remove(path)
    rel = os.path.relpath(new_path, root)
    _emit(json_out, {"action": "promote", "id": fm["id"], "type": target_type, "path": rel},
          f"promoted -> {rel}")
    _reindex_fast(root)
    pc.git_autocommit(root, f"inbox promote: {title} -> {target_type}")
    return 0


def _inbox_redirect(root, path, fm, body, json_out=False):
    """Append an inbox item into its created-date's journal entry (creating
    that entry first if needed) and remove it from inbox/. Shared by the
    interactive picker and non-interactive `kb inbox <id> redirect`."""
    title = fm.get("title") or os.path.basename(path)
    created_dt = pc.parse_iso(fm.get("created")) or datetime.now().astimezone()
    journal_dir = os.path.join(root, "journal", f"{created_dt.year:04d}", f"{created_dt.month:02d}")
    journal_path = os.path.join(journal_dir, f"{created_dt.date().isoformat()}.md")
    if not os.path.exists(journal_path):
        all_ids = pc.collect_existing_ids(root)
        jid = pc.gen_id(all_ids, dt=created_dt)
        now_s = pc.now_iso()
        jfm = {
            "id": jid, "created": now_s, "updated": now_s,
            "type": "journal", "extension": "journal", "source": "manual",
            "source_id": None, "tags": [], "links": [],
            "title": created_dt.date().isoformat(),
        }
        pc.write_entry(journal_path, jfm, f"# {created_dt.date().isoformat()}\n\n")
    with open(journal_path, "r", encoding="utf-8") as f:
        existing = f.read()
    with open(journal_path, "w", encoding="utf-8") as f:
        f.write(existing.rstrip("\n") + f"\n\n## {title}\n\n{body.strip()}\n")
    os.remove(path)
    rel = os.path.relpath(journal_path, root)
    _emit(json_out, {"action": "redirect", "id": fm.get("id"), "path": rel}, f"redirected -> {rel}")
    _reindex_fast(root)
    pc.git_autocommit(root, f"inbox redirect: {title}")
    return 0


def _inbox_discard(root, path, json_out=False):
    name = os.path.basename(path)
    os.remove(path)
    _emit(json_out, {"action": "discard", "name": name}, f"discarded {name}")
    _reindex_fast(root)
    pc.git_autocommit(root, f"inbox discard: {name}")
    return 0


def _inbox_find(inbox_dir, item_id):
    """(path, fm, body) for the inbox item with this id, or None. Inbox items
    are addressed by id here, same as kb tag/kb link, not by filename."""
    if not os.path.isdir(inbox_dir):
        return None
    for name in os.listdir(inbox_dir):
        if not name.endswith(".md"):
            continue
        path = os.path.join(inbox_dir, name)
        try:
            fm, body = pc.read_entry(path)
        except ValueError:
            continue
        if fm.get("id") == item_id:
            return path, fm, body
    return None


def _inbox_interactive(root, items):
    """fzf-based triage: select an item (live content preview), then promote
    (choose a Diataxis type, moved there with extension cleared), redirect
    (appended into that day's journal entry, per its created date), or discard.
    Every inbox item must end in one of exactly these three states (spec S5) --
    this is what lets kb inbox actually resolve that, not just report on it."""
    inbox_dir = os.path.join(root, "inbox")
    lines = []
    for name, age, title in items:
        lines.append(f"{name}\t{age}d\t{title}")

    preview_cmd = f"cat {shlex.quote(inbox_dir)}/{{1}}"
    selected = _fzf_select(lines, "inbox> ", "enter: promote / redirect / discard this item",
                            delimiter="\t", with_nth="1,2,3", preview_cmd=preview_cmd)
    if selected is None:
        return 0  # cancelled
    chosen_name = selected.split("\t", 1)[0].strip()
    chosen_path = os.path.join(inbox_dir, chosen_name)
    fm, body = pc.read_entry(chosen_path)
    title = fm.get("title") or chosen_name

    choice = _fzf_select(["promote", "redirect", "discard", "cancel"],
                          f"{chosen_name}> ", f"{title} -- promote/redirect/discard/cancel")
    if choice is None or choice == "cancel":
        return 0

    if choice == "promote":
        target_type = _fzf_select(pc.CORE_TYPES, "type> ", "promote to which Diataxis type?")
        if target_type is None:
            return 0
        return _inbox_promote(root, chosen_path, fm, body, target_type)
    elif choice == "redirect":
        return _inbox_redirect(root, chosen_path, fm, body)
    elif choice == "discard":
        return _inbox_discard(root, chosen_path)
    return 0


def cmd_triage(args):
    return proc.run_script("triage_report", ["--json"] if args.json else [])


def cmd_sync(args):
    sync_sources = sources.discover_sync_sources()
    # sources whose SOURCE_META sets uses_sops read credentials from the environment;
    # if this data repo keeps them sops-encrypted, decrypt straight into that one
    # subprocess's env via `sops exec-env` -- no wrapping needed at the call site,
    # and the values never pass through kb's own environment or output.
    targets = list(sync_sources.keys()) if args.source in (None, "all") else [args.source]
    root = pc.get_repo_root()
    secrets_path = os.path.join(root, ".pkb", "secrets.enc.env")
    use_sops = shutil.which("sops") and os.path.exists(secrets_path)

    rc = 0
    for t in targets:
        print(f"--- sync: {t} ---")
        fname, meta = sync_sources[t]
        cmd = proc.module_cmd(fname[:-len(".py")])
        if use_sops and meta.get("uses_sops"):
            cmd_str = shlex.join(cmd)
            target_rc = proc.run_captured(["sops", "exec-env", secrets_path, cmd_str], env=health.sops_key_env())
        else:
            target_rc = proc.run_captured(cmd)
        rc = target_rc or rc
    pc.git_autocommit(root, f"sync: {', '.join(targets)}")
    # sync writes new/updated files straight to disk -- without this the FTS
    # index goes stale immediately and `kb doctor`/`kb search` won't see them
    # until someone remembers to run `kb index` by hand.
    index_rc = proc.run_script("index_fts")
    return index_rc or rc


def cmd_index(args):
    return proc.run_script("index_fts", ["--full"] if args.full else [])


def cmd_validate(args):
    return proc.run_script("validate_frontmatter")


def _entry_fzf_lines(entries):
    return [f"{e['id']}\t{e['type']}\t{e['title']}\t{e['path']}" for e in entries]


def _pick_entry(root, prompt, header, exclude_ids=()):
    """fzf picker over every entry in the repo, returning the chosen id (or None
    if cancelled/unavailable) -- used when `kb tag`/`kb link` are invoked with no
    id, so the id doesn't have to be hunted down by hand first."""
    entries = [e for e in _iter_all_entries(root) if e["id"] not in exclude_ids]
    if not entries:
        return None
    selected = _fzf_select(_entry_fzf_lines(entries), prompt, header, delimiter="\t",
                            with_nth="1,2,3", preview_cmd="cat {4}")
    return selected.split("\t")[0] if selected else None


def _pick_entries_multi(root, prompt, header, candidate_ids):
    """fzf multi-select picker restricted to `candidate_ids`, returning the
    chosen ids or None. Used for `kb link add` (candidates = every id not
    already linked, minus self)."""
    entries = [e for e in _iter_all_entries(root) if e["id"] in candidate_ids]
    if not entries:
        return None
    picked = _fzf_select(_entry_fzf_lines(entries), prompt, header, delimiter="\t",
                          with_nth="1,2,3", preview_cmd="cat {4}", multi=True)
    return [line.split("\t")[0] for line in picked] if picked else None


def _pick_tags_to_add(root, prompt, header):
    """fzf picker for `kb tag add` with no tags given: tab to multi-select from
    every tag already used in the repo, or type one or more new comma-separated
    tags and press enter with nothing selected to create them. Returns a list
    of tags, or None if cancelled."""
    cmd = ["fzf", "--multi", "--print-query", "--prompt", prompt, "--header", header]
    result = subprocess.run(cmd, input="\n".join(_all_tags(root)), text=True, stdout=subprocess.PIPE)
    if result.returncode == 130:
        return None  # esc / ctrl-c
    lines = result.stdout.split("\n")
    query, selected = lines[0], [l for l in lines[1:] if l.strip()]
    if selected:
        return selected
    if query.strip():
        return [t.strip() for t in query.split(",") if t.strip()]
    return None


def cmd_tag(args):
    """Add/remove tags on an entry -- the only sanctioned way to change `tags`,
    since hand-editing frontmatter is off limits. With no action, just list the
    entry's current tags.

    Anything left unspecified on the command line -- the id, or the tags to
    add/remove -- is filled in with an fzf picker when one is available
    (installed + both ends real terminals), same rule as every other
    interactive picker in this file."""
    root = pc.get_repo_root()
    interactive = shutil.which("fzf") and sys.stdout.isatty() and sys.stdin.isatty()

    entry_id = args.id
    if entry_id is None:
        if not interactive:
            return _fail("`kb tag` needs an entry id (install fzf and run from a "
                         "terminal for a picker)", args.json)
        entry_id = _pick_entry(root, "tag> ", "select an entry to tag")
        if entry_id is None:
            return 0  # cancelled, or nothing to pick from

    found = _find_entry(root, entry_id)
    if found is None:
        return _fail(f"no entry with id '{entry_id}'", args.json)
    path, fm, body = found
    tags = list(fm.get("tags") or [])

    if args.action is None:
        _emit(args.json, {"id": entry_id, "tags": tags}, ", ".join(tags) if tags else "(no tags)")
        return 0

    values = args.values
    if not values:
        if not interactive:
            return _fail(f"`kb tag {entry_id} {args.action}` needs at least one tag", args.json)
        if args.action == "add":
            values = _pick_tags_to_add(
                root, "add tag> ",
                "tab: multi-select existing tags -- or type new ones (comma-separated) and enter")
        else:
            if not tags:
                print(f"no tags to remove on {entry_id}")
                return 0
            values = _fzf_select(tags, "rm tag> ", "tab: multi-select tags to remove", multi=True)
        if not values:
            return 0  # cancelled

    result = entry_set_tags(root, entry_id, add=values if args.action == "add" else None,
                             rm=values if args.action == "rm" else None)
    if isinstance(result, dict):  # {"error": "...", "readonly": True, ...}
        return _fail(result["error"], args.json)
    changed = result != (fm.get("tags") or [])
    prefix = "" if changed else "no change -- "
    _emit(args.json, {"id": entry_id, "tags": result, "changed": changed},
          f"{prefix}tags for {entry_id}: {', '.join(result) if result else '(none)'}")
    return 0


def cmd_rm(args):
    """Delete an entry by id -- the sanctioned way to remove a file created
    under the Diataxis structure, since deleting it out from under kb (with
    `rm` directly) would leave the FTS index and any backlinks stale."""
    root = pc.get_repo_root()
    result = entry_delete(root, args.id, force=args.force)
    if "error" in result:
        return _fail(result["error"], args.json)
    suffix = f" ({result['backlinks_cleared']} backlink(s) cleaned up)" if result["backlinks_cleared"] else ""
    _emit(args.json, {"id": args.id, **result}, f"deleted {result['path']}{suffix}")
    return 0


def cmd_mv(args):
    """Move an entry to a different Diataxis type and/or rename its title --
    the sanctioned way to recategorize or retitle a file created under the
    Diataxis structure, since hand-moving it would desync `type` from the
    directory it lives in."""
    root = pc.get_repo_root()
    result = entry_move(root, args.id, new_type=args.type, new_title=args.title)
    if "error" in result:
        return _fail(result["error"], args.json)
    _emit(args.json, {"id": args.id, **result}, f"-> {result['path']}")
    return 0


def cmd_link(args):
    """Add/remove forward links on an entry -- the only sanctioned way to change
    `links`, since hand-editing frontmatter is off limits. With no action, show
    forward links + backlinks (same as kb links).

    Anything left unspecified on the command line -- the id, or the target
    ids to add/remove -- is filled in with an fzf picker when one is
    available, same rule as kb tag."""
    root = pc.get_repo_root()
    interactive = shutil.which("fzf") and sys.stdout.isatty() and sys.stdin.isatty()

    entry_id = args.id
    if entry_id is None:
        if not interactive:
            return _fail("`kb link` needs an entry id (install fzf and run from a "
                         "terminal for a picker)", args.json)
        entry_id = _pick_entry(root, "link> ", "select an entry to link")
        if entry_id is None:
            return 0

    if args.action is None:
        return show_links(root, entry_id, json_out=args.json)

    found = _find_entry(root, entry_id)
    if found is None:
        return _fail(f"no entry with id '{entry_id}'", args.json)
    path, fm, body = found
    links = list(fm.get("links") or [])

    values = args.values
    if not values:
        if not interactive:
            return _fail(f"`kb link {entry_id} {args.action}` needs at least one target id", args.json)
        if args.action == "add":
            candidate_ids = {e["id"] for e in _iter_all_entries(root)} - {entry_id} - set(links)
            values = _pick_entries_multi(root, "add link> ",
                                          "tab: multi-select entries to link to", candidate_ids)
        else:
            if not links:
                print(f"no links to remove on {entry_id}")
                return 0
            values = _fzf_select(links, "rm link> ", "tab: multi-select links to remove", multi=True)
        if not values:
            return 0

    result = entry_set_links(root, entry_id, add=values if args.action == "add" else None,
                              rm=values if args.action == "rm" else None)
    if isinstance(result, dict):  # {"error": "unknown id(s) ..."}
        return _fail(result["error"], args.json)
    changed = result != links
    prefix = "" if changed else "no change -- "
    _emit(args.json, {"id": entry_id, "links": result, "changed": changed},
          f"{prefix}links for {entry_id}: {', '.join(result) if result else '(none)'}")
    return 0


def show_links(root, target_id, json_out=False):
    """Print forward links + backlinks for an entry. Shared by cmd_links and the
    interactive search picker's 'links' action."""
    forward = None
    backlinks = []
    target_path = None

    for path in pc.iter_markdown_files(root):
        try:
            fm, _ = pc.read_entry(path)
        except ValueError:
            continue
        if fm.get("id") == target_id:
            forward = fm.get("links") or []
            target_path = os.path.relpath(path, root)
        if target_id in (fm.get("links") or []):
            backlinks.append((fm.get("id"), fm.get("title"), os.path.relpath(path, root)))

    if forward is None:
        return _fail(f"no entry with id '{target_id}'", json_out)

    if json_out:
        print(json.dumps({
            "id": target_id,
            "path": target_path,
            "forward": forward,
            "backlinks": [{"id": lid, "title": title, "path": path} for lid, title, path in backlinks],
        }))
        return 0

    print(f"{target_id}  ({target_path})")
    print(f"\nforward links ({len(forward)}):")
    for link_id in forward:
        print(f"  -> {link_id}")
    print(f"\nbacklinks ({len(backlinks)}):")
    for link_id, title, path in backlinks:
        print(f"  <- {link_id}  {title}  ({path})")
    return 0


def cmd_links(args):
    root = pc.get_repo_root()
    return show_links(root, args.id, json_out=args.json)


def cmd_show(args):
    """Print an entry's full content by id -- the id-addressed counterpart to
    kb search/kb links, for when you already know the id and just want the
    content without looking up its path first."""
    root = pc.get_repo_root()
    found = _find_entry(root, args.id)
    if found is None:
        print(f"error: no entry with id '{args.id}'", file=sys.stderr)
        return 1
    path, fm, body = found

    if args.json:
        import json
        print(json.dumps({"id": args.id, "path": os.path.relpath(path, root),
                           "frontmatter": fm, "body": body}))
        return 0

    with open(path, "r", encoding="utf-8") as f:
        sys.stdout.write(f.read())
    return 0


def cmd_todo(args):
    if not shutil.which("bd"):
        print("bd CLI not found -- install it (`brew install beads`) to track TODOs. "
              "See `kb doctor`.", file=sys.stderr)
        return 1

    root = pc.get_repo_root()

    if args.words:
        return _todo_word_command(root, args.words, args.json)

    if args.add is not None:
        return _todo_add(root, args)

    try:
        issues = todos.bd_list(root, include_all=args.all)
    except RuntimeError as e:
        print(f"error: bd list failed: {e}", file=sys.stderr)
        return 1
    if not args.all:
        # snoozed todos are out of sight until the snooze ends (--all shows them)
        issues = [i for i in issues if not todos.todo_is_snoozed(i)]

    if args.json:
        import json
        print(json.dumps(issues))
        return 0

    if not issues:
        print("no open todos" if not args.all else "no todos")
        return 0

    # interactive by default when the tooling supports it: fzf installed, and both
    # ends are real terminals (not piped/redirected -- a script consuming `kb todo`
    # output must keep getting the plain list). --plain forces the old behavior.
    interactive = not args.plain and shutil.which("fzf") and sys.stdout.isatty() and sys.stdin.isatty()
    if interactive:
        return _todo_interactive(root, issues)

    for i in issues:
        status = f" [{i['status']}]" if args.all and i["status"] not in ("open", "deferred") else ""
        print(f"{i['id']:<10} P{i['priority']}  {i['title']}{_due_suffix(i)}{status}")

    example = issues[0]["id"]
    hint = (f"\nact on one: kb bd show {example}  |  kb bd close {example}  |  kb bd comment {example} \"...\""
            f"  |  kb todo snooze {example} 1h")
    if not shutil.which("fzf"):
        hint += "  (install fzf for an interactive picker)"
    print(hint)
    return 0


def _due_suffix(issue):
    label = todos.format_due(issue)
    return f"  ({label})" if label else ""


def _todo_word_command(root, words, json_out=False):
    """`kb todo snooze ID WHEN` / `due ID WHEN|none` / `unsnooze ID` -- the
    reminder verbs, as positional words so they read like the rest of `kb todo`."""
    verb, rest = words[0], words[1:]
    try:
        if verb == "snooze" and len(rest) == 2:
            todos.bd_snooze(root, rest[0], rest[1])
            _emit(json_out, {"action": "snooze", "id": rest[0], "until": rest[1]},
                  f"snoozed {rest[0]} until {rest[1]} -- it will come back and remind you then")
        elif verb == "unsnooze" and len(rest) == 1:
            output = todos.bd_unsnooze(root, rest[0])
            _emit(json_out, {"action": "unsnooze", "id": rest[0]}, output)
        elif verb == "due" and len(rest) == 2:
            clear = rest[1] in ("none", "clear")
            todos.bd_update(root, rest[0], due="" if clear else rest[1])
            _emit(json_out, {"action": "due", "id": rest[0], "due": None if clear else rest[1]},
                  f"{rest[0]}: due date cleared" if clear else f"{rest[0]}: due {rest[1]}")
        else:
            return _fail("usage: kb todo snooze ID WHEN | kb todo unsnooze ID | kb todo due ID WHEN|none\n"
                         "  WHEN: 30m, 2h, 3d, 1w, tomorrow, friday, 5pm, 2026-11-02", json_out, code=2)
    except RuntimeError as e:
        return _fail(f"{verb} failed: {e}", json_out)
    return 0


def _todo_add(root, args):
    """Create a new TODO. With a title, this is a one-shot `bd create` -- fast to
    fire off, but still takes --description/--priority/--type/--labels so it can
    carry as much ticket-shaped detail as you want in one line. With no title
    (`kb todo -a`), hand off to bd's own interactive form instead of reinventing
    it -- that already prompts for every field a ticket needs."""
    if not args.add:
        if args.json:
            return _fail("`kb todo -a --json` needs a TITLE -- bd's interactive form can't be machine-driven",
                         True)
        os.execvp("bd", ["bd", "-C", root, "create-form"])  # replaces this process

    try:
        output = todos.bd_create(root, args.add, description=args.description,
                            priority=args.priority, issue_type=args.type, labels=args.labels,
                            due=args.due, as_json=args.json)
    except RuntimeError as e:
        return _fail(f"bd create failed: {e}", args.json)
    print(output)
    return 0


def _todo_interactive(root, issues):
    """fzf-based picker: select an item (with a live `bd show` preview), then an
    action. Falls back to nothing fancy for the action step -- another small fzf
    menu, since that's the same tool the user just used to pick the item."""
    lines, by_id = [], {}
    for i in issues:
        status = f" [{i['status']}]" if i["status"] not in ("open", "deferred") else ""
        lines.append(f"{i['id']}\tP{i['priority']}\t{i['title']}{_due_suffix(i)}{status}")
        by_id[i["id"]] = i

    preview_cmd = f"bd -C {shlex.quote(root)} show {{1}}"
    selected = _fzf_select(lines, "todo> ", "enter: choose an action for this item",
                            delimiter="\t", with_nth="1,2,3", preview_cmd=preview_cmd)
    if selected is None:
        return 0  # cancelled (esc / ctrl-c)
    chosen_id = selected.split("\t", 1)[0].strip()
    chosen = by_id.get(chosen_id)

    is_closed = chosen and chosen["status"] == "closed"
    actions = ["show", "reopen", "comment", "delete", "cancel"] if is_closed else \
        ["show", "close", "snooze", "set due", "comment", "delete", "cancel"]
    choice = _fzf_select(actions, f"{chosen_id}> ",
                          f"{chosen['title'] if chosen else chosen_id} -- choose an action")
    if choice is None:
        return 0

    if choice == "show":
        subprocess.run(["bd", "-C", root, "show", chosen_id])
    elif choice == "close":
        subprocess.run(["bd", "-C", root, "close", chosen_id])
    elif choice == "reopen":
        subprocess.run(["bd", "-C", root, "reopen", chosen_id])
    elif choice in ("snooze", "set due"):
        when = input(f"{choice} {chosen_id} until (30m, 2h, tomorrow, friday, 5pm, ...): ").strip()
        if when:
            _todo_word_command(root, ["snooze" if choice == "snooze" else "due", chosen_id, when])
    elif choice == "comment":
        text = input(f"comment on {chosen_id}: ").strip()
        if text:
            subprocess.run(["bd", "-C", root, "comment", chosen_id, text])
    elif choice == "delete":
        confirm = input(f"type '{chosen_id}' to confirm delete: ").strip()
        if confirm == chosen_id:
            subprocess.run(["bd", "-C", root, "delete", chosen_id, "--force"])
        else:
            print("confirmation didn't match -- delete aborted", file=sys.stderr)
    return 0


def _print_result(ok, msg):
    """Print a success on stdout or a problem on stderr, flushing stdout first so a
    problem never appears ahead of output that was printed before it (stdout is
    block-buffered when piped, stderr isn't)."""
    sys.stdout.flush()
    print(msg, file=sys.stdout if ok else sys.stderr)
    sys.stderr.flush()


def cmd_remind(args):
    """`kb remind` -- list what's due; `check` notifies about it (run on a timer);
    `install`/`uninstall`/`status` manage that timer."""
    if args.ask and not (args.action == "push" and args.rest[:1] == ["deploy"]):
        print("error: --ask only applies to `kb remind push deploy`", file=sys.stderr)
        return 2
    kind = service.service_kind()
    if args.action == "test":
        ok, msg = notifier.send_test_notification()
        _print_result(ok, msg)
        if remind_push.get_config():
            push_ok, push_msg = remind_push.send_test()
            _print_result(push_ok, push_msg)
            ok = ok and push_ok
        return 0 if ok else 1
    if args.action == "push":
        return cmd_remind_push(args)
    if args.action in ("install", "uninstall", "status"):
        if kind is None:
            print(f"error: `kb remind {args.action}` isn't supported on {sys.platform} -- only macOS "
                  f"(launchd) and Linux (systemd --user). Run `kb remind check` from your own "
                  f"scheduler instead.", file=sys.stderr)
            return 1
        if args.action == "install":
            root = service._resolve_agent_repo(args)
            if root is None:
                return 1
            rc, ok, msg = reminders.reminders_install(root, args.interval_minutes)
            if rc == 0:
                _print_result(ok, msg)
            return rc
        if args.action == "uninstall":
            rc = service._interval_service_uninstall(kind, "remind")
            app = notifier._notifier_app_path()
            if sys.platform == "darwin" and os.path.isdir(app):
                shutil.rmtree(app)
                print(f"removed {app}")
            return rc
        rc = service._interval_service_status(kind, "remind", "kb remind install")
        if remind_push.get_config():
            _print_result(*remind_push.status())
        return rc

    issues = _remind_issues()
    if issues is None:
        return 1

    if args.action == "list":
        due = todos.todos_due(issues)
        if not due:
            print("nothing due")
            return 0
        for i in due:
            print(f"{i['id']:<10} P{i['priority']}  {i['title']}{_due_suffix(i)}")
        print(f"\nsnooze: kb todo snooze {due[0]['id']} 1h  |  done: kb bd close {due[0]['id']}")
        return 0

    # check
    pending = todos.reminders_to_send(issues, repeat_hours=args.repeat_hours)
    state = todos._reminder_load()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    shown, rest = pending[:5], len(pending) - 5
    for i in shown:
        label = todos.format_due(i)
        print(f"{i['id']}  {i['title']}  ({label})")
        notifier.notify(f"TODO {label}", f"{i['title']}  --  kb todo snooze {i['id']} 1h")
        state[i["id"]] = now
    if rest > 0:
        # past the cap: one summary instead of a wall of notifications
        notifier.notify("kb: more TODOs due", f"and {rest} more -- run `kb remind` to see them")
        for i in pending[5:]:
            state[i["id"]] = now
    if pending:
        todos._reminder_save(state)
    if remind_push.get_config():
        # mirror every due time to the Worker each run, so the phone push stays right even
        # when nothing was announced here; a failure is logged, not fatal to the desktop path
        ok, msg = _phone_exchange(issues)
        if not ok:
            print(f"warning: {msg}", file=sys.stderr)
    return 0


def _remind_issues():
    """Open bd issues for the reminder commands, or None after printing why not."""
    if not shutil.which("bd"):
        print("bd CLI not found -- install it (`brew install beads`) to track TODOs. "
              "See `kb doctor`.", file=sys.stderr)
        return None
    try:
        return todos.bd_list(pc.get_repo_root())
    except RuntimeError as e:
        print(f"error: bd list failed: {e}", file=sys.stderr)
        return None


_PHONE_TODO_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _apply_phone_action(root, action):
    """Do one close/snooze the phone asked for. The Worker is outside this machine's trust,
    so the id must look like a bd id (and can't start with `-`, which bd would read as a
    flag) and only the two known actions run. Raises RuntimeError with what went wrong."""
    todo_id, verb = action.get("todo_id"), action.get("action")
    if not isinstance(todo_id, str) or not _PHONE_TODO_ID.fullmatch(todo_id):
        raise RuntimeError(f"ignored a request for an odd TODO id {str(todo_id)[:40]!r}")
    if verb == "close":
        todos.bd_action(root, "close", todo_id)
    elif verb == "snooze" and isinstance(action.get("until"), int):
        now = datetime.now().astimezone()
        until = max(datetime.fromtimestamp(action["until"]).astimezone(), now + timedelta(minutes=1))
        todos.bd_snooze(root, todo_id, until.isoformat(timespec="seconds"))
    else:
        raise RuntimeError(f"ignored an unknown request {str(verb)[:20]!r} for {todo_id}")


def _phone_exchange(issues):
    """Apply what the phone asked for (close/snooze), then replace the Worker's reminders with
    the current TODOs. Applying comes first: syncing first would put a TODO the phone just
    closed back on its list. Requests that fail are reported and dropped, and the sync that
    follows shows the phone how things really stand. (ok, message)."""
    actions, err = remind_push.pull_actions()
    if err:
        print(f"warning: couldn't fetch the phone's requests: {err}", file=sys.stderr)
    if actions:
        root = pc.get_repo_root()
        done = []
        for a in actions:
            try:
                _apply_phone_action(root, a)
                done.append(a)
            except RuntimeError as e:
                print(f"warning: phone request for {a.get('todo_id')} failed: {e}", file=sys.stderr)
        remind_push.ack_actions([a["id"] for a in actions if isinstance(a.get("id"), int)])
        if done:
            for a in done:
                print(f"phone: {a['action']} {a['todo_id']}")
            try:
                issues = todos.bd_list(root)
            except RuntimeError as e:
                return False, f"applied the phone's requests but couldn't re-read TODOs: {e}"
    return remind_push.sync(issues)


def _remind_push_devices(rest):
    """`kb remind push devices [rm ID]` -- list the phones that get alerts, or remove one.
    Only this machine (the sync token) can; the phone's own token can't."""
    if rest[:1] == ["rm"]:
        if len(rest) != 2 or not rest[1].isdigit():
            print("usage: kb remind push devices rm <id>", file=sys.stderr)
            return 2
        ok, msg = remind_push.remove_device(int(rest[1]))
        _print_result(ok, msg)
        return 0 if ok else 1
    if rest:
        print("usage: kb remind push devices [rm <id>]", file=sys.stderr)
        return 2
    found, err = remind_push.devices()
    if err:
        _print_result(False, err)
        return 1
    if not found:
        print("no phones are subscribed")
    for d in found:
        added = datetime.fromtimestamp(d.get("created_at") or 0).astimezone().strftime("%Y-%m-%d %H:%M")
        print(f"{d['id']:>3}  {d.get('label') or '(unnamed)':<28} {d.get('service', ''):<26} added {added}")
    if found:
        print("remove one with: kb remind push devices rm <id>")
    return 0


def cmd_remind_push(args):
    """`kb remind push init [dir] | deploy | setup <url> | sync | status | devices | off` -- phone
    reminders through the kb-push Cloudflare Worker (templates/worker,
    docs/how-to/get-phone-reminders.md)."""
    sub = args.rest[0] if args.rest else "status"
    if sub == "deploy":
        return worker_deploy.deploy(ask=args.ask)
    if sub == "init":
        dest = remind_push.init_worker(args.rest[1] if len(args.rest) > 1 else None)
        print(f"copied the Worker to {dest}\nto put it live: `kb remind push deploy`, or by hand from "
              f"https://github.com/dantuck/pkb-cli/blob/main/docs/how-to/get-phone-reminders.md")
        return 0
    if sub == "status":
        ok, msg = remind_push.status()
        _print_result(ok, msg)
        return 0 if ok else 1
    if sub == "off":
        remind_push.disable()
        print("phone reminders are off for kb (the Worker and your phone's subscription are untouched)")
        return 0
    if sub == "sync":
        issues = _remind_issues()
        if issues is None:
            return 1
        ok, msg = _phone_exchange(issues)
        _print_result(ok, msg)
        return 0 if ok else 1
    if sub == "devices":
        return _remind_push_devices(args.rest[1:])
    if sub == "setup":
        url = args.rest[1] if len(args.rest) > 1 else ""
        if not remind_push.valid_url(url):
            print("usage: kb remind push setup https://<your-worker>.workers.dev", file=sys.stderr)
            return 1
        token = os.environ.get("KB_PUSH_TOKEN") or (
            getpass.getpass("sync token (the Worker's SYNC_TOKEN): ") if sys.stdin.isatty()
            else sys.stdin.readline()).strip()
        if not token:
            print("error: no sync token given", file=sys.stderr)
            return 1
        ok, msg, payload = remind_push.verify(url, token)
        if not ok:
            print(f"error: {msg}", file=sys.stderr)
            return 1
        remind_push.save_config(url, token)
        print(f"saved -- `kb remind check` now mirrors due times to {url}")
        if not payload.get("subscriptions"):
            print(f"next: open {url} on your phone and tap Enable notifications")
        return 0
    print(f"error: unknown push action '{sub}' -- use init, deploy, setup, sync, status, devices or off", file=sys.stderr)
    return 1


def cmd_bd(args):
    """Passthrough to `bd`, pointed at the resolved data repo root via -C -- so
    every other bd action (show/close/comment/create/update/...) works from
    anywhere the same way `kb todo` does, without hand-wrapping each one."""
    if not shutil.which("bd"):
        print("bd CLI not found -- install it (`brew install beads`).", file=sys.stderr)
        return 1
    root = pc.get_repo_root()
    result = subprocess.run(["bd", "-C", root, *args.bd_args])
    return result.returncode


def cmd_secrets(args):
    """Open the resolved data repo's encrypted secrets file in sops, creating it
    (per .sops.yaml) on first run. Hands off to sops entirely via exec -- it needs
    real interactive control to spawn $EDITOR, same reasoning as `kb journal`'s
    EDITOR handoff. The real secret values never pass through kb or this process:
    sops decrypts straight into the editor and re-encrypts straight from it."""
    if not shutil.which("sops"):
        print("sops CLI not found -- install it to manage encrypted credentials "
              "(e.g. `brew install sops`), or export PKB_MEMOS_URL/PKB_MEMOS_TOKEN/"
              "PKB_GITLAB_PROJECT directly instead.", file=sys.stderr)
        return 1
    root = pc.get_repo_root()
    sops_config = os.path.join(root, ".sops.yaml")
    if not os.path.exists(sops_config):
        print(f"error: no .sops.yaml in {root} -- this data repo isn't set up for sops-encrypted "
              f"secrets yet. See pkb-cli's templates/secrets.env.example and README.", file=sys.stderr)
        return 1
    secrets_path = os.path.join(root, ".pkb", "secrets.enc.env")
    # sops discovers .sops.yaml relative to cwd, not the target file's path -- pass
    # it explicitly so this works regardless of where kb secrets was actually run
    # from (the whole point of resolving `root` in the first place). Re-editing an
    # existing file also needs the *private* key (sops_key_env), not just the
    # public recipient in .sops.yaml -- only first-time creation gets away without it.
    env = health.sops_key_env()
    if "EDITOR" not in env:
        editor = pc.get_editor()
        if editor:
            env["EDITOR"] = editor
    os.execvpe("sops", ["sops", "--config", sops_config, secrets_path], env)


def cmd_config(args):
    if args.value is None:
        editor, source = pc.resolve_editor()
        if source == "env":
            print(f"editor: {editor}  (from $EDITOR)")
        else:
            print(f"editor: {editor}  (saved in {pc.kb_config_path()})" if editor else "editor: not set")
        return 0

    if not args.value.strip() or not pc.set_editor(args.value):
        print(f"error: '{args.value}' doesn't look like a usable editor command "
              f"(empty, or can't be parsed -- check quoting)", file=sys.stderr)
        return 1

    print(f"saved -- kb will use '{args.value}' whenever $EDITOR isn't set")
    pc.warn_if_editor_missing(args.value)
    return 0


_confirm = pc.confirm


def cmd_push(args):
    root = pc.get_repo_root()
    status = health.push_status(root)
    if not status["is_git"]:
        print("error: not a git repo", file=sys.stderr)
        return 1
    if not status["upstream"]:
        print("error: no upstream configured -- `git push -u <remote> <branch>` once to set one",
              file=sys.stderr)
        return 1
    if status["behind"]:
        print(f"note: {status['behind']} commit(s) behind {status['upstream']} as of the last fetch -- "
              f"push may be rejected; `git pull` first if so")
    if not status["ahead"]:
        print(f"up to date with {status['upstream']}")
        return 0
    print(f"pushing {status['ahead']} commit(s) to {status['upstream']}...")
    ok, message = pc.git_push(root)
    if not ok:
        print(f"error: {message}", file=sys.stderr)
        return 1
    print(f"pushed -> {message}")
    return 0


def cmd_setup(args):
    # root is optional: --install (and the tool/sync checks below) don't need a data
    # repo at all -- only the hook/index/bd/secrets steps do.
    ok, todo = [], []
    try:
        root = pc.get_repo_root()
    except SystemExit:
        # No data repo found anywhere (walking up from cwd) and no central kb at
        # ~/.pkb either -- this is a brand-new machine. Bootstrap the central kb
        # here instead of requiring a manual `mkdir -p ~/.pkb/.pkb && git init`
        # before setup can do anything, since that step was easy to miss (e.g.
        # when a scripted `kb setup --yes` runs non-interactively).
        central = os.path.join(os.path.expanduser("~"), ".pkb")
        os.makedirs(os.path.join(central, ".pkb"), exist_ok=True)
        if shutil.which("git") and not os.path.isdir(os.path.join(central, ".git")):
            subprocess.run(["git", "init", "-q"], cwd=central, check=True)
        pc.REPO_ROOT = central
        root = central
        ok.append(f"created central kb at {central} (none found)")

    # 0. kb itself is put on PATH by the installer (pipx/uv/pip); just confirm it.
    on_path = shutil.which("kb")
    if on_path:
        ok.append(f"kb is on PATH ({on_path})")
    else:
        todo.append("`kb` isn't on your PATH -- if installed with pipx, run `pipx ensurepath` and open a new shell")

    # 0b. install the bundled Claude Code skill -- guided, default no (not
    # everyone using kb also uses Claude Code). A symlink, not a copy --
    # Claude Code follows a skill-directory symlink and reads SKILL.md from
    # the target, so this repo stays the single source of truth and a `kb
    # update` picks up any skill changes automatically, same reasoning as
    # symlinking the kb binary itself above.
    skill_src = os.path.join(PKG_DIR, "skills", "kb")
    skill_dir = os.path.expanduser("~/.claude/skills")
    skill_target = os.path.join(skill_dir, "kb")
    if os.path.islink(skill_target) and os.readlink(skill_target) == skill_src:
        ok.append(f"kb skill already installed at {skill_target}")
    elif os.path.islink(skill_target) and os.readlink(skill_target).endswith(os.path.join("skills", "kb")):
        # our own symlink from an older layout/install location -- repoint it
        os.remove(skill_target)
        os.symlink(skill_src, skill_target)
        ok.append(f"repointed kb skill -> {skill_src}")
    elif os.path.lexists(skill_target):
        todo.append(f"{skill_target} already exists and isn't this repo's skill -- remove it yourself "
                     f"if you want kb's version there")
    elif _confirm("Install the Claude Code skill for kb (lets Claude Code drive kb directly)?",
                   default=False, assume_yes=args.yes):
        os.makedirs(skill_dir, exist_ok=True)
        os.symlink(skill_src, skill_target)
        ok.append(f"installed kb skill -> {skill_target}")

    sync_sources = sources.discover_sync_sources()
    tools = health.tool_status(sync_sources)

    # 0c. install missing optional CLI deps via brew -- guided, default yes
    # (this is the "including optional tooling" part of setup: bd/beads,
    # glab/gh for sync sources, rg/fzf for Tier 1 search, sops for secrets).
    to_install = health.missing_deps_with_formulas(tools, sync_sources)
    if not to_install:
        ok.append("all optional CLI deps (bd/glab/gh/rg/fzf/sops) already installed")
    elif not shutil.which("brew"):
        todo.append("brew not found -- install manually: " +
                    ", ".join(f"{t} (`{f}`)" for t, f in to_install))
    else:
        names = ", ".join(t for t, _f in to_install)
        if _confirm(f"Install missing optional CLI deps via brew ({names})?", default=True, assume_yes=args.yes):
            formulas = sorted({f for _t, f in to_install})
            result = subprocess.run(["brew", "install", *formulas], capture_output=True, text=True)
            if result.returncode == 0:
                ok.append(f"installed missing deps via brew: {', '.join(formulas)}")
                # just recheck presence of what we installed -- no need to re-discover
                # sync sources or re-run every auth_check subprocess again
                tools.update({t: shutil.which(t) is not None for t, _f in to_install})
            else:
                todo.append(f"brew install {' '.join(formulas)} failed: "
                            f"{result.stderr.strip() or result.stdout.strip()}")
        else:
            todo.append(f"skipped installing optional deps: {names} -- rerun `kb setup` "
                        f"or `brew install {' '.join(sorted({f for _t, f in to_install}))}` later")

    if root is None:
        todo.append("not inside a pkb data repo (no .pkb/ found) -- cd into one to also set up the "
                     "pre-commit hook, search index, and local bd store")
    else:
        # 1. pre-commit hook -- generated directly (not a symlinked template) since it
        # must hardcode this machine's path to the tool install, which lives in a
        # separate repo from the data repo the hook runs in.
        git_dir = os.path.join(root, ".git")
        if not os.path.isdir(git_dir):
            todo.append("not a git repo (or .git not found here) -- pre-commit hook skipped")
        else:
            hook_content = health.hook_expected_content()
            hook_path, existing = health.read_hook(root)
            if existing == hook_content:
                ok.append("pre-commit hook already installed")
            elif existing is not None and health.HOOK_MARKER not in existing:
                todo.append(f"{os.path.relpath(hook_path, root)} exists and isn't our hook -- leaving it alone")
            else:
                os.makedirs(os.path.dirname(hook_path), exist_ok=True)
                if os.path.lexists(hook_path):  # e.g. a stale/broken symlink from an older kb setup
                    os.remove(hook_path)
                with open(hook_path, "w", encoding="utf-8") as f:
                    f.write(hook_content)
                os.chmod(hook_path, 0o755)
                ok.append("installed pre-commit hook" if existing is None else "refreshed pre-commit hook path")

            # 1b. auto-commit excludes (fts.db, entry locks) -- every kb mutation
            # commits automatically, so these must be in place before that ever
            # runs, not just lazily on first write
            pc._ensure_git_excludes(root)
            ok.append("auto-commit excludes (fts.db, entry locks) are in place")

        # 2. FTS index
        db_path = health.index_status(root)
        full = not os.path.exists(db_path)
        rc = proc.run_script("index_fts", ["--full"] if full else [])
        ok.append(f"{'built' if full else 'refreshed'} the FTS5 search index" if rc == 0 else "index build failed")
        sem = health.semantic_status(root)
        if sem:
            (ok if sem[0] else todo).append(sem[1])
        elif semantic.missing_dependency() and semantic.config(root)[0]:
            ok.append("semantic search not installed (optional) -- `uv tool install 'pkb-cli[semantic]'` "
                      "adds meaning-based search")

        # 3. local bd store
        if tools["bd"]:
            store_exists, _git_excluded = health.bd_store_status(root)
            if store_exists:
                ok.append("local bd (beads) store already initialized")
            else:
                result = subprocess.run(
                    ["bd", "init", "--stealth", "--non-interactive"],
                    cwd=root, capture_output=True, text=True,
                )
                if result.returncode == 0:
                    ok.append("initialized a local bd store (--stealth: excluded from git)")
                else:
                    todo.append(f"bd init failed: {result.stderr.strip() or result.stdout.strip()}")
            # 3b. TODO reminders -- opt-in, default no (a background job on a timer is
            # not something to turn on unasked, including under --yes)
            kind = service.service_kind()
            reminders_on, reminder_msg = reminders.reminder_status_line(kind)
            if reminders_on:
                ok.append(reminder_msg)
            elif kind is not None and not service._service_state(kind, "remind")["installed"]:
                if _confirm("Check for due TODOs every 5 minutes and send desktop notifications "
                            "(kb remind install)?", default=False, assume_yes=args.yes):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        rc = service._interval_service_install(kind, "remind", root, ["remind", "check"], 5,
                                                       "kb remind -- notify about due TODOs")
                    if rc != 0:
                        todo.append(f"kb remind install failed: {out.getvalue().strip()}")
                    else:
                        ok.append("installed the TODO reminder check (every 5 minutes)")
                        notified, note = notifier.finish_reminder_install()
                        (ok if notified else todo).append(note.replace("\n", "\n         "))
                else:
                    todo.append(reminder_msg)
            else:
                todo.append(reminder_msg)
        else:
            todo.append("bd CLI not found -- `brew install beads` if you want local TODO tracking via kb sync beads")

        # 4. secrets (sops), informational only -- never touch/create real secrets ourselves
        sops_configured, secrets_exists = health.secrets_status(root)
        if sops_configured:
            if secrets_exists:
                ok.append(".pkb/secrets.enc.env already set up")
            elif tools["sops"]:
                example_path = os.path.join(PKG_DIR, "templates", "secrets.env.example")
                todo.append("run `kb secrets` to create encrypted memos/gitlab/github credentials "
                            f"(see {os.path.normpath(example_path)} for the expected keys)")
            else:
                todo.append("sops CLI not found -- install it to use encrypted credentials, or export "
                            "PKB_MEMOS_URL/PKB_MEMOS_TOKEN/PKB_GITLAB_PROJECT/PKB_GITHUB_REPO directly")

    # 5. cli_tool sync sources (e.g. glab/gh, required for GitLab/GitHub issue sync)
    todo.extend(sources.sync_tool_todos(sync_sources, tools))

    # 6. rg/fzf (Tier 1 search, optional -- kb search/index work without them)
    missing_search_tools = [t for t in ("rg", "fzf") if not tools[t]]
    if missing_search_tools:
        todo.append(f"{'/'.join(missing_search_tools)} not found -- optional, only needed for zero-setup "
                     f"search (`brew install ripgrep fzf`); kb search/index don't need them")

    # 7. what's actually configured for sync right now (informational)
    configured = sources.sync_configured_sources(sync_sources, tools)
    if configured:
        ok.append(f"sync sources ready in this shell: {', '.join(configured)}")
    else:
        todo.append("no sync source has credentials in this shell yet (memos/gitlab/github need env vars, "
                     "see README 'Ingestion credentials')")

    print("kb setup\n")
    for line in ok:
        print(f"  [ok]   {line}")
    for line in todo:
        print(f"  [todo] {line}")
    return 1 if any("failed" in t for t in todo) else 0


def cmd_web(args):
    root = pc.get_repo_root()
    from . import kb_web
    return kb_web.serve(root, args.port, open_browser=not args.no_open)


def cmd_doctor(args):
    """Read-only health check -- reuses the same detection helpers as `kb setup`
    but never writes anything, and adds a few deeper checks setup doesn't do."""
    try:
        root = pc.get_repo_root()
    except SystemExit:
        root = None
    ok, todo = health.doctor_checks(root)

    p_ok, p_todo = projdocs.doctor_lines()
    ok, todo = ok + p_ok, todo + p_todo

    print("kb doctor\n")
    for line in ok:
        print(f"  [ok]   {line}")
    for line in todo:
        print(f"  [todo] {line}")
    if not todo:
        print("\nall clear")
    return 1 if todo else 0


EPILOG = """\
examples:
  kb new how-to "Tailscale sidecar config for Synology"
  kb search tailscale --type how-to
  kb sync all && kb triage
  kb setup --yes
  kb update

kb operates on a data repo found by walking up from the current directory to a
.pkb/ directory. Run 'kb <command> -h' for details on a specific command.
"""


def build_parser():
    """The full `kb` argument parser (split from main() so tests can inspect it)."""
    parser = KBArgumentParser(
        prog="kb",
        description="A CLI for a Diataxis-based personal knowledge base: search, "
                     "create, triage, and sync your notes.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"kb {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, title="commands", metavar="<command>")

    p = sub.add_parser(
        "search", help="search indexed content",
        description="Search indexed content. By default keyword (FTS5) and semantic matches are "
                    "combined when the optional `semantic` extra is installed; otherwise keyword only.",
        epilog=(
            "if fzf is installed and you're in a real terminal, this opens an "
            "interactive picker by default: select a result (with a live file "
            "preview), then an action (view/edit/links). --plain always skips it."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("query", help="search terms (bare words match as prefixes; FTS5 match syntax also works)")
    p.add_argument("--type", choices=pc.CORE_TYPES, help="restrict to one Diataxis type")
    p.add_argument("--tag", help="restrict to entries with this tag")
    p.add_argument("--all", action="store_true", help="include journal/inbox/sources (default: core content only)")
    p.add_argument("--plain", action="store_true",
                    help="force the plain list even if fzf + a terminal make interactive mode available")
    p.add_argument("--json", action="store_true", help="machine-readable output; implies --plain")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--keyword", action="store_true",
                      help="FTS5 keyword match only (exact words/prefixes, FTS5 syntax)")
    mode.add_argument("--semantic", action="store_true",
                      help="rank by meaning only (needs the `semantic` extra); errors if unavailable")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser(
        "new", help="scaffold a new core entry",
        description="Scaffold a new core entry with valid frontmatter.",
        epilog=(
            "  kb new how-to \"Tailscale sidecar config\" --tags networking,tailscale "
            "--links 2026-08-20-0900 --body \"first draft notes...\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("type", choices=pc.CORE_TYPES, help="Diataxis type")
    p.add_argument("title", help="entry title")
    p.add_argument("--tags", metavar="TAG,TAG", help="comma-separated initial tags")
    p.add_argument("--links", metavar="ID,ID", help="comma-separated ids of related entries (must already exist)")
    p.add_argument("--body", metavar="TEXT", help="initial body text, after the title heading")
    p.add_argument("--json", action="store_true", help="machine-readable output: {id, path, type, title}")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser(
        "journal", help="open or create a journal entry",
        description="Open or create a journal entry.",
        epilog=(
            "with no DATE (the default), and if fzf is installed and you're in a "
            "real terminal, this opens an interactive picker over past entries "
            "(today included, created on selection if it doesn't exist yet), with "
            "on-this-day entries (1 week/month/year+ ago) pinned to the top. "
            "--plain always skips it and opens/creates today's entry directly, "
            "printing on-this-day and nearby entries for context.\n\n"
            "-m/--add TEXT appends a timestamped line instead of opening the entry "
            "-- no frontmatter to deal with, works whether it exists yet or not:\n"
            "  kb journal -m \"fixed the flaky test\"\n"
            "  kb journal 2026-08-30 -m \"forgot to log this\"\n\n"
            "-m/--add with no TEXT opens a blank scratch buffer in $EDITOR instead "
            "-- for multiline input -- and appends whatever you save as one "
            "timestamped block, again without touching the entry's frontmatter:\n"
            "  kb journal -m\n\n"
            "--tag TAG lists past entries carrying TAG, newest first, for "
            "browsing one theme across time:\n"
            "  kb journal --tag homelab\n\n"
            "`rollup` generates/refreshes a browsable month-at-a-glance page at "
            "reference/journal-summaries/YYYY-MM.md (every day's entry concatenated, "
            "plus a tag-frequency line) -- defaults to last month if MONTH is omitted:\n"
            "  kb journal rollup\n"
            "  kb journal rollup 2026-07"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("date", nargs="?", default=None, metavar="DATE",
                    help="YYYY-MM-DD, 'today' (default), or 'rollup' to generate a monthly summary")
    p.add_argument("month", nargs="?", default=None, metavar="MONTH",
                    help="YYYY-MM, only used with `rollup` (default: last month)")
    p.add_argument("-m", "--add", nargs="?", const="", default=None, metavar="TEXT",
                    help="append TEXT as a timestamped line; with no TEXT, open a scratch "
                         "buffer in $EDITOR for multiline input instead")
    p.add_argument("--tag", metavar="TAG", help="list past entries tagged TAG instead of opening one")
    p.add_argument("--plain", action="store_true",
                    help="force opening/creating today's entry directly, skipping the interactive picker")
    p.add_argument("--json", action="store_true",
                    help="machine-readable result for -m/--add and `rollup`")
    p.set_defaults(func=cmd_journal)

    p = sub.add_parser(
        "inbox", help="list inbox contents with age",
        description="List inbox contents with age.",
        epilog=(
            "with no arguments, and if fzf is installed and you're in a real terminal, "
            "this opens an interactive picker by default: select an item (with a live "
            "preview), then promote (to a Diataxis type), redirect (into that day's "
            "journal entry), or discard -- resolving it, not just reporting on it. "
            "--plain always skips it, falling back to a plain list.\n\n"
            "for scripted/non-interactive use (no picker available), resolve one item "
            "directly by id instead:\n"
            "  kb inbox <id> promote how-to     move into core content as that type\n"
            "  kb inbox <id> redirect           append into that day's journal entry\n"
            "  kb inbox <id> discard            delete it outright"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("id", nargs="?", default=None, help="inbox item id -- with an action, resolve it non-interactively")
    p.add_argument("action", nargs="?", choices=["promote", "redirect", "discard"], default=None)
    p.add_argument("type", nargs="?", choices=pc.CORE_TYPES, default=None,
                    help="Diataxis type, required for `promote`")
    p.add_argument("--plain", action="store_true",
                    help="force the plain list even if fzf + a terminal make interactive mode available")
    p.add_argument("--json", action="store_true",
                    help="machine-readable output: the item list, or the result of promote/redirect/discard")
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("triage", help="flag inbox items overdue for triage")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_triage)

    sync_source_names = list(sources.discover_sync_sources().keys())
    p = sub.add_parser("sync", help="pull in " + " / ".join(sync_source_names))
    p.add_argument("source", nargs="?", choices=sync_source_names + ["all"], default="all",
                    help="which source to sync (default: all)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser(
        "push", help="push the data repo's commits to its upstream",
        description="Push whatever `kb`'s auto-commit has accumulated to the data repo's "
                    "configured upstream. Never guesses a remote -- if none is configured, "
                    "set one once with `git push -u <remote> <branch>`. To push automatically "
                    "after every auto-commit instead, set `auto_push: true` in .pkb/config.yml.",
    )
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("index", help="rebuild or refresh the FTS5 search index")
    p.add_argument("--full", action="store_true", help="rebuild from scratch instead of incrementally")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("validate", help="check frontmatter, id, and link integrity")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("links", help="show forward links and backlinks for an entry")
    p.add_argument("id", help="entry id, e.g. 2026-08-31-1423")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_links)

    p = sub.add_parser("show", help="print an entry's full content by id")
    p.add_argument("id", help="entry id, e.g. 2026-08-31-1423")
    p.add_argument("--json", action="store_true",
                    help="machine-readable output ({id, path, frontmatter, body})")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser(
        "tag", help="view, add, or remove tags on an entry",
        description="View, add, or remove tags on an entry -- the sanctioned way to "
                    "change `tags` (never hand-edit frontmatter). Anything left out "
                    "(the id, or which tags to add/remove) opens an fzf picker instead, "
                    "when fzf is installed and you're in a real terminal.",
        epilog=(
            "  kb tag 2026-08-31-1423                  list current tags\n"
            "  kb tag 2026-08-31-1423 add networking homelab\n"
            "  kb tag 2026-08-31-1423 rm homelab\n"
            "  kb tag                                  pick an entry, then list its tags\n"
            "  kb tag 2026-08-31-1423 add              pick tags: multi-select existing ones,\n"
            "                                           or type new ones (comma-separated)\n"
            "  kb tag 2026-08-31-1423 rm                pick which current tags to remove"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("id", nargs="?", default=None,
                    help="entry id, e.g. 2026-08-31-1423 (omit for an fzf picker)")
    p.add_argument("action", nargs="?", choices=["add", "rm"], default=None)
    p.add_argument("values", nargs="*", metavar="TAG",
                    help="tag(s) to add/remove (omit for an fzf picker)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser(
        "link", help="view, add, or remove links (forward references) on an entry",
        description="View, add, or remove links on an entry -- the sanctioned way to "
                    "change `links` (never hand-edit frontmatter). With no action, same "
                    "as `kb links` (forward links + backlinks). Anything left out (the id, "
                    "or which entries to link/unlink) opens an fzf picker instead, when "
                    "fzf is installed and you're in a real terminal.",
        epilog=(
            "  kb link 2026-08-31-1423                        forward links + backlinks\n"
            "  kb link 2026-08-31-1423 add 2026-08-20-0900\n"
            "  kb link 2026-08-31-1423 rm 2026-08-20-0900\n"
            "  kb link                                        pick an entry, show its links\n"
            "  kb link 2026-08-31-1423 add                    multi-select entries to link to\n"
            "  kb link 2026-08-31-1423 rm                     multi-select links to remove"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("id", nargs="?", default=None,
                    help="entry id, e.g. 2026-08-31-1423 (omit for an fzf picker)")
    p.add_argument("action", nargs="?", choices=["add", "rm"], default=None)
    p.add_argument("values", nargs="*", metavar="TARGET_ID",
                    help="id(s) to link/unlink (omit for an fzf picker)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_link)

    p = sub.add_parser(
        "rm", help="delete an entry",
        description="Delete an entry by id -- the sanctioned way to remove a file "
                    "created under the Diataxis structure (deleting it directly would "
                    "leave the search index and any backlinks stale).",
        epilog=(
            "  kb rm 2026-08-31-1423             delete it (refuses if other entries link to it)\n"
            "  kb rm 2026-08-31-1423 --force     delete it, also removing it from any\n"
            "                                     other entry's `links`"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("id", help="entry id, e.g. 2026-08-31-1423")
    p.add_argument("--force", action="store_true",
                    help="delete even if other entries still link to it, cleaning up those links")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_rm)

    p = sub.add_parser(
        "mv", help="move an entry to a different Diataxis type and/or rename it",
        description="Move a core entry (tutorial/how-to/reference/explanation) to a "
                    "different Diataxis type and/or rename its title -- the sanctioned "
                    "way to fix a miscategorized or mistitled file, since moving it "
                    "yourself would desync `type` from the directory it lives in. "
                    "Inbox items use `kb inbox <id> promote` instead.",
        epilog=(
            "  kb mv 2026-08-31-1423 --type how-to             recategorize\n"
            "  kb mv 2026-08-31-1423 --title \"New title\"        rename\n"
            "  kb mv 2026-08-31-1423 --type how-to --title \"New title\""
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("id", help="entry id, e.g. 2026-08-31-1423")
    p.add_argument("--type", choices=pc.CORE_TYPES, help="new Diataxis type")
    p.add_argument("--title", help="new title")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_mv)

    p = sub.add_parser(
        "todo", help="list open bd (beads) TODOs, sorted by priority",
        description="List open bd (beads) TODOs, sorted by priority.",
        epilog=(
            "if fzf is installed and you're in a real terminal, this opens an "
            "interactive picker by default: select an item (with a live preview), "
            "then an action (show/close/comment). --plain always skips it; --json "
            "prints bd's own issue JSON instead (implies --plain).\n\n"
            "otherwise (or with --plain/--json), act on one with `kb bd`, which points any "
            "bd command at the resolved data repo the same way this command does:\n"
            "  kb bd show <id>              full details\n"
            "  kb bd comment <id> \"...\"      log progress\n"
            "  kb bd close <id>              mark done\n"
            "  kb bd update <id> --priority <0-4>\n"
            "see `bd --help` (or `kb bd <command> --help`) for the full bd surface.\n\n"
            "-a/--add TITLE creates a new one -- fast, but still ticket-shaped:\n"
            "  kb todo -a \"fix the flaky test\" -p 1 -t bug -d \"fails ~1/10 runs in CI\"\n"
            "-a/--add with no TITLE opens bd's interactive form instead, for filling "
            "in every field (title, description, type, priority, labels, ...) by hand:\n"
            "  kb todo -a"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--all", action="store_true", help="include closed issues")
    p.add_argument("--plain", action="store_true",
                    help="force the plain list even if fzf + a terminal make interactive mode available")
    p.add_argument("--json", action="store_true",
                    help="machine-readable output (bd's own issue JSON); implies --plain; also the "
                         "result of -a/--add and snooze/unsnooze/due")
    p.add_argument("-a", "--add", nargs="?", const="", default=None, metavar="TITLE",
                    help="create a new TODO with TITLE; with no TITLE, open bd's interactive form")
    p.add_argument("-d", "--description", metavar="TEXT", help="description for -a/--add")
    p.add_argument("-p", "--priority", default="2", metavar="0-4", help="priority for -a/--add (default: 2)")
    p.add_argument("-t", "--type", default="task", metavar="TYPE",
                    help="issue type for -a/--add (bug|feature|task|epic|chore|decision; default: task)")
    p.add_argument("-l", "--labels", metavar="LABELS", help="comma-separated labels for -a/--add")
    p.add_argument("--due", metavar="WHEN",
                    help="due date to be reminded at, for -a/--add (30m, 2h, 3d, tomorrow, friday, 5pm, 2026-11-02)")
    p.add_argument("words", nargs="*", metavar="snooze|unsnooze|due",
                    help="`snooze ID WHEN`, `unsnooze ID`, or `due ID WHEN|none`")
    p.set_defaults(func=cmd_todo)

    p = sub.add_parser(
        "remind", help="list or announce TODOs that are due; schedule the check (launchd/systemd)",
        description="Remind you of TODOs that have come due. With no action, lists them. "
                     "`check` posts a desktop notification for each one not yet announced "
                     "(overdue ones repeat every --repeat-hours until closed or snoozed) -- "
                     "`install` schedules it on a timer.",
        epilog=(
            "  kb todo -a \"review the vendor email\" --due 'tomorrow 9am'\n"
            "  kb remind                           what's due right now\n"
            "  kb remind install                   check every 5 minutes, notify on due\n"
            "  kb remind test                      send a test notification (and say how to fix it if not)\n"
            "  kb todo snooze <id> 1h              hide it, remind me again in an hour\n"
            "  kb remind push deploy               set up (or update) the Cloudflare Worker that pushes to your phone\n"
            "  kb remind push init                 just copy its source out, to deploy by hand\n"
            "  kb remind push setup <url>          point kb at a Worker you deployed yourself\n"
            "  kb remind push devices [rm <id>]    list the phones that get alerts, or remove one\n"
            "  kb remind status | uninstall"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("action", nargs="?", default="list",
                    choices=["list", "check", "test", "install", "uninstall", "status", "push"])
    p.add_argument("rest", nargs="*", metavar="ARG",
                    help="for `push`: init [dir] | deploy | setup <worker-url> | sync | status | off")
    p.add_argument("--ask", action="store_true", help=argparse.SUPPRESS)  # `kb update`: offer, don't just do
    p.add_argument("--interval-minutes", type=int, default=5, metavar="MIN",
                    help="how often to run `kb remind check` (default: 5; install only)")
    p.add_argument("--repeat-hours", type=float, default=4, metavar="H",
                    help="re-announce a still-overdue TODO after this long (default: 4; check only)")
    p.add_argument("--repo", metavar="DIR",
                    help="data repo to watch (default: cwd's repo, falling back to the central ~/.pkb; install only)")
    p.set_defaults(func=cmd_remind)

    p = sub.add_parser("bd", help="run any bd command against the resolved data repo "
                                   "(e.g. kb bd show kb-nux, kb bd close kb-nux)")
    p.add_argument("bd_args", nargs=argparse.REMAINDER, help="arguments passed straight to bd")
    p.set_defaults(func=cmd_bd)

    p = sub.add_parser("secrets", help="edit encrypted memos/gitlab/github credentials via sops")
    p.set_defaults(func=cmd_secrets)

    p = sub.add_parser(
        "config", help="view or set kb's own machine-local preferences (currently: editor)",
        description="View or set kb's own machine-local preferences (currently just: editor). "
                     "Separate from a data repo's .pkb/config.yml, which is committed and shared.",
    )
    p.add_argument("key", choices=["editor"], help="which preference")
    p.add_argument("value", nargs="?", default=None, metavar="VALUE",
                    help="new value to save (omit to show the current one and its source)")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser(
        "setup", help="guided onboarding: PATH install, optional deps, pre-commit hook, index, bd store",
        description="Guided onboarding. Bootstraps a central kb at ~/.pkb if no data repo is found, "
                     "then walks through installing kb onto PATH, the Claude Code skill, and any "
                     "missing optional CLI deps (bd/beads, glab, gh, rg, fzf, sops) via brew -- asking "
                     "before each -- before wiring up the pre-commit hook, search index, and local bd "
                     "store. Safe to re-run any time; every step is idempotent.",
    )
    p.add_argument("-y", "--yes", action="store_true",
                    help="skip prompts and accept defaults (for scripted installs)")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser(
        "web", aliases=["open"], help="run the local web UI, or open it if already running",
        description="Run the local web UI: a browser-based feed + quick-capture "
                     "view over the same data repo the CLI operates on. Binds "
                     "127.0.0.1 only. Runs in the foreground until Ctrl-C.",
    )
    p.add_argument("--port", type=int, default=4173, metavar="PORT",
                    help="port to bind (default: 4173)")
    p.add_argument("--no-open", action="store_true",
                    help="don't open a browser tab (used by `kb service`, so a login-scoped "
                         "background instance doesn't pop a tab on every boot)")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser(
        "service", help="install/uninstall a login service for `kb web` (launchd/systemd)",
        description="Run `kb web` as a login-scoped background service, so it's always "
                     "reachable at http://127.0.0.1:PORT without running `kb web` by hand. "
                     "Generates and loads a launchd LaunchAgent (macOS) or a systemd --user "
                     "unit (Linux) -- both still bind 127.0.0.1 only, same as `kb web` itself.",
        epilog=(
            "  kb service install                  install + start, serving the current repo\n"
            "  kb service install --port 8080\n"
            "  kb service install --repo ~/notes   point the service at a specific data repo\n"
            "  kb service status\n"
            "  kb service refresh                  repoint installed web/sync services at this install and restart them\n"
            "  kb service uninstall"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("action", choices=["install", "uninstall", "status", "refresh"])
    p.add_argument("--port", type=int, default=4173, metavar="PORT",
                    help="port to bind (default: 4173; install only)")
    p.add_argument("--repo", metavar="DIR",
                    help="data repo to serve (default: resolved the same way `kb` normally "
                         "does -- cwd's repo, falling back to the central ~/.pkb; install only)")
    p.set_defaults(func=service.cmd_service)

    p = sub.add_parser(
        "sync-service", help="install/uninstall a recurring `kb sync` timer (launchd/systemd)",
        description="Run `kb sync` on a recurring interval instead of by hand or via a "
                     "Claude Code slash command. Generates and loads a launchd LaunchAgent "
                     "with StartInterval (macOS) or a systemd --user timer (Linux).",
        epilog=(
            "  kb sync-service install                       every 60 minutes, current repo\n"
            "  kb sync-service install --interval-minutes 15\n"
            "  kb sync-service install --repo ~/notes         point at a specific data repo\n"
            "  kb sync-service status\n"
            "  kb sync-service uninstall"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("action", choices=["install", "uninstall", "status"])
    p.add_argument("--interval-minutes", type=int, default=60, metavar="MIN",
                    help="how often to run `kb sync` (default: 60; install only)")
    p.add_argument("--repo", metavar="DIR",
                    help="data repo to sync (default: resolved the same way `kb` normally "
                         "does -- cwd's repo, falling back to the central ~/.pkb; install only)")
    p.set_defaults(func=service.cmd_sync_service)

    p = sub.add_parser("doctor", help="diagnose issues (read-only, never writes anything)")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("update", help="check for and pull kb tool updates")
    p.add_argument("--check", action="store_true", help="only check, don't pull even if behind")
    p.set_defaults(func=updater.cmd_update)

    p = sub.add_parser("help", help="show this help message")
    p.set_defaults(func=lambda args: parser.print_help() or 0)

    project.add_parsers(sub)
    projdocs.add_parser(sub)

    parser._subcommands = list(sub.choices)  # for the "did you mean" suggestion
    return parser


def main():
    parser = build_parser()
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)
    args = parser.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
