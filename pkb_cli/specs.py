"""Specs and decision records (ADRs): plain markdown with a small frontmatter, stored under
`docs/specs/` and `docs/decisions/` in a code project so they survive sessions.

Everything operates on an explicit project `root` and returns plain dicts (`{"error": ...}` on
failure); nothing here prints or exits. `kb spec` / `kb adr` render the results (see project.py).
"""
import json
import os
import re
import tempfile

from . import pkb_common as pc

DOCS_DIR = "docs"

SPEC_TEMPLATE = """# {title}

## Problem

What is wrong or missing today, and for whom? Link the explanation/ page if one exists.

## Goals

-

## Non-goals

-

## Requirements

Each requirement is testable. State how you will know it is met.

- R1:
  - Accept:

## Design

The approach, and what you rejected. Record big choices as ADRs (`kb adr new`).

## Tasks

Small, ordered, each finishable in one sitting. `kb spec task <id> T1` ticks one off; ids (T1, T2, ...) are assigned on first use and never change.

- [ ]

## Open questions

-
"""

ADR_TEMPLATE = """# {title}

## Context

What forces are at play? What is the decision about?

## Decision

What we will do.

## Consequences

What gets easier, what gets harder, what we now owe.

## Alternatives considered

-
"""

# --------------------------------------------------------------------------- tasks

_TAG_RE = re.compile(r"^[\w.-]+$")
_TASK_RE = re.compile(r"^(\s*)- \[([ xX])\] ?(.*)$")
_ID_RE = re.compile(r"^(T\d+):\s*(.*)$")
_BD_RE = re.compile(r"\s*\[bd:([^\]\s]+)\]\s*$")


def _parse_tasks(body):
    """Checkbox tasks under '## Tasks' (every checkbox if there is no such heading), as dicts
    {idx, indent, done, id, text, bd}. Tasks without a `T<n>:` prefix get the next free id in
    order, so ids are stable once written and deterministic before they are."""
    lines = body.split("\n")
    has_heading = any(l.strip().lower() == "## tasks" for l in lines)
    in_tasks, tasks = False, []
    for i, line in enumerate(lines):
        if line.startswith("## "):
            in_tasks = line.strip().lower() == "## tasks"
        m = _TASK_RE.match(line)
        if not m or not (in_tasks or not has_heading):
            continue
        text, bd = m.group(3).strip(), None
        b = _BD_RE.search(text)
        if b:
            bd, text = b.group(1), text[:b.start()].strip()
        im = _ID_RE.match(text)
        tid, text = (im.group(1), im.group(2).strip()) if im else (None, text)
        if text:
            tasks.append({"idx": i, "indent": m.group(1), "done": m.group(2) != " ",
                          "id": tid, "text": text, "bd": bd})
    used = {int(t["id"][1:]) for t in tasks if t["id"]}
    nxt = max(used, default=0) + 1
    for t in tasks:
        if not t["id"]:
            t["id"], nxt = f"T{nxt}", nxt + 1
    return tasks


def _render_tasks(body, tasks):
    """Rewrite each task line from its dict (writes ids and bd links back into the body)."""
    lines = body.split("\n")
    for t in tasks:
        lines[t["idx"]] = (f"{t['indent']}- [{'x' if t['done'] else ' '}] {t['id']}: {t['text']}"
                           + (f" [bd:{t['bd']}]" if t["bd"] else ""))
    return "\n".join(lines)


def task_progress(body):
    tasks = _parse_tasks(body)
    return sum(1 for t in tasks if t["done"]), len(tasks)


def next_task(body):
    for t in _parse_tasks(body):
        if not t["done"]:
            return t
    return None


def _locked(path):
    """Serialise read-modify-write on one spec/ADR without leaving lock files in the project."""
    return pc.entry_lock(path, lock_dir=tempfile.gettempdir())


def _pin_task_ids(body):
    """on_write for specs: write generated `T<n>:` ids (and bd links) back into the file."""
    return _render_tasks(body, _parse_tasks(body))


def _spec_gate(doc, status):
    """Lifecycle gate for specs: a problem string, or None when the move is allowed."""
    if status in ("approved", "implementing"):
        problems = spec_lint(doc)
        if problems:
            return "spec is not ready: " + "; ".join(problems)
    if status == "done":
        done, total = task_progress(doc["body"])
        if done < total:
            return f"{total - done} task(s) still unchecked"
    return None


# Per-kind behaviour lives here, so the generic doc functions below never branch on the kind:
#   flow: statuses that must be walked in order (empty: any move is fine)
#   gate(doc, new_status) -> problem or None;   on_write(body) -> body
KINDS = {
    "spec": {
        "dir": "specs",
        "statuses": ("draft", "approved", "implementing", "done", "superseded", "abandoned"),
        "flow": ("draft", "approved", "implementing", "done"),
        "template": SPEC_TEMPLATE,
        "gate": _spec_gate,
        "on_write": _pin_task_ids,
    },
    "adr": {
        "dir": "decisions",
        "statuses": ("proposed", "accepted", "deprecated", "superseded"),
        "flow": (),
        "template": ADR_TEMPLATE,
        "gate": None,
        "on_write": None,
    },
}

_NUMBERED_RE = re.compile(r"^\d{4}-")


def kind_dir(root, kind):
    return os.path.join(root, DOCS_DIR, KINDS[kind]["dir"])


def _write(path, fm, body):
    pc.write_entry(path, fm, body, fields=list(fm))


def load_doc(path):
    try:
        fm, body = pc.read_entry(path)
    except (ValueError, OSError):
        return None
    return {"id": str(fm.get("id") or ""), "title": fm.get("title") or "",
            "status": fm.get("status") or "", "path": path, "fm": fm, "body": body}


def _paths(root, kind):
    """Sorted paths of the NNNN-*.md files for a kind, without opening any of them."""
    d = kind_dir(root, kind)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".md") and _NUMBERED_RE.match(n)]


def _next_number(root, kind):
    return max((int(os.path.basename(p)[:4]) for p in _paths(root, kind)), default=0) + 1


def _load(paths):
    return [d for d in (load_doc(p) for p in paths) if d and d["id"]]


def all_docs(root, kind):
    return _load(_paths(root, kind))


def summary(root, doc):
    out = {"id": doc["id"], "title": doc["title"], "status": doc["status"],
           "path": os.path.relpath(doc["path"], root)}
    if "- [" in doc["body"]:
        done, total = task_progress(doc["body"])
        out["tasks"] = {"done": done, "total": total}
    return out


def find_doc(root, kind, ref):
    """Resolve '3', '0003', or a unique slug fragment to a doc, or (None, error). Matches on the
    file name first, so only the candidate files are opened."""
    ref = str(ref).strip()
    if ref.isdigit():
        wanted = ref.zfill(4) + "-"
        paths = [p for p in _paths(root, kind) if os.path.basename(p).startswith(wanted)]
    else:
        paths = [p for p in _paths(root, kind) if ref.lower() in os.path.basename(p).lower()]
    hits = _load(paths)
    if not hits:
        return None, f"no {kind} matches '{ref}'"
    if len(hits) > 1:
        return None, (f"'{ref}' is ambiguous: " + ", ".join(os.path.basename(d["path"]) for d in hits)
                      + " (use a longer slug fragment" + (", or `renumber` to fix duplicate ids)"
                                                           if len({d["id"] for d in hits}) == 1 else ")"))
    return hits[0], None


# --------------------------------------------------------------------------- spec / adr

def doc_new(root, kind, title, tags=None):
    title = (title or "").strip()
    if not title:
        return {"error": "title is empty"}
    tags = [t.strip() for t in (tags or []) if t.strip()]
    bad = [t for t in tags if not _TAG_RE.match(t)]
    if bad:
        return {"error": f"invalid tag(s): {', '.join(bad)} (letters, digits, '.', '_' and '-' only)"}
    d = kind_dir(root, kind)
    os.makedirs(d, exist_ok=True)
    n = _next_number(root, kind)
    while True:
        doc_id = f"{n:04d}"
        path = os.path.join(d, f"{doc_id}-{pc.slugify(title)}.md")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            n += 1
    now = pc.now_iso()
    fm = {"id": doc_id, "title": title, "status": KINDS[kind]["statuses"][0],
          "created": now, "updated": now, "tags": tags or []}
    _write(path, fm, KINDS[kind]["template"].format(title=title))
    return {"id": doc_id, "title": title, "status": fm["status"], "path": os.path.relpath(path, root)}


def duplicate_ids(root, kind, docs=None):
    """{id: [relative paths]} for ids used by more than one file, e.g. after merging two
    branches that each created the next spec. Pass `docs` to reuse an already-parsed list."""
    seen = {}
    for d in (all_docs(root, kind) if docs is None else docs):
        seen.setdefault(d["id"], []).append(os.path.relpath(d["path"], root))
    return {i: paths for i, paths in seen.items() if len(paths) > 1}


def doc_renumber(root, kind, ref):
    """Give one doc (resolved by a unique slug fragment) the next free id, renaming its file.
    References to the old id elsewhere are not rewritten; the result lists the old id."""
    doc, err = find_doc(root, kind, ref)
    if err:
        return {"error": err}
    with _locked(doc["path"]):
        doc = load_doc(doc["path"])
        new_id = f"{_next_number(root, kind):04d}"
        old_id = doc["id"]
        base = os.path.basename(doc["path"])
        new_path = os.path.join(os.path.dirname(doc["path"]), new_id + base[len(old_id):])
        if os.path.exists(new_path):
            return {"error": f"{os.path.relpath(new_path, root)} already exists"}
        doc["fm"]["id"] = new_id
        doc["fm"]["updated"] = pc.now_iso()
        _write(doc["path"], doc["fm"], doc["body"])
        os.rename(doc["path"], new_path)
    return {"id": new_id, "previous": old_id, "path": os.path.relpath(new_path, root)}


def doc_list(root, kind, status=None):
    docs = all_docs(root, kind)
    if status:
        docs = [d for d in docs if d["status"] == status]
    return [summary(root, d) for d in docs]


def spec_lint(doc):
    """Problems that should block approval or implementation."""
    body, problems = doc["body"], []
    reqs = re.search(r"^## Requirements\n(.*?)(?=^## |\Z)", body, re.M | re.S)
    if not reqs or not re.search(r"R\d+:[ \t]*\S", reqs.group(1)):
        problems.append("Requirements has no filled-in 'R<n>: ...' item")
    elif not re.search(r"Accept:[ \t]*\S", reqs.group(1)):
        problems.append("no requirement has an 'Accept:' criterion")
    if not _parse_tasks(body):
        problems.append("Tasks has no checkbox items")
    return problems


def doc_set_status(root, kind, ref, status, by=None, force=False):
    doc, err = find_doc(root, kind, ref)
    if err:
        return {"error": err}
    with _locked(doc["path"]):
        doc = load_doc(doc["path"])  # reread under the lock
        info = KINDS[kind]
        if status not in info["statuses"]:
            return {"error": f"invalid {kind} status '{status}' (choose: {', '.join(info['statuses'])})"}
        old = doc["status"]
        if not force and old != status:
            flow = info["flow"]
            if old in flow and status in flow and flow.index(status) > flow.index(old) + 1:
                return {"error": f"cannot go {old} -> {status}; next step is '{flow[flow.index(old) + 1]}' "
                                 "(--force to override)"}
            problem = info["gate"](doc, status) if info["gate"] else None
            if problem:
                return {"error": f"{problem} (--force to override)"}
        if status == "superseded" and by:
            target, err = find_doc(root, kind, by)
            if err:
                return {"error": err}
            doc["fm"]["superseded_by"] = target["id"]
        doc["fm"]["status"] = status
        doc["fm"]["updated"] = pc.now_iso()
        body = info["on_write"](doc["body"]) if info["on_write"] else doc["body"]
        _write(doc["path"], doc["fm"], body)
    return {**summary(root, {**doc, "body": body, "status": status}), "previous": old}


def spec_task(root, ref, task, undo=False):
    """Tick (or untick) a task by id ('T3' or '3'). Ticking closes its linked bd issue, best effort."""
    doc, err = find_doc(root, "spec", ref)
    if err:
        return {"error": err}
    with _locked(doc["path"]):
        doc = load_doc(doc["path"])
        tasks = _parse_tasks(doc["body"])
        tid = str(task).strip().upper()
        tid = tid if tid.startswith("T") else f"T{tid}"
        hit = next((t for t in tasks if t["id"] == tid), None)
        if not hit:
            return {"error": f"no task {tid} in spec {doc['id']} (have: {', '.join(t['id'] for t in tasks) or 'none'})"}
        hit["done"] = not undo
        doc["fm"]["updated"] = pc.now_iso()
        body = _render_tasks(doc["body"], tasks)
        _write(doc["path"], doc["fm"], body)
    out = {"id": doc["id"], "task": tid, "text": hit["text"], "done": hit["done"],
           "progress": dict(zip(("done", "total"), task_progress(body)))}
    if hit["bd"]:
        out["bd"] = hit["bd"]
        out["bd_synced"] = _bd_sync(root, hit["bd"], reopen=undo)
    return out


def _bd_sync(root, bd_id, reopen=False):
    from . import todos
    try:
        todos.bd_action(root, "reopen" if reopen else "close", bd_id)
        return True
    except (RuntimeError, OSError):
        return False


def _bd_id(text):
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if isinstance(obj, list):
        obj = obj[0] if obj else {}
    return obj.get("id") if isinstance(obj, dict) else None


def spec_todos(root, ref):
    """Create a bd issue for every open task without one and record it as `[bd:<id>]` on the task."""
    from . import todos
    doc, err = find_doc(root, "spec", ref)
    if err:
        return {"error": err}
    if not todos.bd_available():
        return {"error": "bd (beads) is not installed"}
    created = []
    with _locked(doc["path"]):
        doc = load_doc(doc["path"])
        tasks = _parse_tasks(doc["body"])
        failure = None
        for t in tasks:
            if t["done"] or t["bd"]:
                continue
            try:
                bd_id = _bd_id(todos.bd_create(
                    root, f"[{doc['id']}/{t['id']}] {t['text']}",
                    description=f"{os.path.relpath(doc['path'], root)} task {t['id']}",
                    labels=f"spec-{doc['id']}", as_json=True))
            except RuntimeError as e:
                failure = str(e)
                break
            if not bd_id:
                failure = "bd did not return an issue id"
                break
            t["bd"] = bd_id
            created.append({"task": t["id"], "bd": bd_id})
        if created:
            doc["fm"]["updated"] = pc.now_iso()
            _write(doc["path"], doc["fm"], _render_tasks(doc["body"], tasks))
    if failure:
        return {"error": f"{failure} ({len(created)} issue(s) created before the failure)", "created": created}
    return {"id": doc["id"], "created": created}


def spec_resume(root):
    """What a fresh session should pick up: implementing specs first, then approved ones."""
    rank = {"implementing": 0, "approved": 1}
    active = sorted((d for d in all_docs(root, "spec") if d["status"] in rank), key=lambda d: rank[d["status"]])
    out = []
    for doc in active:
        nt = next_task(doc["body"])
        out.append({**summary(root, doc), "next_task": nt and {"id": nt["id"], "text": nt["text"]}})
    return out


def doc_show(root, kind, ref):
    doc, err = find_doc(root, kind, ref)
    if err:
        return {"error": err}
    with open(doc["path"], encoding="utf-8") as f:
        return {**summary(root, doc), "content": f.read()}
