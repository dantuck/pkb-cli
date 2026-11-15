"""Project-level Diataxis docs, specs, decision records, and agent instruction files.

Everything here operates on a *project* (a code repo), not the personal data repo:
`docs/{tutorials,how-to,reference,explanation}` plus `docs/specs/` and `docs/decisions/`,
and the `AGENTS.md` / `CLAUDE.md` files agents read. Specs and ADRs are plain markdown
with a small frontmatter, committed with the project, so they survive sessions.

Functions return plain dicts (`{"error": ...}` on failure) and never print or exit;
the `cmd_*` handlers at the bottom render them for the CLI.
"""
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile

from . import pkb_common as pc

DOCS_DIR = "docs"
DIATAXIS_DIRS = ("tutorials", "how-to", "reference", "explanation")

KINDS = {
    "spec": {
        "dir": "specs",
        "statuses": ("draft", "approved", "implementing", "done", "superseded", "abandoned"),
        "flow": ("draft", "approved", "implementing", "done"),
        "initial": "draft",
    },
    "adr": {
        "dir": "decisions",
        "statuses": ("proposed", "accepted", "deprecated", "superseded"),
        "flow": (),
        "initial": "proposed",
    },
}

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

DOCS_READMES = {
    "specs": "# Specs\n\nOne file per change, written before the code. Lifecycle: draft, approved, "
             "implementing, done. Manage with `kb spec`; start a session with `kb spec resume`.\n",
    "decisions": "# Decisions\n\nArchitecture decision records: one file per significant choice, kept "
                 "after it is made. Manage with `kb adr`.\n",
}

MARK_BEGIN = "<!-- kb:begin (managed by `kb agents init`; edit outside this block) -->"
MARK_END = "<!-- kb:end -->"

AGENTS_BLOCK = """{begin}
## Docs and specs (Diataxis + spec-driven)

Project docs live in `docs/`, organised by [Diataxis](https://diataxis.fr/):
`tutorials/` (learning), `how-to/` (tasks), `reference/` (facts), `explanation/` (why).
Put new docs in the quadrant that matches the reader's need; do not mix them.

- Specs: `docs/specs/NNNN-slug.md`. Decisions: `docs/decisions/NNNN-slug.md`.
- **Start of every session:** run `kb spec resume` and continue the active spec.
- **Before non-trivial work:** find a spec (`kb spec list`) or write one (`kb spec new "<title>"`).
  Get it approved by the user before implementing. Never start from an unapproved spec.
- Move it forward with `kb spec status <id> approved|implementing|done`. Tick finished work
  with `kb spec task <id> <T-id>` as you go, not at the end. `kb spec todos <id>` mirrors open
  tasks into bd issues (ticking a task closes its issue).
- Record a significant choice with `kb adr new "<title>"`, and link it from the spec.
- Change behaviour: update the spec first, then the code. Update `reference/` and `how-to/`
  docs in the same change. `kb docs search "<q>"` finds existing pages, `kb docs new <type> "<title>"`
  adds one in the right quadrant, `kb docs check` flags misfiled or unlinked pages.
- Add `--json` to `kb` commands and parse the result.
{end}
"""

HOOK_COMMAND = "command -v kb >/dev/null 2>&1 && kb spec resume --quiet || true"

CLAUDE_STUB = "@AGENTS.md\n\n## Claude Code\n\n"


# --------------------------------------------------------------------------- helpers

def find_project_root(start=None):
    """Nearest ancestor holding .git, else one holding docs/specs, AGENTS.md or CLAUDE.md,
    else the start directory itself."""
    start = os.path.abspath(start or os.getcwd())
    path = start
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    path = start
    while True:
        if any(os.path.exists(os.path.join(path, p))
               for p in ("docs/specs", "AGENTS.md", "CLAUDE.md")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return start
        path = parent


def _dir(root, kind):
    return os.path.join(root, DOCS_DIR, KINDS[kind]["dir"])


def _dump_value(value):
    if value is None:
        return "null"
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in value) + "]"
    s = str(value)
    if s == "" or any(c in s for c in ':#[]{}"') or s != s.strip():
        return json.dumps(s)
    return s


def _dump_fm(fm):
    return "---\n" + "".join(f"{k}: {_dump_value(v)}\n" for k, v in fm.items()) + "---\n"


def _write(path, fm, body):
    with open(path, "w", encoding="utf-8") as f:
        f.write(_dump_fm(fm) + "\n" + body.lstrip("\n"))


def _doc(path):
    try:
        fm, body = pc.read_entry(path)
    except (ValueError, OSError):
        return None
    return {"id": str(fm.get("id") or ""), "title": fm.get("title") or "",
            "status": fm.get("status") or "", "path": path, "fm": fm, "body": body}


def _all(root, kind):
    d = _dir(root, kind)
    if not os.path.isdir(d):
        return []
    docs = []
    for name in sorted(os.listdir(d)):
        if name.endswith(".md") and re.match(r"^\d{4}-", name):
            doc = _doc(os.path.join(d, name))
            if doc and doc["id"]:
                docs.append(doc)
    return docs


def _summary(root, doc):
    out = {"id": doc["id"], "title": doc["title"], "status": doc["status"],
           "path": os.path.relpath(doc["path"], root)}
    if "- [" in doc["body"]:
        done, total = task_progress(doc["body"])
        out["tasks"] = {"done": done, "total": total}
    return out


def find_doc(root, kind, ref):
    """Resolve '3', '0003', or a unique slug fragment to a doc, or (None, error)."""
    docs = _all(root, kind)
    ref = str(ref).strip()
    if ref.isdigit():
        hits = [d for d in docs if d["id"] == ref.zfill(4)]
    else:
        hits = [d for d in docs if ref.lower() in os.path.basename(d["path"]).lower()]
    if not hits:
        return None, f"no {kind} matches '{ref}'"
    if len(hits) > 1:
        return None, (f"'{ref}' is ambiguous: " + ", ".join(os.path.basename(d["path"]) for d in hits)
                      + " (use a longer slug fragment" + (", or `renumber` to fix duplicate ids)"
                                                           if len({d["id"] for d in hits}) == 1 else ")"))
    return hits[0], None


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


def _task_ref(ref):
    ref = str(ref).strip().upper()
    return ref if ref.startswith("T") else f"T{ref}"


def task_progress(body):
    tasks = _parse_tasks(body)
    return sum(1 for t in tasks if t["done"]), len(tasks)


def next_task(body):
    for t in _parse_tasks(body):
        if not t["done"]:
            return t
    return None


@contextlib.contextmanager
def _locked(path):
    """Serialise read-modify-write on one spec/ADR without leaving lock files in the project."""
    name = "kb-project-" + hashlib.sha1(os.path.abspath(path).encode()).hexdigest() + ".lock"
    fd = os.open(os.path.join(tempfile.gettempdir(), name), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --------------------------------------------------------------------------- scaffold

def project_init(root):
    """Create the Diataxis docs tree, specs/ and decisions/. Never overwrites."""
    created = []

    def touch(rel, content=""):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        created.append(rel)

    for d in DIATAXIS_DIRS:
        touch(f"{DOCS_DIR}/{d}/.gitkeep")
    for sub, text in DOCS_READMES.items():
        touch(f"{DOCS_DIR}/{sub}/README.md", text)
    touch(f"{DOCS_DIR}/README.md",
          "# Documentation\n\nOrganised by [Diataxis](https://diataxis.fr/):\n\n"
          "- [tutorials/](tutorials/): learning-oriented lessons\n"
          "- [how-to/](how-to/): goal-oriented task guides\n"
          "- [reference/](reference/): facts to look up\n"
          "- [explanation/](explanation/): background and rationale\n"
          "- [specs/](specs/): what we are building next, and its progress\n"
          "- [decisions/](decisions/): why we chose what we chose\n")
    return {"root": root, "created": created}


def project_status(root):
    docs_dir = os.path.join(root, DOCS_DIR)
    quadrants = {}
    for d in DIATAXIS_DIRS:
        p = os.path.join(docs_dir, d)
        quadrants[d] = (sum(1 for n in os.listdir(p) if n.endswith(".md")) if os.path.isdir(p) else None)
    by_status = {}
    for doc in _all(root, "spec"):
        by_status[doc["status"]] = by_status.get(doc["status"], 0) + 1
    return {"root": root, "quadrants": quadrants, "specs": by_status,
            "decisions": len(_all(root, "adr")), "agents": agents_check(root),
            "duplicate_ids": {k: duplicate_ids(root, k) for k in KINDS if duplicate_ids(root, k)}}


# --------------------------------------------------------------------------- spec / adr

def doc_new(root, kind, title, tags=None):
    title = (title or "").strip()
    if not title:
        return {"error": "title is empty"}
    tags = [t.strip() for t in (tags or []) if t.strip()]
    bad = [t for t in tags if not _TAG_RE.match(t)]
    if bad:
        return {"error": f"invalid tag(s): {', '.join(bad)} (letters, digits, '.', '_' and '-' only)"}
    d = _dir(root, kind)
    os.makedirs(d, exist_ok=True)
    nums = [int(x["id"]) for x in _all(root, kind) if x["id"].isdigit()]
    n = max(nums, default=0) + 1
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
    fm = {"id": doc_id, "title": title, "status": KINDS[kind]["initial"],
          "created": now, "updated": now, "tags": tags or []}
    template = SPEC_TEMPLATE if kind == "spec" else ADR_TEMPLATE
    _write(path, fm, template.format(title=title))
    return {"id": doc_id, "title": title, "status": fm["status"], "path": os.path.relpath(path, root)}


def duplicate_ids(root, kind):
    """{id: [relative paths]} for ids used by more than one file, e.g. after merging two
    branches that each created the next spec."""
    seen = {}
    for d in _all(root, kind):
        seen.setdefault(d["id"], []).append(os.path.relpath(d["path"], root))
    return {i: paths for i, paths in seen.items() if len(paths) > 1}


def doc_renumber(root, kind, ref):
    """Give one doc (resolved by a unique slug fragment) the next free id, renaming its file.
    References to the old id elsewhere are not rewritten; the result lists the old id."""
    doc, err = find_doc(root, kind, ref)
    if err:
        return {"error": err}
    with _locked(doc["path"]):
        doc = _doc(doc["path"])
        new_id = f"{max((int(d['id']) for d in _all(root, kind) if d['id'].isdigit()), default=0) + 1:04d}"
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
    docs = _all(root, kind)
    if status:
        docs = [d for d in docs if d["status"] == status]
    return [_summary(root, d) for d in docs]


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
        doc = _doc(doc["path"])  # reread under the lock
        info = KINDS[kind]
        if status not in info["statuses"]:
            return {"error": f"invalid {kind} status '{status}' (choose: {', '.join(info['statuses'])})"}
        old = doc["status"]
        if not force and kind == "spec" and old != status:
            flow = info["flow"]
            if old in flow and status in flow and flow.index(status) > flow.index(old) + 1:
                return {"error": f"cannot go {old} -> {status}; next step is '{flow[flow.index(old) + 1]}' "
                                 "(--force to override)"}
            if status in ("approved", "implementing"):
                problems = spec_lint(doc)
                if problems:
                    return {"error": "spec is not ready: " + "; ".join(problems) + " (--force to override)"}
            if status == "done":
                done, total = task_progress(doc["body"])
                if done < total:
                    return {"error": f"{total - done} task(s) still unchecked (--force to override)"}
        if status == "superseded" and by:
            target, err = find_doc(root, kind, by)
            if err:
                return {"error": err}
            doc["fm"]["superseded_by"] = target["id"]
        doc["fm"]["status"] = status
        doc["fm"]["updated"] = pc.now_iso()
        body = doc["body"]
        if kind == "spec":
            body = _render_tasks(body, _parse_tasks(body))  # pin task ids
        _write(doc["path"], doc["fm"], body)
    return {**_summary(root, {**doc, "body": body, "status": status}), "previous": old}


def spec_task(root, ref, task, undo=False):
    """Tick (or untick) a task by id ('T3' or '3'). Ticking closes its linked bd issue, best effort."""
    doc, err = find_doc(root, "spec", ref)
    if err:
        return {"error": err}
    with _locked(doc["path"]):
        doc = _doc(doc["path"])
        tasks = _parse_tasks(doc["body"])
        tid = _task_ref(task)
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
        doc = _doc(doc["path"])
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
    active = []
    for status in ("implementing", "approved"):
        for doc in _all(root, "spec"):
            if doc["status"] == status:
                nt = next_task(doc["body"])
                active.append({**_summary(root, doc), "next_task": nt and {"id": nt["id"], "text": nt["text"]}})
    return active


def doc_show(root, kind, ref):
    doc, err = find_doc(root, kind, ref)
    if err:
        return {"error": err}
    with open(doc["path"], encoding="utf-8") as f:
        return {**_summary(root, doc), "content": f.read()}


# --------------------------------------------------------------------------- agents files

def _has_import(text):
    return re.search(r"^@AGENTS\.md\s*$", text, re.M) is not None


def _block():
    return AGENTS_BLOCK.format(begin=MARK_BEGIN, end=MARK_END)


def _settings_path(root):
    return os.path.join(root, ".claude", "settings.json")


def _hook_installed(root):
    try:
        with open(_settings_path(root), encoding="utf-8") as f:
            data = json.load(f)
        return any("kb spec resume" in h.get("command", "")
                   for entry in data.get("hooks", {}).get("SessionStart", [])
                   for h in entry.get("hooks", []))
    except (OSError, ValueError, AttributeError):
        return False


def install_hook(root):
    """Merge a SessionStart hook running `kb spec resume` into .claude/settings.json (shared,
    committed project settings). Returns (changed, message); never overwrites invalid JSON."""
    path = _settings_path(root)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            return False, ".claude/settings.json is not valid JSON; add the SessionStart hook by hand"
        if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
            return False, ".claude/settings.json has an unexpected shape; add the SessionStart hook by hand"
    if _hook_installed(root):
        upgraded = False
        for entry in data["hooks"]["SessionStart"]:
            for h in entry.get("hooks", []):
                if "kb spec resume" in h.get("command", "") and "--quiet" not in h["command"]:
                    h["command"] = HOOK_COMMAND
                    upgraded = True
        if not upgraded:
            return False, None
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return True, "updated the SessionStart hook to `kb spec resume --quiet`"
    data.setdefault("hooks", {}).setdefault("SessionStart", []).append(
        {"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return True, "added SessionStart hook (kb spec resume) to .claude/settings.json"


GUARD_RULES = ("Bash(kb spec status * approved*)", "Bash(kb spec * --force*)")


def install_guard(root):
    """Merge `permissions.ask` rules so Claude Code asks the user before an agent approves a spec
    or bypasses a gate with --force. Returns (changed, message). Matches the command text as
    written, so it is a prompt for the normal path, not a sandbox."""
    path = _settings_path(root)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            return False, ".claude/settings.json is not valid JSON; add the permission rules by hand"
        if not isinstance(data, dict) or not isinstance(data.get("permissions", {}), dict) \
                or not isinstance(data.get("permissions", {}).get("ask", []), list):
            return False, ".claude/settings.json has an unexpected shape; add the permission rules by hand"
    ask = data.setdefault("permissions", {}).setdefault("ask", [])
    missing = [r for r in GUARD_RULES if r not in ask]
    if not missing:
        return False, None
    ask.extend(missing)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    return True, "added permission prompts for `kb spec ... approved` and `--force` to .claude/settings.json"


def _guard_installed(root):
    try:
        with open(_settings_path(root), encoding="utf-8") as f:
            ask = json.load(f).get("permissions", {}).get("ask", [])
        return all(r in ask for r in GUARD_RULES)
    except (OSError, ValueError, AttributeError):
        return False


def agents_check(root):
    """Read-only findings about AGENTS.md / CLAUDE.md, each with a suggestion."""
    agents, claude = os.path.join(root, "AGENTS.md"), os.path.join(root, "CLAUDE.md")
    has_a, has_c = os.path.exists(agents), os.path.lexists(claude)
    findings = []
    if not has_a:
        findings.append({"issue": "no AGENTS.md", "fix": "kb agents init"})
    else:
        with open(agents, encoding="utf-8") as f:
            text = f.read()
        if MARK_BEGIN not in text:
            findings.append({"issue": "AGENTS.md has no kb workflow block", "fix": "kb agents init"})
        elif _block().strip() not in text:
            findings.append({"issue": "kb workflow block in AGENTS.md is out of date",
                             "fix": "kb agents init"})
    if has_a and has_c and not os.path.islink(claude):
        with open(claude, encoding="utf-8") as f:
            if not _has_import(f.read()):
                findings.append({"issue": "CLAUDE.md does not import AGENTS.md, so the two can drift apart",
                                 "fix": "kb agents init --link"})
    if has_a and not has_c:
        findings.append({"issue": "no CLAUDE.md: recent Claude Code reads AGENTS.md as a fallback, older "
                                  "versions and some sessions do not",
                         "fix": "kb agents init"})
    if os.path.isdir(_dir(root, "spec")) and not _hook_installed(root):
        findings.append({"issue": "no SessionStart hook, so sessions only resume specs if the agent "
                                  "remembers to run `kb spec resume`",
                         "fix": "kb agents init --hook"})
    if os.path.isdir(_dir(root, "spec")) and not _guard_installed(root):
        findings.append({"issue": "an agent can approve its own spec; nothing asks you first",
                         "fix": "kb agents init --guard"})
    return {"agents_md": has_a, "claude_md": has_c, "findings": findings}


def agents_init(root, link=False, hook=False, guard=False):
    """Create/refresh AGENTS.md with the managed workflow block and make CLAUDE.md import it."""
    actions = []
    agents, claude = os.path.join(root, "AGENTS.md"), os.path.join(root, "CLAUDE.md")
    block = _block()
    if not os.path.exists(agents):
        with open(agents, "w", encoding="utf-8") as f:
            f.write(f"# {os.path.basename(root)}\n\nOne paragraph on what this project is, how to build and "
                    f"test it, and the conventions an agent can't infer from the code.\n\n{block}")
        actions.append("created AGENTS.md")
    else:
        with open(agents, encoding="utf-8") as f:
            text = f.read()
        b, e = text.find(MARK_BEGIN), text.find(MARK_END)
        if b != -1 and e > b:
            new = text[:b] + block.rstrip("\n") + text[e + len(MARK_END):]
        else:
            new = text.rstrip("\n") + "\n\n" + block
        if new != text:
            with open(agents, "w", encoding="utf-8") as f:
                f.write(new)
            actions.append("updated kb block in AGENTS.md")
    if not os.path.lexists(claude):
        with open(claude, "w", encoding="utf-8") as f:
            f.write(CLAUDE_STUB)
        actions.append("created CLAUDE.md importing @AGENTS.md")
    elif not os.path.islink(claude):
        with open(claude, encoding="utf-8") as f:
            text = f.read()
        if not _has_import(text):
            if link:
                with open(claude, "w", encoding="utf-8") as f:
                    f.write("@AGENTS.md\n\n" + text)
                actions.append("added @AGENTS.md import to CLAUDE.md")
            else:
                actions.append("CLAUDE.md exists without an @AGENTS.md import; rerun with --link to add one")
    if hook:
        changed, msg = install_hook(root)
        if msg:
            actions.append(msg)
    if guard:
        changed, msg = install_guard(root)
        if msg:
            actions.append(msg)
    return {"root": root, "actions": actions}


def doctor_lines(start=None):
    """([ok], [todo]) for `kb doctor`, only when the cwd's project actually uses kb's project
    workflow (docs/specs/ or a kb block in AGENTS.md); otherwise ([], []) so non-project use is
    never flagged."""
    root = find_project_root(start)
    agents = os.path.join(root, "AGENTS.md")
    uses_kb = os.path.isdir(_dir(root, "spec")) or (
        os.path.exists(agents) and MARK_BEGIN in open(agents, encoding="utf-8").read())
    if not uses_kb:
        return [], []
    ok, todo = [f"project {root} uses kb specs/AGENTS.md"], []
    todo += [f"{f['issue']} (fix: {f['fix']})" for f in agents_check(root)["findings"]]
    for kind in KINDS:
        todo += [f"duplicate {kind} id {i}: {', '.join(p)} (fix: kb {kind} renumber <slug>)"
                 for i, p in duplicate_ids(root, kind).items()]
    from . import projdocs
    todo += [f"{f['path']}: {f['issue']}" for f in projdocs.docs_check(root)]
    if not todo:
        ok.append("project docs, specs and agent files look consistent")
    return ok, todo


# --------------------------------------------------------------------------- CLI

def _emit(args, payload, text):
    print(json.dumps(payload) if getattr(args, "json", False) else text)


def _fail(args, message):
    if getattr(args, "json", False):
        print(json.dumps({"error": message}), file=sys.stderr)
    else:
        print(f"error: {message}", file=sys.stderr)
    return 1


def _root(args):
    return find_project_root(getattr(args, "dir", None))


def _nt(t):
    return f"{t['id']}: {t['text']}" if t else "(no open tasks)"


def _line(s):
    tasks = f"  [{s['tasks']['done']}/{s['tasks']['total']}]" if "tasks" in s else ""
    return f"{s['id']}  {s['status']:<12} {s['title']}{tasks}"


def cmd_project(args):
    root = _root(args)
    if args.action == "init":
        r = project_init(root)
        _emit(args, r, "\n".join(f"created {p}" for p in r["created"]) or "docs tree already in place")
        return 0
    r = project_status(root)
    lines = [f"project: {root}"]
    for q, n in r["quadrants"].items():
        lines.append(f"  docs/{q}: " + ("missing" if n is None else f"{n} page(s)"))
    lines.append("  specs: " + (", ".join(f"{n} {s}" for s, n in r["specs"].items()) or "none"))
    lines.append(f"  decisions: {r['decisions']}")
    lines += [f"  ! {f['issue']} -> {f['fix']}" for f in r["agents"]["findings"]]
    for kind, dups in r["duplicate_ids"].items():
        lines += [f"  ! duplicate {kind} id {i}: {', '.join(p)} -> kb {kind} renumber <slug>" for i, p in dups.items()]
    _emit(args, r, "\n".join(lines))
    return 0


def cmd_doc(args):
    kind, root = args.command, _root(args)
    if args.action == "new":
        r = doc_new(root, kind, args.title, args.tags.split(",") if args.tags else [])
        if "error" in r:
            return _fail(args, r["error"])
        _emit(args, r, r["path"])
    elif args.action == "list":
        r = doc_list(root, kind, args.status)
        _emit(args, r, "\n".join(_line(s) for s in r) or f"no {kind}s")
    elif args.action == "show":
        r = doc_show(root, kind, args.ref)
        if "error" in r:
            return _fail(args, r["error"])
        _emit(args, r, r["content"])
    elif args.action == "status":
        if not args.value:
            doc, err = find_doc(root, kind, args.ref)
            if err:
                return _fail(args, err)
            r = _summary(root, doc)
            _emit(args, r, _line(r))
            return 0
        r = doc_set_status(root, kind, args.ref, args.value, by=args.by, force=args.force)
        if "error" in r:
            return _fail(args, r["error"])
        _emit(args, r, f"{r['id']}: {r['previous']} -> {r['status']}")
    elif args.action == "task":
        r = spec_task(root, args.ref, args.value, undo=args.undo)
        if "error" in r:
            return _fail(args, r["error"])
        p = r["progress"]
        _emit(args, r, f"{'[x]' if r['done'] else '[ ]'} {r['task']}: {r['text']}  ({p['done']}/{p['total']})")
    elif args.action == "todos":
        r = spec_todos(root, args.ref)
        if "error" in r:
            return _fail(args, r["error"])
        _emit(args, r, "\n".join(f"{c['task']} -> bd {c['bd']}" for c in r["created"]) or "every open task already has a bd issue")
    elif args.action == "renumber":
        r = doc_renumber(root, kind, args.ref)
        if "error" in r:
            return _fail(args, r["error"])
        _emit(args, r, f"{r['previous']} -> {r['id']}: {r['path']} (update any references to {r['previous']})")
    elif args.action == "resume":
        r = spec_resume(root)
        if args.quiet and not r:
            return 0  # SessionStart hook: add nothing to the context when there is nothing to resume
        text = "\n\n".join(f"{_line(s)}\n  {s['path']}\n  next: {_nt(s['next_task'])}"
                           for s in r) or "no active specs; `kb spec new \"<title>\"` to start one"
        _emit(args, r, text)
    return 0


def cmd_agents(args):
    root = _root(args)
    if args.action == "init":
        r = agents_init(root, link=args.link, hook=args.hook, guard=args.guard)
        _emit(args, r, "\n".join(r["actions"]) or "already up to date")
        return 0
    r = agents_check(root)
    _emit(args, r, "\n".join(f"{f['issue']}\n  fix: {f['fix']}" for f in r["findings"]) or "ok")
    return 0


def add_parsers(sub):
    """Register project, spec, adr and agents on the top-level subparsers."""
    def common(p):
        p.add_argument("--dir", metavar="DIR", help="project directory (default: cwd's project root)")
        p.add_argument("--json", action="store_true", help="machine-readable output")

    p = sub.add_parser("project", help="scaffold and inspect a project's Diataxis docs",
                       description="Set up docs/{tutorials,how-to,reference,explanation,specs,decisions} "
                                   "in a code project, or report on them.")
    p.add_argument("action", choices=["init", "status"])
    common(p)
    p.set_defaults(func=cmd_project)

    for kind, noun in (("spec", "spec"), ("adr", "decision record")):
        statuses = KINDS[kind]["statuses"]
        p = sub.add_parser(kind, help=f"manage {noun}s in docs/{KINDS[kind]['dir']}/",
                           description=f"Create and track {noun}s. They are plain markdown committed with "
                                       "the project, so they survive sessions.",
                           epilog=f"statuses: {', '.join(statuses)}")
        p.add_argument("action", choices=["new", "list", "show", "status"] +
                       ["renumber"] + (["task", "resume", "todos"] if kind == "spec" else []))
        p.add_argument("ref", nargs="?", help="new: title; show/status/task: id (3, 0003) or slug fragment")
        p.add_argument("value", nargs="?", help="status: the new status; task: the task id (T3 or 3)")
        p.add_argument("--tags", metavar="TAG,TAG", help="new: comma-separated tags")
        p.add_argument("--status", help="list: only this status")
        p.add_argument("--by", metavar="ID", help="status superseded: the replacing doc")
        p.add_argument("--force", action="store_true", help="status: skip the lifecycle gates")
        p.add_argument("--undo", action="store_true", help="task: untick instead of tick")
        p.add_argument("--quiet", action="store_true", help="resume: print nothing when no spec is active")
        common(p)
        p.set_defaults(func=_dispatch_doc)

    p = sub.add_parser("agents", help="create and check AGENTS.md / CLAUDE.md for a project",
                       description="AGENTS.md holds shared agent instructions plus a managed kb workflow "
                                   "block; CLAUDE.md imports it with @AGENTS.md.")
    p.add_argument("action", choices=["init", "check"])
    p.add_argument("--link", action="store_true",
                   help="init: prepend @AGENTS.md to an existing CLAUDE.md that lacks it")
    p.add_argument("--hook", action="store_true",
                   help="init: add a Claude Code SessionStart hook running `kb spec resume` "
                        "to .claude/settings.json")
    p.add_argument("--guard", action="store_true",
                   help="init: add permissions.ask rules so Claude Code asks you before an agent "
                        "approves a spec or uses --force")
    common(p)
    p.set_defaults(func=cmd_agents)

    from . import projdocs
    projdocs.add_parser(sub, common)


def _dispatch_doc(args):
    """Normalise the positional layout per action, then hand off to cmd_doc."""
    if args.action == "new":
        args.title = args.ref
        if not args.title:
            return _fail(args, "new needs a title")
    elif args.action in ("show", "status", "task", "todos", "renumber") and not args.ref:
        return _fail(args, f"{args.action} needs an id")
    if args.action == "status":
        args.value = args.value or None
    if args.action == "task":
        if not args.value:
            return _fail(args, "task needs a task id (T3 or 3)")
    return cmd_doc(args)
