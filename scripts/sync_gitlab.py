#!/usr/bin/env python3
"""Ingest GitLab issues into sources/gitlab/ via the `glab api` CLI.

Same contract as sync_memos.py: cursor-based, idempotent on source_id,
cursor advances only after a fully successful write pass.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pkb_common as pc


def fetch_issues(project, updated_after):
    args = [
        "glab", "api",
        f"projects/{urlquote(project)}/issues",
        "-X", "GET",
        "-f", "scope=all",
        "-f", "order_by=updated_at",
        "-f", "sort=asc",
        "-f", "per_page=100",
    ]
    if updated_after:
        # small lookback so same-second updates aren't permanently skipped by GitLab's
        # strict '>' filter; dedup-by-source_id in write_issue makes re-fetches a no-op
        args += ["-f", f"updated_after={pc.cursor_lookback(updated_after)}"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError("glab CLI not found on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("glab api call timed out")
    if result.returncode != 0:
        raise RuntimeError(f"glab api failed: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"unexpected glab api response: {e}")


def urlquote(s):
    import urllib.parse
    return urllib.parse.quote(s, safe="")


def existing_source_paths(root, source):
    """Map source_id -> mirror file path for every already-synced item.

    Keyed on source_id, not path, so a re-fetch of a changed item updates the
    same mirror file in place instead of creating a duplicate (see write_issue).
    """
    paths = {}
    for path in pc.iter_markdown_files(root):
        rel = os.path.relpath(path, root)
        if not rel.startswith(f"sources{os.sep}{source}{os.sep}"):
            continue
        try:
            fm, _ = pc.read_entry(path)
        except ValueError:
            continue
        if fm.get("source_id"):
            paths[str(fm["source_id"])] = path
    return paths


def write_issue(root, issue, existing_paths, all_ids, inbox_all):
    """Write or refresh the sources/gitlab/ mirror for one issue.

    A source_id already on disk gets its mirror updated in place (title/body/
    updated timestamp) rather than skipped outright -- otherwise an issue that
    changes after its first sync (retitled, edited, closed) would leave a
    permanently stale mirror. The inbox stub is only ever created once, on
    first sight, so this can't reopen something already triaged out of inbox.
    Returns (path, "added" | "updated" | "unchanged").
    """
    source_id = str(issue["iid"])
    created = issue.get("created_at") or pc.now_iso()
    updated = issue.get("updated_at") or created
    title = issue.get("title") or f"issue !{source_id}"
    body = issue.get("description") or ""
    web_url = issue.get("web_url") or ""
    content = f"{body}\n\n[GitLab issue]({web_url})\n" if web_url else body

    existing_path = existing_paths.get(source_id)
    if existing_path:
        fm, _ = pc.read_entry(existing_path)
        if fm.get("updated") == updated and fm.get("title") == title:
            return existing_path, "unchanged"
        fm["updated"] = updated
        fm["title"] = title
        pc.write_entry(existing_path, fm, content)
        return existing_path, "updated"

    entry_id = pc.gen_id(all_ids)
    fm = {
        "id": entry_id,
        "created": created,
        "updated": updated,
        "type": "source",
        "extension": "source",
        "source": "gitlab",
        "source_id": source_id,
        "tags": [],
        "links": [],
        "title": title,
    }
    path = os.path.join(root, "sources", "gitlab", f"{entry_id}.md")
    pc.write_entry(path, fm, content)

    if inbox_all:
        inbox_id = pc.gen_id(all_ids)
        inbox_fm = dict(fm)
        inbox_fm.update({"id": inbox_id, "type": "inbox", "extension": "inbox", "links": [entry_id]})
        inbox_path = os.path.join(root, "inbox", f"{inbox_id}.md")
        pc.write_entry(inbox_path, inbox_fm, f"Synced from GitLab (see [{entry_id}]).\n\n{content}")

    return path, "added"


def main():
    root = pc.get_repo_root()
    config = pc.load_config(root)["sync"]["gitlab"]
    project = os.environ.get(config["project_env"])
    if not project:
        pc.fail(f"missing GitLab project: set ${config['project_env']} in the environment")

    cursors = pc.load_cursors(root)
    updated_after = cursors.get("gitlab", {}).get("last_updated_after")

    try:
        issues = fetch_issues(project, updated_after)
    except RuntimeError as e:
        pc.fail(str(e))

    if not issues:
        print("sync_gitlab: no new/updated issues")
        return

    existing_paths = existing_source_paths(root, "gitlab")
    all_ids = pc.collect_existing_ids(root)
    added, updated_count = 0, 0
    last_updated = updated_after
    try:
        for issue in issues:
            path, outcome = write_issue(root, issue, existing_paths, all_ids, config["inbox_all_issues"])
            existing_paths[str(issue["iid"])] = path
            if outcome == "added":
                added += 1
            elif outcome == "updated":
                updated_count += 1
            last_updated = issue.get("updated_at") or last_updated
    except Exception as e:
        pc.fail(f"failed writing issue {issue.get('iid')}: {e} (cursor not advanced)")

    cursors.setdefault("gitlab", {})["last_updated_after"] = last_updated
    pc.save_cursors(cursors, root)
    print(f"sync_gitlab: {added} new, {updated_count} updated, cursor advanced to {last_updated}")


if __name__ == "__main__":
    main()
