#!/usr/bin/env python3
"""Ingest GitLab issues into sources/gitlab/ via the `glab api` CLI.

Same contract as sync_memos.py: cursor-based, idempotent on source_id,
cursor advances only after a fully successful write pass.
"""
import json
import os
import subprocess

from . import pkb_common as pc

# Read by kb's discover_sync_sources() -- see docs/reference/ingestion.md for
# the SOURCE_META contract.
SOURCE_META = {
    "cli_tool": "glab",
    "brew_formula": "glab",
    "auth_check": ["glab", "auth", "status"],
    "not_found_hint": "glab CLI not found -- `brew install glab` (+ `glab auth login`) if you want kb sync gitlab",
    "not_authed_hint": "glab is installed but not authenticated -- run `glab auth login`",
    "env_keys": ["PKB_GITLAB_PROJECT"],
    "uses_sops": True,
}


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


def write_issue(root, issue, existing_paths, all_ids, inbox_all):
    """Write or refresh the sources/gitlab/ mirror for one issue (see pc.write_source_mirror)."""
    source_id = str(issue["iid"])
    created = issue.get("created_at") or pc.now_iso()
    updated = issue.get("updated_at") or created
    title = issue.get("title") or f"issue !{source_id}"
    body = issue.get("description") or ""
    web_url = issue.get("web_url") or ""
    content = f"{body}\n\n[GitLab issue]({web_url})\n" if web_url else body
    return pc.write_source_mirror(root, "gitlab", "GitLab", source_id, title, content, created, updated,
                                  existing_paths, all_ids, inbox_all)

def main():
    root = pc.get_repo_root()
    config = pc.load_config(root)["sync"]["gitlab"]
    project = os.environ.get(config["project_env"])
    if not project:
        pc.fail(f"missing GitLab project: set ${config['project_env']} in the environment")

    updated_after = pc.load_cursors(root).get("gitlab", {}).get("last_updated_after")
    try:
        issues = fetch_issues(project, updated_after)
    except RuntimeError as e:
        pc.fail(str(e))
    pc.mirror_items(root, "gitlab", issues, lambda i: i["iid"],
                    lambda i, paths, ids: write_issue(root, i, paths, ids, config["inbox_all_issues"]),
                    "last_updated_after", updated_after)

if __name__ == "__main__":
    main()
