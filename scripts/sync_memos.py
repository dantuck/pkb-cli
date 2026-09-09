#!/usr/bin/env python3
"""Ingest usememos memos into sources/memos/, one markdown file per memo.

Contract (see docs/reference/ingestion.md):
1. Read cursor from .pkb/cursors.json.
2. Fetch only memos created/updated after the cursor.
3. Write one file per item into sources/memos/, keyed on source_id (idempotent).
4. Stub anything above the configured length threshold into inbox/ for triage
   (inbox_min_length: null disables this -- no memo ever gets an inbox stub).
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

Attachments (images and other files) are exposed inline on each memo as an
`attachments` array (NOT `resources` -- that's an older/other API version's
name), each shaped like {"name": "attachments/{id}", "filename", "content"
(empty here in practice), "externalLink" (empty unless the instance stores
attachments externally), "type" (mime), "size"}. Confirmed against a real
instance with an image-attached memo. The actual bytes are fetched from
`{base_url}/file/{name}/{filename}` with the same Bearer token -- this
endpoint returned 200 with the exact `size` byte count and correct
Content-Type; `/api/v1/{name}` by contrast returns only the JSON metadata
above (empty content), and `/api/v1/{name}/blob` 404s. externalLink, when
set, is used directly (no Bearer auth -- it's not this instance's API).
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(__file__))
import pkb_common as pc

# Read by kb's discover_sync_sources() -- see docs/reference/ingestion.md for
# the SOURCE_META contract. No cli_tool: memos talks straight to the REST API
# over urllib, no external CLI to check for.
SOURCE_META = {
    "env_keys": ["PKB_MEMOS_URL", "PKB_MEMOS_TOKEN"],
    "uses_sops": True,
}


def memo_source_id(memo):
    # name looks like "memos/101"
    name = memo.get("name", "")
    return name.rsplit("/", 1)[-1] if name else None


_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_attachment_filename(attachment):
    """A filesystem- and markdown-path-safe filename for one attachment.

    The API's `filename` is server-controlled but not trusted here: strip any
    directory components (a defensively-basename'd value, in case a hostile
    or buggy instance sends one embedding "../") and replace anything outside
    a conservative safe set, so it can never escape the per-entry assets dir
    it's written into. Falls back to the attachment id when the name is
    empty or collapses entirely (e.g. a filename that was only "../../").
    """
    name = os.path.basename(attachment.get("filename") or "")
    name = _UNSAFE_FILENAME_RE.sub("_", name).strip("._") or "attachment"
    attachment_id = attachment.get("name", "").rsplit("/", 1)[-1]
    return f"{attachment_id}-{name}" if attachment_id else name


def _authed_get(req, timeout, what):
    """Run one urlopen(req) and return the raw response bytes, wrapping any
    failure in a RuntimeError tagged with `what` (shared by fetch_memos and
    fetch_attachment_bytes, which otherwise duplicated this exact try/except
    scaffolding). HTTPError is caught first -- it's a URLError subclass, so
    catching URLError first would silently swallow it before this branch ever
    ran, losing the HTTP status code from the message."""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{what} auth/HTTP error ({e.code}): {e}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"failed to reach {what}: {e}")


def fetch_attachment_bytes(base_url, token, attachment):
    """Return the raw bytes for one memo attachment. Raises RuntimeError on
    network/auth failure (same failure contract as fetch_memos)."""
    external = attachment.get("externalLink")
    if external:
        req = urllib.request.Request(external)
    else:
        quoted_filename = urllib.parse.quote(attachment.get("filename") or "")
        url = f"{base_url.rstrip('/')}/file/{attachment['name']}/{quoted_filename}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    return _authed_get(req, timeout=30, what=f"attachment {attachment.get('name')}")


def sync_attachments(root, entry_id, attachments, base_url, token):
    """Download every attachment on one memo into sources/memos/assets/<entry_id>/,
    skipping any file already on disk (attachments are immutable once created,
    so this is idempotent and cheap to re-run on every sync pass, including for
    memos whose text is otherwise unchanged -- heals a prior run that wrote the
    markdown mirror but crashed partway through downloading images).

    Returns a list of (relative_markdown_path, filename, mime) for use in the
    entry body, in attachment order.
    """
    results = []
    if not attachments:
        return results
    asset_dir = os.path.join(root, "sources", "memos", "assets", entry_id)
    os.makedirs(asset_dir, exist_ok=True)
    for attachment in attachments:
        filename = safe_attachment_filename(attachment)
        dest = os.path.join(asset_dir, filename)
        if not os.path.exists(dest):
            data = fetch_attachment_bytes(base_url, token, attachment)
            tmp = dest + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
        # Root-relative (leading "/"), not relative to whichever file embeds it: the
        # same rendered content is also copied verbatim into an inbox/ stub (see
        # write_memo), a directory a path relative to sources/memos/ wouldn't resolve
        # from. kb web serves any path under a known content dir (sources/, etc.)
        # straight off the data repo root -- see _serve_static in kb_web.py -- and
        # the client's markdown renderer already treats a leading "/" as a safe,
        # renderable link/image target.
        results.append((f"/sources/memos/assets/{entry_id}/{filename}", filename, attachment.get("type") or ""))
    return results


def attachments_markdown(attachments):
    """Render synced attachments as markdown: images inline via `![]()` (kb web
    serves the root-relative path directly -- see _serve_static in kb_web.py);
    anything else (pdf, audio, ...) as a plain link so it's still reachable,
    just not inlined."""
    lines = []
    for rel_path, filename, mime in attachments:
        if mime.startswith("image/"):
            lines.append(f"![{filename}]({rel_path})")
        else:
            lines.append(f"[{filename}]({rel_path})")
    return "\n\n".join(lines)


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
        data = json.loads(_authed_get(req, timeout=15, what=f"memos API at {url}").decode("utf-8"))

        memos.extend(data.get("memos", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    memos.sort(key=lambda m: m.get("updateTime") or "")
    return memos


def write_memo(root, memo, existing_paths, all_ids, threshold, base_url, token):
    """Write or refresh the sources/memos/ mirror for one memo.

    A source_id already on disk gets its mirror updated in place (title/body/
    tags/updated timestamp) rather than skipped outright -- otherwise an edited
    memo would leave a permanently stale mirror. The inbox stub is only ever
    created once, on first sight, so this can't reopen something already
    triaged out of inbox.

    Attachments are synced (downloaded if missing) on every pass, even the
    "unchanged" fast path -- see sync_attachments -- so a crash partway
    through a previous run's image downloads gets healed on the next sync
    instead of leaving the mirror's images permanently missing.

    Returns (path, "added" | "updated" | "unchanged"). threshold=None means the
    inbox stub is disabled outright: every memo is mirrored into sources/memos/
    only, never duplicated into inbox/.
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

    def build_content(entry_id):
        attachments = sync_attachments(root, entry_id, memo.get("attachments"), base_url, token)
        parts = [p for p in (raw_content, attachments_markdown(attachments)) if p]
        parts.append(f"[Memo]({permalink})\n")
        return "\n\n".join(parts)

    existing_path = existing_paths.get(source_id)
    if existing_path:
        fm, _ = pc.read_entry(existing_path)
        if fm.get("updated") == updated and fm.get("title") == title:
            # heal any incomplete attachment download from a prior crashed run;
            # body itself is already correct, so skip reassembling it
            sync_attachments(root, fm["id"], memo.get("attachments"), base_url, token)
            return existing_path, "unchanged"
        fm["updated"] = updated
        fm["title"] = title
        fm["tags"] = tags
        pc.write_entry_readonly(existing_path, fm, build_content(fm["id"]))
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
    content = build_content(entry_id)
    path = os.path.join(root, "sources", "memos", f"{entry_id}.md")
    pc.write_entry_readonly(path, fm, content)

    if threshold is not None and len(raw_content) >= threshold:
        inbox_id = pc.gen_id(all_ids)
        inbox_fm = dict(fm)
        inbox_fm.update({"id": inbox_id, "type": "inbox", "extension": "inbox", "links": [entry_id]})
        inbox_path = os.path.join(root, "inbox", f"{inbox_id}.md")
        pc.write_entry_readonly(inbox_path, inbox_fm, f"Synced from memos (see [{entry_id}]).\n\n{content}")

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
            path, outcome = write_memo(root, memo, existing_paths, all_ids, config["inbox_min_length"], base_url, token)
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
