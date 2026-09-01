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
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pkb_common as pc


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


def existing_source_paths(root, source):
    """Map source_id -> mirror file path for every already-synced item.

    Keyed on source_id, not path, so a re-fetch of a changed item updates the
    same mirror file in place instead of creating a duplicate (see write_bead).
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


def write_bead(root, bead, existing_paths, all_ids, inbox_all):
    """Write or refresh the sources/beads/ mirror for one bead.

    A source_id already on disk gets its mirror updated in place (title/body/
    updated timestamp) rather than skipped outright -- otherwise a bead that
    changes after its first sync (closed, re-titled, commented on) would leave
    a permanently stale mirror. The inbox stub is only ever created once, on
    first sight, so this can't reopen something already triaged out of inbox.
    Returns (path, "added" | "updated" | "unchanged").
    """
    source_id = str(bead.get("id"))
    created = bead.get("created_at") or pc.now_iso()
    updated = bead.get("updated_at") or created
    title = bead.get("title") or bead.get("summary") or f"bead {source_id}"
    body = bead.get("description") or bead.get("body") or ""

    existing_path = existing_paths.get(source_id)
    if existing_path:
        fm, _ = pc.read_entry(existing_path)
        if fm.get("updated") == updated and fm.get("title") == title:
            return existing_path, "unchanged"
        fm["updated"] = updated
        fm["title"] = title
        pc.write_entry(existing_path, fm, body)
        return existing_path, "updated"

    entry_id = pc.gen_id(all_ids)
    fm = {
        "id": entry_id,
        "created": created,
        "updated": updated,
        "type": "source",
        "extension": "source",
        "source": "beads",
        "source_id": source_id,
        "tags": [],
        "links": [],
        "title": title,
    }
    path = os.path.join(root, "sources", "beads", f"{entry_id}.md")
    pc.write_entry(path, fm, body)

    if inbox_all:
        inbox_id = pc.gen_id(all_ids)
        inbox_fm = dict(fm)
        inbox_fm.update({"id": inbox_id, "type": "inbox", "extension": "inbox", "links": [entry_id]})
        inbox_path = os.path.join(root, "inbox", f"{inbox_id}.md")
        pc.write_entry(inbox_path, inbox_fm, f"Synced from beads (see [{entry_id}]).\n\n{body}")

    return path, "added"


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

    cursors = pc.load_cursors(root)
    since_timestamp = cursors.get("beads", {}).get("last_timestamp")

    try:
        beads = fetch_beads(cli, since_timestamp, directory=directory, use_global=use_global)
    except RuntimeError as e:
        pc.fail(str(e))

    if not beads:
        print("sync_beads: no new/updated beads")
        return

    existing_paths = existing_source_paths(root, "beads")
    all_ids = pc.collect_existing_ids(root)
    added, updated_count = 0, 0
    last_ts = since_timestamp
    try:
        for bead in beads:
            path, outcome = write_bead(root, bead, existing_paths, all_ids, config["inbox_all"])
            existing_paths[str(bead.get("id"))] = path
            if outcome == "added":
                added += 1
            elif outcome == "updated":
                updated_count += 1
            last_ts = bead.get("updated_at") or last_ts
    except Exception as e:
        pc.fail(f"failed writing bead {bead.get('id')}: {e} (cursor not advanced)")

    cursors.setdefault("beads", {})["last_timestamp"] = last_ts
    pc.save_cursors(cursors, root)
    print(f"sync_beads: {added} new, {updated_count} updated, cursor advanced to {last_ts}")


if __name__ == "__main__":
    main()
