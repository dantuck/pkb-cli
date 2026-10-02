"""Entry-level read/write operations on a data repo: create, retag, relink,
rewrite, delete, move, and the listing helpers behind them.

Shared by the `kb` CLI and `kb web`. Everything here operates on an explicit
`root` and returns plain dicts (`{"error": ...}` on failure) rather than
printing or exiting, so both front ends can render results their own way.
"""
import os
import re
from datetime import datetime

import pkb_common as pc


def _reindex_fast(root):
    """In-process incremental reindex -- same work as `run_script("index_fts.py")`
    but without paying for a fresh interpreter + subprocess on every call.
    Used by entry_set_tags/entry_set_links/entry_update_content, which kb
    web's edit panel can trigger repeatedly in quick succession; other
    callers keep using run_script for output capture / exit-code parity with
    `kb reindex`."""
    import index_fts
    index_fts.index_repo(root)


def _find_entry(root, entry_id):
    """(path, fm, body) for the entry with this id, or None. Shared by kb tag and
    kb link -- both need to locate and rewrite one specific entry by id, the only
    sanctioned way to touch tags/links now that hand-editing frontmatter is off
    limits (see SKILL.md's "never hand-edit frontmatter" rule)."""
    for path in pc.iter_markdown_files(root):
        try:
            fm, body = pc.read_entry(path)
        except ValueError:
            continue
        if fm.get("id") == entry_id:
            return path, fm, body
    return None


_SOURCE_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://\S+)\)\s*$")


def _readonly_source_info(path, body):
    """None if `path` is writable; otherwise (label, url) describing where to
    make the edit instead. A source mirror is written chmod 0o444 by
    write_entry_readonly (see sync_memos.py) precisely so a local edit can't
    be silently clobbered by the next sync pass -- checking the filesystem
    bit directly (rather than e.g. fm.get("source") == "memos") means this
    stays correct for any sync that adopts the same read-only convention,
    without this file needing to know which sources do.

    Every read-only-mirroring sync script appends exactly one trailing
    markdown link to the body naming where the item lives upstream (e.g.
    "[Memo](https://.../memos/101)") -- reused here as the "edit it there"
    pointer rather than reconstructing the URL from frontmatter."""
    if os.access(path, os.W_OK):
        return None
    m = _SOURCE_LINK_RE.search(body.strip())
    return (m.group(1), m.group(2)) if m else ("its source", None)


def _apply_tag_changes(tags, add=None, rm=None):
    """Pure list transform: apply add/rm to a list, returning (new_list,
    changed) -- the only place the actual add/rm set-diff logic lives. Used
    for both tags and links (the semantics are identical: dedup on add, drop
    on rm), via entry_set_tags/entry_set_links."""
    new_tags = list(tags)
    changed = False
    if add:
        additions = [t for t in add if t not in new_tags]
        new_tags.extend(additions)
        changed = changed or bool(additions)
    if rm:
        before = len(new_tags)
        new_tags = [t for t in new_tags if t not in rm]
        changed = changed or before != len(new_tags)
    return new_tags, changed


def _clean_list(items):
    """Strip whitespace, drop blanks, and dedupe (order-preserving) --
    shared by entry_new so every caller gets the same tags/links hygiene
    regardless of whether its input arrived pre-cleaned (CLI comma-split
    args) or raw (a JSON POST body)."""
    seen = set()
    cleaned = []
    for item in items or []:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            cleaned.append(item)
    return cleaned


def entry_new(root, entry_type, title, tags=None, links=None, body=None):
    """Create a new core-content entry (tutorial/how-to/reference/explanation)
    with correct frontmatter, write it to disk, and reindex. Returns
    {"id", "path"} for the created entry, or {"error": ...} if `entry_type`
    is invalid or `links` names id(s) that don't exist in this repo. Shared
    by cmd_new (CLI) and kb web's entry-creation endpoint."""
    if entry_type not in pc.CORE_TYPES:
        return {"error": f"type must be one of {list(pc.CORE_TYPES)}"}

    tags = _clean_list(tags)
    links = _clean_list(links)

    # kb web's server is threaded, so two POST /api/entries calls (or a web
    # create racing a CLI `kb new`) can otherwise both scan existing ids,
    # both land in the same gen_id() minute bucket, and both write the same
    # path -- the second write silently clobbering the first entry. Serialize
    # the scan-through-write on a repo-level lock (there's no entry file yet
    # to lock on, unlike entry_set_tags/entry_set_links/entry_update_content).
    pkb_dir = os.path.join(root, ".pkb")
    os.makedirs(pkb_dir, exist_ok=True)
    with pc.entry_lock(os.path.join(pkb_dir, "entry-new")):
        all_ids = pc.collect_existing_ids(root)
        unknown = [t for t in links if t not in all_ids]
        if unknown:
            return {"error": f"unknown link target id(s), not found in this repo: {', '.join(unknown)}"}

        entry_id = pc.gen_id(all_ids)
        now = pc.now_iso()
        fm = {
            "id": entry_id,
            "created": now,
            "updated": now,
            "type": entry_type,
            "extension": None,
            "source": "manual",
            "source_id": None,
            "tags": tags,
            "links": links,
            "title": title,
        }
        path = os.path.join(root, pc.TYPE_DIR[entry_type], f"{entry_id}-{pc.slugify(title)}.md")
        entry_body = f"# {title}\n\n{body.strip()}\n" if body else f"# {title}\n\n"
        pc.write_entry(path, fm, entry_body)
        _reindex_fast(root)
        pc.git_autocommit(root, f"new {entry_type}: {title}")
    return {"id": entry_id, "path": path}


def _readonly_error(label, url):
    where = f"{label} ({url})" if url else label
    return {"error": f"read-only: synced from {where} -- edit it there instead",
            "readonly": True, "source_label": label, "source_url": url}


def _readonly_guard(path, body):
    """{"error": ..., "readonly": True, ...} if `path` is a read-only source
    mirror, else None -- the guard entry_set_tags, entry_set_links, and
    entry_update_content each need before mutating an entry."""
    readonly = _readonly_source_info(path, body)
    return _readonly_error(*readonly) if readonly else None


def entry_set_tags(root, entry_id, add=None, rm=None):
    """Apply add/rm to an entry's tags and persist if anything changed.
    Returns the resulting tag list, {"error": ..., "readonly": True, ...} if
    the entry is a read-only source mirror (see _readonly_source_info), or
    None if no entry has this id. Shared by cmd_tag (CLI) and kb web's
    tag-edit endpoint."""
    found = _find_entry(root, entry_id)
    if found is None:
        return None
    path, _fm, body = found
    guard = _readonly_guard(path, body)
    if guard:
        return guard
    with pc.entry_lock(path):
        fm, body = pc.read_entry(path)
        tags, changed = _apply_tag_changes(fm.get("tags") or [], add=add, rm=rm)
        if changed:
            fm["tags"] = tags
            fm["updated"] = pc.now_iso()
            pc.write_entry(path, fm, body)
            _reindex_fast(root)
            pc.git_autocommit(root, f"tags: {entry_id}")
    return tags


def entry_set_links(root, entry_id, add=None, rm=None):
    """Same as entry_set_tags but for links. Returns the resulting link list;
    None if no entry has this id; {"error": ...} if `add` names id(s) that
    don't exist in the repo. Shared by cmd_link (CLI) and kb web's link-edit
    endpoint."""
    found = _find_entry(root, entry_id)
    if found is None:
        return None
    path, _fm, body = found
    guard = _readonly_guard(path, body)
    if guard:
        return guard
    if add:
        all_ids = pc.collect_existing_ids(root)
        unknown = [t for t in add if t not in all_ids]
        if unknown:
            return {"error": f"unknown id(s), not found in this repo: {', '.join(unknown)}"}
    with pc.entry_lock(path):
        fm, body = pc.read_entry(path)
        links, changed = _apply_tag_changes(list(fm.get("links") or []), add=add, rm=rm)
        if changed:
            fm["links"] = links
            fm["updated"] = pc.now_iso()
            pc.write_entry(path, fm, body)
            _reindex_fast(root)
            pc.git_autocommit(root, f"links: {entry_id}")
    return links


def entry_update_content(root, entry_id, title=None, body=None):
    """Overwrite an entry's title and/or body and persist if anything changed.
    Returns {"title", "body"} for the resulting entry, or None if no entry has
    this id. Shared by kb web's content-edit endpoint (no CLI counterpart yet
    -- editing full content is currently a web-only affordance)."""
    found = _find_entry(root, entry_id)
    if found is None:
        return None
    path, _fm, existing_body = found
    guard = _readonly_guard(path, existing_body)
    if guard:
        return guard
    with pc.entry_lock(path):
        fm, cur_body = pc.read_entry(path)
        changed = False
        if title is not None and title != fm.get("title"):
            fm["title"] = title
            changed = True
        if body is not None and body != cur_body:
            cur_body = body
            changed = True
        if changed:
            fm["updated"] = pc.now_iso()
            pc.write_entry(path, fm, cur_body)
            _reindex_fast(root)
            pc.git_autocommit(root, f"edit: {entry_id}")
    return {"title": fm.get("title"), "body": cur_body}


def entry_delete(root, entry_id, force=False):
    """Delete an entry by id. Refuses if other entries still link to it, unless
    force=True -- in which case this id is also stripped from every one of
    those entries' `links` first, so the delete can never leave a dangling
    reference for `kb validate` to catch later. Returns {"path",
    "backlinks_cleared"} on success, or {"error": ...}. Shared by cmd_rm (CLI)
    and kb web's delete endpoint.

    Finds the target and collects backlinks in one pass over the repo --
    locating the target and checking every entry's `links` both require
    reading every file's frontmatter anyway, so there's no early exit to lose
    by combining them (unlike _find_entry, which can stop as soon as it
    matches)."""
    path = None
    backlinks = []
    for p in pc.iter_markdown_files(root):
        try:
            fm, _body = pc.read_entry(p)
        except ValueError:
            continue
        if fm.get("id") == entry_id:
            path = p
            continue
        if entry_id in (fm.get("links") or []):
            backlinks.append((p, fm))

    if path is None:
        return {"error": f"no entry with id '{entry_id}'"}

    if backlinks and not force:
        ids = ", ".join(fm.get("id") for _p, fm in backlinks)
        return {"error": f"'{entry_id}' is linked from {len(backlinks)} entry(ies) ({ids}) "
                          f"-- pass --force to delete anyway (their links will be cleaned up)"}

    for p, _fm in backlinks:
        with pc.entry_lock(p):
            bfm, bbody = pc.read_entry(p)
            bfm["links"] = [l for l in (bfm.get("links") or []) if l != entry_id]
            bfm["updated"] = pc.now_iso()
            pc.write_entry(p, bfm, bbody)

    with pc.entry_lock(path):
        os.remove(path)
    _reindex_fast(root)
    pc.git_autocommit(root, f"rm: {entry_id}")
    return {"path": os.path.relpath(path, root), "backlinks_cleared": len(backlinks)}


def entry_move(root, entry_id, new_type=None, new_title=None):
    """Move a core entry to a different Diataxis type and/or rename its title,
    rewriting frontmatter and relocating the file to match -- the sanctioned
    way to fix a miscategorized or mistitled entry, since hand-editing
    frontmatter (or moving the file yourself, which would desync `type` from
    the directory it lives in) is off limits. Returns {"path"} on success, or
    {"error": ...}. Shared by cmd_mv (CLI) and kb web's move endpoint."""
    if new_type is None and new_title is None:
        return {"error": "need --type and/or --title"}
    if new_type is not None and new_type not in pc.CORE_TYPES:
        return {"error": f"type must be one of {list(pc.CORE_TYPES)}"}

    found = _find_entry(root, entry_id)
    if found is None:
        return {"error": f"no entry with id '{entry_id}'"}
    path, fm, _body = found
    if fm.get("type") not in pc.CORE_TYPES:
        return {"error": f"'{entry_id}' is type '{fm.get('type')}' -- kb mv only handles core "
                          f"content ({', '.join(pc.CORE_TYPES)}); inbox items use "
                          f"`kb inbox <id> promote`"}

    with pc.entry_lock(path):
        fm, body = pc.read_entry(path)
        target_type = new_type or fm["type"]
        target_title = new_title or fm["title"]
        if target_type == fm["type"] and target_title == fm["title"]:
            return {"path": os.path.relpath(path, root)}
        fm["type"] = target_type
        fm["title"] = target_title
        fm["updated"] = pc.now_iso()
        new_path = os.path.join(root, pc.TYPE_DIR[target_type], f"{entry_id}-{pc.slugify(target_title)}.md")
        pc.write_entry(new_path, fm, body)
        if new_path != path:
            os.remove(path)
    _reindex_fast(root)
    pc.git_autocommit(root, f"mv: {entry_id} -> {target_type}")
    return {"path": os.path.relpath(new_path, root)}


def inbox_list(root):
    """[{"id", "name", "title", "age_days", "created"}, ...] for everything in
    inbox/, sorted by filename -- the data behind `kb inbox`'s plain listing.
    Kept separate from cmd_inbox's own listing loop (which feeds items as bare
    tuples into the fzf interactive picker) so this can return a clean,
    JSON-friendly shape for kb web's /api/inbox without disturbing that path."""
    inbox_dir = os.path.join(root, "inbox")
    now = datetime.now().astimezone()
    files = sorted(f for f in os.listdir(inbox_dir) if f.endswith(".md")) if os.path.isdir(inbox_dir) else []
    items = []
    for name in files:
        path = os.path.join(inbox_dir, name)
        try:
            fm, _ = pc.read_entry(path)
            created, title, entry_id = fm.get("created"), fm.get("title") or name, fm.get("id")
        except ValueError:
            created, title, entry_id = None, name, None
        created_dt = pc.parse_iso(created)
        age_days = (now - created_dt).days if created_dt else None
        items.append({"id": entry_id, "name": name, "title": title, "age_days": age_days, "created": created})
    return items


def _iter_all_entries(root):
    """[{id, title, type, path}] for every entry in the repo (core + journal/
    inbox/sources) -- shared by the tag/link pickers below, which need to offer
    any entry as a target, not just core Diataxis content."""
    entries = []
    for path in pc.iter_markdown_files(root):
        try:
            fm, _ = pc.read_entry(path)
        except ValueError:
            continue
        if fm.get("id"):
            entries.append({"id": fm["id"], "title": fm.get("title") or "",
                             "type": fm.get("type") or "", "path": path})
    entries.sort(key=lambda e: e["id"], reverse=True)
    return entries


def _all_tags(root):
    """Sorted set of every tag currently used anywhere in the repo -- offered as
    the pick list when adding a tag interactively, alongside the option to type
    a new one."""
    tags = set()
    for path in pc.iter_markdown_files(root):
        try:
            fm, _ = pc.read_entry(path)
        except ValueError:
            continue
        tags.update(fm.get("tags") or [])
    return sorted(tags)
