#!/usr/bin/env python3
"""Ingest GitHub issues into sources/github/ via the `gh api` CLI.

Same contract as sync_gitlab.py / sync_memos.py: cursor-based, idempotent on
source_id, cursor advances only after a fully successful write pass.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pkb_common as pc

# Read by kb's discover_sync_sources() -- see docs/reference/ingestion.md for
# the SOURCE_META contract.
SOURCE_META = {
    "cli_tool": "gh",
    "brew_formula": "gh",
    "auth_check": ["gh", "auth", "status"],
    "not_found_hint": "gh CLI not found -- `brew install gh` (+ `gh auth login`) if you want kb sync github",
    "not_authed_hint": "gh is installed but not authenticated -- run `gh auth login`",
    "env_keys": ["PKB_GITHUB_REPO"],
    "uses_sops": True,
}


def fetch_issues(repo, updated_after):
    args = [
        "gh", "api",
        f"repos/{repo}/issues",
        "-X", "GET",
        "-f", "state=all",
        "-f", "sort=updated",
        "-f", "direction=asc",
        "-f", "per_page=100",
    ]
    if updated_after:
        # small lookback so same-second updates aren't permanently skipped by GitHub's
        # strict '>' filter; dedup-by-source_id in write_issue makes re-fetches a no-op
        args += ["-f", f"since={pc.cursor_lookback(updated_after)}"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError("gh CLI not found on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh api call timed out")
    if result.returncode != 0:
        raise RuntimeError(f"gh api failed: {result.stderr.strip()}")
    try:
        issues = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"unexpected gh api response: {e}")
    # the issues endpoint also returns pull requests (they carry a
    # "pull_request" key) -- this sync is for issues only
    return [i for i in issues if "pull_request" not in i]


def write_issue(root, issue, existing_paths, all_ids, inbox_all):
    """Write or refresh the sources/github/ mirror for one issue.

    A source_id already on disk gets its mirror updated in place (title/body/
    updated timestamp) rather than skipped outright -- otherwise an issue that
    changes after its first sync (retitled, edited, closed) would leave a
    permanently stale mirror. The inbox stub is only ever created once, on
    first sight, so this can't reopen something already triaged out of inbox.
    Returns (path, "added" | "updated" | "unchanged").
    """
    source_id = str(issue["number"])
    created = issue.get("created_at") or pc.now_iso()
    updated = issue.get("updated_at") or created
    title = issue.get("title") or f"issue #{source_id}"
    body = issue.get("body") or ""
    html_url = issue.get("html_url") or ""
    content = f"{body}\n\n[GitHub issue]({html_url})\n" if html_url else body

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
        "source": "github",
        "source_id": source_id,
        "tags": [],
        "links": [],
        "title": title,
    }
    path = os.path.join(root, "sources", "github", f"{entry_id}.md")
    pc.write_entry(path, fm, content)

    if inbox_all:
        inbox_id = pc.gen_id(all_ids)
        inbox_fm = dict(fm)
        inbox_fm.update({"id": inbox_id, "type": "inbox", "extension": "inbox", "links": [entry_id]})
        inbox_path = os.path.join(root, "inbox", f"{inbox_id}.md")
        pc.write_entry(inbox_path, inbox_fm, f"Synced from GitHub (see [{entry_id}]).\n\n{content}")

    return path, "added"


def main():
    root = pc.get_repo_root()
    config = pc.load_config(root)["sync"]["github"]
    repo = os.environ.get(config["repo_env"])
    if not repo:
        pc.fail(f"missing GitHub repo: set ${config['repo_env']} in the environment (owner/repo)")

    cursors = pc.load_cursors(root)
    updated_after = cursors.get("github", {}).get("last_updated_after")

    try:
        issues = fetch_issues(repo, updated_after)
    except RuntimeError as e:
        pc.fail(str(e))

    if not issues:
        print("sync_github: no new/updated issues")
        return

    existing_paths = pc.existing_source_paths(root, "github")
    all_ids = pc.collect_existing_ids(root)
    added, updated_count = 0, 0
    last_updated = updated_after
    try:
        for issue in issues:
            path, outcome = write_issue(root, issue, existing_paths, all_ids, config["inbox_all_issues"])
            existing_paths[str(issue["number"])] = path
            if outcome == "added":
                added += 1
            elif outcome == "updated":
                updated_count += 1
            last_updated = issue.get("updated_at") or last_updated
    except Exception as e:
        pc.fail(f"failed writing issue {issue.get('number')}: {e} (cursor not advanced)")

    cursors.setdefault("github", {})["last_updated_after"] = last_updated
    pc.save_cursors(cursors, root)
    print(f"sync_github: {added} new, {updated_count} updated, cursor advanced to {last_updated}")


if __name__ == "__main__":
    main()
