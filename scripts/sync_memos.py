#!/usr/bin/env python3
"""Ingest usememos memos into sources/memos/, one markdown file per memo.

Contract (see docs/spec.md §4):
1. Read cursor from .pkb/cursors.json.
2. Fetch only memos created/updated after the cursor.
3. Write one file per item into sources/memos/, keyed on source_id (idempotent).
4. Stub anything above the configured length threshold into inbox/ for triage.
5. Advance the cursor only after a fully successful write pass.
6. Exit non-zero with a clear message on auth/network failure; never partially
   advance the cursor.

Verified against the usememos v1 REST API (ListMemos): the response is
{"memos": [...], "nextPageToken": ...}; each memo's id is embedded in its
`name` field as "memos/{id}" (not a top-level `id`); timestamps are
`createTime`/`updateTime` (ISO 8601); pagination is followed via `nextPageToken`;
the `filter` query param takes a CEL expression, e.g. updated_ts > timestamp("<iso>")
-- updated_ts is a native CEL timestamp type; comparing it to a bare string 400s
("found no matching overload for '_>_' applied to '(timestamp, string)'"), confirmed
against a real instance. Filtering on updated_ts (not created_ts) is what makes an
edited memo -- same createTime, new updateTime -- get re-fetched at all (see write_memo).
"""
import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
import pkb_common as pc


def memo_source_id(memo):
    # name looks like "memos/101"
    name = memo.get("name", "")
    return name.rsplit("/", 1)[-1] if name else None


def fetch_memos(base_url, token, since_updated_time):
    """Fetch memos updated after since_updated_time (exclusive). Raises on network/auth failure."""
    memos = []
    page_token = None
    while True:
        params = {
            "pageSize": "100",
            "orderBy": "update_time asc",
        }
        if since_updated_time:
            # small lookback so same-second updates aren't permanently skipped by the
            # strict '>' filter; dedup-by-source_id in write_memo makes re-fetches a no-op
            params["filter"] = f'updated_ts > timestamp("{pc.cursor_lookback(since_updated_time)}")'
        if page_token:
            params["pageToken"] = page_token
        url = f"{base_url.rstrip('/')}/api/v1/memos?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"failed to reach memos API at {url}: {e}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"memos API auth/HTTP error ({e.code}) at {url}: {e}")

        memos.extend(data.get("memos", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    memos.sort(key=lambda m: m.get("updateTime") or "")
    return memos


def write_memo(root, memo, existing_paths, all_ids, threshold, base_url):
    """Write or refresh the sources/memos/ mirror for one memo.

    A source_id already on disk gets its mirror updated in place (title/body/
    tags/updated timestamp) rather than skipped outright -- otherwise an edited
    memo would leave a permanently stale mirror. The inbox stub is only ever
    created once, on first sight, so this can't reopen something already
    triaged out of inbox.
    Returns (path, "added" | "updated" | "unchanged").
    """
    source_id = memo_source_id(memo)
    if not source_id:
        return None, "unchanged"

    created = memo.get("createTime") or pc.now_iso()
    updated = memo.get("updateTime") or created
    raw_content = memo.get("content", "")
    tags = memo.get("tags") or []
    title = (raw_content.strip().splitlines() or [""])[0][:80] or f"memo {source_id}"
    # web route confirmed against usememos' own frontend router source
    # (web/src/router/index.tsx: "memos/:uid" -> <MemoDetail />), not /m/<uid>.
    permalink = f"{base_url.rstrip('/')}/memos/{source_id}"
    content = f"{raw_content}\n\n[Memo]({permalink})\n" if raw_content else f"[Memo]({permalink})\n"

    existing_path = existing_paths.get(source_id)
    if existing_path:
        fm, _ = pc.read_entry(existing_path)
        if fm.get("updated") == updated and fm.get("title") == title:
            return existing_path, "unchanged"
        fm["updated"] = updated
        fm["title"] = title
        fm["tags"] = tags
        pc.write_entry(existing_path, fm, content)
        return existing_path, "updated"

    entry_id = pc.gen_id(all_ids)
    fm = {
        "id": entry_id,
        "created": created,
        "updated": updated,
        "type": "source",
        "extension": "source",
        "source": "memos",
        "source_id": source_id,
        "tags": tags,
        "links": [],
        "title": title,
    }
    path = os.path.join(root, "sources", "memos", f"{entry_id}.md")
    pc.write_entry(path, fm, content)

    if len(raw_content) >= threshold:
        inbox_id = pc.gen_id(all_ids)
        inbox_fm = dict(fm)
        inbox_fm.update({"id": inbox_id, "type": "inbox", "extension": "inbox", "links": [entry_id]})
        inbox_path = os.path.join(root, "inbox", f"{inbox_id}.md")
        pc.write_entry(inbox_path, inbox_fm, f"Synced from memos (see [{entry_id}]).\n\n{content}")

    return path, "added"


def main():
    root = pc.get_repo_root()
    config = pc.load_config(root)["sync"]["memos"]
    base_url = os.environ.get(config["base_url_env"])
    token = os.environ.get(config["token_env"])
    if not base_url or not token:
        pc.fail(
            f"missing memos credentials: set ${config['base_url_env']} and "
            f"${config['token_env']} in the environment"
        )

    cursors = pc.load_cursors(root)
    since_updated_time = cursors.get("memos", {}).get("last_updated_time")

    try:
        memos = fetch_memos(base_url, token, since_updated_time)
    except RuntimeError as e:
        pc.fail(str(e))

    if not memos:
        print("sync_memos: no new memos")
        return

    existing_paths = pc.existing_source_paths(root, "memos")
    all_ids = pc.collect_existing_ids(root)
    added, updated_count = 0, 0
    last_updated_time = since_updated_time
    try:
        for memo in memos:
            path, outcome = write_memo(root, memo, existing_paths, all_ids, config["inbox_min_length"], base_url)
            source_id = memo_source_id(memo)
            if source_id:
                existing_paths[source_id] = path
            if outcome == "added":
                added += 1
            elif outcome == "updated":
                updated_count += 1
            last_updated_time = memo.get("updateTime") or last_updated_time
    except Exception as e:
        pc.fail(f"failed writing memo {memo_source_id(memo)}: {e} (cursor not advanced)")

    cursors.setdefault("memos", {})["last_updated_time"] = last_updated_time
    pc.save_cursors(cursors, root)
    print(f"sync_memos: {added} new, {updated_count} updated, cursor advanced to {last_updated_time}")


if __name__ == "__main__":
    main()
