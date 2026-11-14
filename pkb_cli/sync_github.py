#!/usr/bin/env python3
"""Ingest GitHub issues into sources/github/ via the `gh api` CLI.

Same contract as sync_gitlab.py / sync_memos.py: cursor-based, idempotent on
source_id, cursor advances only after a fully successful write pass.
"""
import json
import os
import subprocess

from . import pkb_common as pc

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
    """Write or refresh the sources/github/ mirror for one issue (see pc.write_source_mirror)."""
    source_id = str(issue["number"])
    created = issue.get("created_at") or pc.now_iso()
    updated = issue.get("updated_at") or created
    title = issue.get("title") or f"issue #{source_id}"
    body = issue.get("body") or ""
    html_url = issue.get("html_url") or ""
    content = f"{body}\n\n[GitHub issue]({html_url})\n" if html_url else body
    return pc.write_source_mirror(root, "github", source_id, title, content, created, updated,
                                  existing_paths, all_ids, inbox_all, label="GitHub")


def main():
    root = pc.get_repo_root()
    config = pc.load_config(root)["sync"]["github"]
    repo = os.environ.get(config["repo_env"])
    if not repo:
        pc.fail(f"missing GitHub repo: set ${config['repo_env']} in the environment (owner/repo)")

    updated_after = pc.load_cursors(root).get("github", {}).get("last_updated_after")
    try:
        issues = fetch_issues(repo, updated_after)
    except RuntimeError as e:
        pc.fail(str(e))
    pc.mirror_items(root, "github", issues,
                    lambda i, paths, ids: write_issue(root, i, paths, ids, config["inbox_all_issues"]),
                    "last_updated_after", updated_after)


if __name__ == "__main__":
    main()
