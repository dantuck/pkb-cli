"""Resolving inbox items: promote into a Diataxis type, redirect into the journal, or discard. Shared by kb inbox and kb web."""
import os
from datetime import datetime

from . import journal
from . import pkb_common as pc
from . import pkb_entries


def promote(root, path, fm, body, target_type, json_out=False):
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
    pc.emit_result(json_out, {"action": "promote", "id": fm["id"], "type": target_type, "path": rel},
          f"promoted -> {rel}")
    pkb_entries._reindex_fast(root)
    pc.git_autocommit(root, f"inbox promote: {title} -> {target_type}")
    return 0


def redirect(root, path, fm, body, json_out=False):
    """Append an inbox item into its created-date's journal entry (creating
    that entry first if needed) and remove it from inbox/. Shared by the
    interactive picker and non-interactive `kb inbox <id> redirect`."""
    title = fm.get("title") or os.path.basename(path)
    created_dt = pc.parse_iso(fm.get("created")) or datetime.now().astimezone()
    journal_path = journal._ensure_journal_entry(root, created_dt.date())
    with open(journal_path, "r", encoding="utf-8") as f:
        existing = f.read()
    with open(journal_path, "w", encoding="utf-8") as f:
        f.write(existing.rstrip("\n") + f"\n\n## {title}\n\n{body.strip()}\n")
    os.remove(path)
    rel = os.path.relpath(journal_path, root)
    pc.emit_result(json_out, {"action": "redirect", "id": fm.get("id"), "path": rel}, f"redirected -> {rel}")
    pkb_entries._reindex_fast(root)
    pc.git_autocommit(root, f"inbox redirect: {title}")
    return 0


def discard(root, path, json_out=False):
    name = os.path.basename(path)
    os.remove(path)
    pc.emit_result(json_out, {"action": "discard", "name": name}, f"discarded {name}")
    pkb_entries._reindex_fast(root)
    pc.git_autocommit(root, f"inbox discard: {name}")
    return 0


def find(inbox_dir, item_id):
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
