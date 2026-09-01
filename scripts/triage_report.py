#!/usr/bin/env python3
"""Scan inbox/ and flag files older than the configured threshold as overdue."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import pkb_common as pc


def scan_inbox(root):
    threshold_days = pc.load_config(root).get("inbox_triage_days", 14)
    inbox_dir = os.path.join(root, "inbox")
    rows = []
    now = datetime.now().astimezone()

    if not os.path.isdir(inbox_dir):
        return [], threshold_days

    for dirpath, _, filenames in os.walk(inbox_dir):
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            try:
                fm, _ = pc.read_entry(path)
                created = pc.parse_iso(fm.get("created"))
                title = fm.get("title") or ""
                entry_id = fm.get("id") or ""
            except ValueError:
                created = None
                title = ""
                entry_id = ""
            if created is None:
                created = datetime.fromtimestamp(os.path.getmtime(path)).astimezone()
            age_days = (now - created).days
            rows.append({
                "path": rel,
                "id": entry_id,
                "title": title,
                "age_days": age_days,
                "overdue": age_days > threshold_days,
            })

    rows.sort(key=lambda r: -r["age_days"])
    return rows, threshold_days


def main():
    root = pc.get_repo_root()
    rows, threshold_days = scan_inbox(root)
    if not rows:
        print("triage: inbox is empty")
        return 0

    overdue = [r for r in rows if r["overdue"]]
    print(f"triage: {len(rows)} item(s) in inbox, {len(overdue)} overdue (> {threshold_days}d)\n")
    for r in rows:
        flag = "OVERDUE" if r["overdue"] else "ok"
        print(f"  [{flag:7}] {r['age_days']:>4}d  {r['id'] or '?':<18} {r['title'] or r['path']}")
    if overdue:
        print("\nrun `kb inbox` to promote/redirect/discard -- triage only reports, it doesn't act")
    return 1 if overdue else 0


if __name__ == "__main__":
    sys.exit(main())
