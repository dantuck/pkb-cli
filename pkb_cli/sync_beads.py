#!/usr/bin/env python3
"""Ingest beads into sources/beads/ via the local `bd` CLI (gastownhall/beads).

Same contract as sync_memos.py / sync_gitlab.py. Verified against bd 1.2.2:
`bd list --json --all --updated-after <RFC3339> --limit 0` returns a bare JSON
array of issues with fields id/title/description/status/priority/issue_type/
created_at/updated_at (RFC3339, UTC). No pagination cursor beyond --updated-after.
"""
import json
import os
import subprocess

from . import pkb_common as pc

# Read by kb's discover_sync_sources() -- see docs/reference/ingestion.md for
# the SOURCE_META contract. No auth_check/hints/env_keys/uses_sops: beads is
# local-only (no login, no secret to inject), and its tool-presence + local
# store setup already get their own bespoke checks in `kb setup`/`kb doctor`
# (store init, git-exclude verification) that a generic cli_tool block can't
# express -- cli_tool is declared anyway so it still counts as "configured"
# once `bd` is on PATH.
SOURCE_META = {
    "cli_tool": "bd",
    "brew_formula": "beads",
}


def fetch_beads(cli, since_timestamp, directory=None, use_global=False):
    # bd auto-discovers .beads/*.db from the cwd upward, but that's the pkb repo's
    # cwd, not necessarily where a bd store lives -- point it explicitly at either a
    # configured project directory (-C) or bd's own cross-project global store.
    args = [cli, "list", "--json", "--all", "--limit", "0"]
    if directory:
        args += ["-C", directory]
    elif use_global:
        args += ["--global"]
    if since_timestamp:
        # small lookback so same-second updates aren't permanently skipped by the
        # strict '>' filter; dedup-by-source_id in write_bead makes re-fetches a no-op
        args += ["--updated-after", pc.cursor_lookback(since_timestamp)]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise RuntimeError(f"'{cli}' CLI not found on PATH")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"'{cli}' call timed out")
    if result.returncode != 0:
        raise RuntimeError(f"'{cli}' failed: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"unexpected '{cli}' output: {e}")
    beads = data if isinstance(data, list) else data.get("beads", [])
    beads.sort(key=lambda b: str(b.get("updated_at") or b.get("id")))
    return beads


def write_bead(root, bead, existing_paths, all_ids, inbox_all):
    """Write or refresh the sources/beads/ mirror for one bead (see pc.write_source_mirror)."""
    source_id = str(bead.get("id"))
    created = bead.get("created_at") or pc.now_iso()
    updated = bead.get("updated_at") or created
    title = bead.get("title") or bead.get("summary") or f"bead {source_id}"
    body = bead.get("description") or bead.get("body") or ""
    return pc.write_source_mirror(root, "beads", "beads", source_id, title, body, created, updated,
                                  existing_paths, all_ids, inbox_all)

def main():
    root = pc.get_repo_root()
    config = pc.load_config(root)["sync"]["beads"]
    cli = config["cli"]
    use_global = bool(os.environ.get("PKB_BEADS_GLOBAL"))
    # default to the resolved data repo root, not bd's own cwd-based auto-discovery:
    # kb can resolve `root` via its home-directory fallback (see find_repo_root)
    # even when invoked from an unrelated cwd, but bd has no such fallback of its
    # own -- pointing it at `root` explicitly keeps behavior consistent regardless
    # of where kb was actually invoked from. PKB_BEADS_DIR overrides for a
    # different project's store; PKB_BEADS_GLOBAL opts into bd's shared store.
    directory = os.environ.get("PKB_BEADS_DIR") or (None if use_global else root)

    since_timestamp = pc.load_cursors(root).get("beads", {}).get("last_timestamp")
    try:
        beads = fetch_beads(cli, since_timestamp, directory=directory, use_global=use_global)
    except RuntimeError as e:
        pc.fail(str(e))
    pc.mirror_items(root, "beads", beads, lambda b: b.get("id"),
                    lambda b, paths, ids: write_bead(root, b, paths, ids, config["inbox_all"]),
                    "last_timestamp", since_timestamp, noun="bead")

if __name__ == "__main__":
    main()
