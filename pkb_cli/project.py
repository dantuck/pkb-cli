"""`kb project`, `kb spec`, `kb adr` and `kb agents`: scaffold a code project's Diataxis docs and
drive specs, decision records and agent files from the command line.

The logic lives in `specs.py` (specs/ADRs/tasks) and `agentfiles.py` (AGENTS.md, hook, guard);
this module holds the docs scaffold and the argparse/rendering layer shared with `projdocs.py`.
"""
import os
import sys

from . import agentfiles, specs
from . import pkb_common as pc
from .specs import DOCS_DIR

DIATAXIS_DIRS = ("tutorials", "how-to", "reference", "explanation")

DOCS_READMES = {
    "specs": "# Specs\n\nOne file per change, written before the code. Lifecycle: draft, approved, "
             "implementing, done. Manage with `kb spec`; start a session with `kb spec resume`.\n",
    "decisions": "# Decisions\n\nArchitecture decision records: one file per significant choice, kept "
                 "after it is made. Manage with `kb adr`.\n",
}

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
    docs = {k: specs.all_docs(root, k) for k in specs.KINDS}
    by_status = {}
    for doc in docs["spec"]:
        by_status[doc["status"]] = by_status.get(doc["status"], 0) + 1
    dups = {k: d for k in specs.KINDS if (d := specs.duplicate_ids(root, k, docs[k]))}
    return {"root": root, "quadrants": quadrants, "specs": by_status, "decisions": len(docs["adr"]),
            "agents": agentfiles.agents_check(root), "duplicate_ids": dups}


# --------------------------------------------------------------------------- CLI

def emit(args, payload, text):
    pc.emit_result(getattr(args, "json", False), payload, text)


def fail(args, message):
    return pc.report_error(message, getattr(args, "json", False))


def root_for(args):
    return find_project_root(getattr(args, "dir", None))


def _nt(t):
    return f"{t['id']}: {t['text']}" if t else "(no open tasks)"


def _line(s):
    tasks = f"  [{s['tasks']['done']}/{s['tasks']['total']}]" if "tasks" in s else ""
    return f"{s['id']}  {s['status']:<12} {s['title']}{tasks}"


def cmd_project(args):
    root = root_for(args)
    if args.action == "init":
        r = project_init(root)
        emit(args, r, "\n".join(f"created {p}" for p in r["created"]) or "docs tree already in place")
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
    emit(args, r, "\n".join(lines))
    return 0


def cmd_doc(args):
    kind, root = args.command, root_for(args)
    action = args.action
    if action == "new":
        r = specs.doc_new(root, kind, args.title, args.tags.split(",") if args.tags else [])
        text = r.get("path")
    elif action == "list":
        r = specs.doc_list(root, kind, args.status)
        text = "\n".join(_line(s) for s in r) or f"no {kind}s"
    elif action == "show":
        r = specs.doc_show(root, kind, args.ref)
        text = r.get("content")
    elif action == "status" and not args.new_status:
        doc, err = specs.find_doc(root, kind, args.ref)
        r = {"error": err} if err else specs.summary(root, doc)
        text = None if err else _line(r)
    elif action == "status":
        r = specs.doc_set_status(root, kind, args.ref, args.new_status, by=args.by, force=args.force)
        text = f"{r.get('id')}: {r.get('previous')} -> {r.get('status')}"
    elif action == "task":
        r = specs.spec_task(root, args.ref, args.task, undo=args.undo)
        p = r.get("progress")
        text = p and f"{'[x]' if r['done'] else '[ ]'} {r['task']}: {r['text']}  ({p['done']}/{p['total']})"
    elif action == "todos":
        r = specs.spec_todos(root, args.ref)
        text = "\n".join(f"{c['task']} -> bd {c['bd']}" for c in r.get("created", [])) or \
            "every open task already has a bd issue"
    elif action == "renumber":
        r = specs.doc_renumber(root, kind, args.ref)
        text = f"{r.get('previous')} -> {r.get('id')}: {r.get('path')} (update any references to {r.get('previous')})"
    else:  # resume
        r = specs.spec_resume(root)
        if args.quiet and not r:
            return 0  # SessionStart hook: add nothing to the context when there is nothing to resume
        text = "\n\n".join(f"{_line(s)}\n  {s['path']}\n  next: {_nt(s['next_task'])}"
                           for s in r) or "no active specs; `kb spec new \"<title>\"` to start one"
    if isinstance(r, dict) and "error" in r:
        return fail(args, r["error"])
    emit(args, r, text)
    return 0


def cmd_agents(args):
    root = root_for(args)
    if args.action == "init":
        r = agentfiles.agents_init(root, link=args.link, hook=args.hook, guard=args.guard)
        emit(args, r, "\n".join(r["actions"]) or "already up to date")
        return 0
    r = agentfiles.agents_check(root)
    emit(args, r, "\n".join(f"{f['issue']}\n  fix: {f['fix']}" for f in r["findings"]) or "ok")
    return 0


def add_common_args(p):
    p.add_argument("--dir", metavar="DIR", help="project directory (default: cwd's project root)")
    p.add_argument("--json", action="store_true", help="machine-readable output")


def show_help_for(parser):
    """Default handler for a command group typed without an action: print its help, exit 2."""
    def handler(args):
        parser.print_help(sys.stderr)
        return 2
    parser.set_defaults(func=handler)


def _leaf(actions, name, help_, *positionals, handler=cmd_doc):
    """One action parser (`kb spec status ...`) with its own named positionals and the common flags."""
    p = actions.add_parser(name, help=help_, description=help_)
    for dest, text, extra in positionals:
        p.add_argument(dest, help=text, **extra)
    add_common_args(p)
    p.set_defaults(func=handler)
    return p


def add_parsers(sub):
    """Register project, spec, adr and agents on the top-level subparsers."""
    p = sub.add_parser("project", help="scaffold and inspect a project's Diataxis docs",
                       description="Set up docs/{tutorials,how-to,reference,explanation,specs,decisions} "
                                   "in a code project, or report on them.")
    p.add_argument("action", choices=["init", "status"])
    add_common_args(p)
    p.set_defaults(func=cmd_project)

    ref = ("ref", "id (3 or 0003) or a unique slug fragment", {})
    for kind, noun in (("spec", "spec"), ("adr", "decision record")):
        info = specs.KINDS[kind]
        kp = sub.add_parser(kind, help=f"manage {noun}s in docs/{info['dir']}/",
                            description=f"Create and track {noun}s. They are plain markdown committed with "
                                        "the project, so they survive sessions.",
                            epilog=f"statuses: {', '.join(info['statuses'])}")
        actions = kp.add_subparsers(dest="action", metavar="<action>")
        show_help_for(kp)
        _leaf(actions, "new", f"create a {noun}", ("title", "title", {})).add_argument(
            "--tags", metavar="TAG,TAG", help="comma-separated tags")
        _leaf(actions, "list", f"list {noun}s").add_argument("--status", help="only this status")
        _leaf(actions, "show", f"print a {noun}", ref)
        sp = _leaf(actions, "status", f"show or change a {noun}'s status", ref,
                   ("new_status", "the new status (omit to just show it)", {"nargs": "?", "metavar": "status"}))
        sp.add_argument("--by", metavar="ID", help="with superseded: the replacing doc")
        sp.add_argument("--force", action="store_true", help="skip the lifecycle gates")
        _leaf(actions, "renumber", f"give a {noun} the next free id (after a merge duplicated one)", ref)
        if kind == "spec":
            _leaf(actions, "task", "tick (or untick) a task", ref,
                  ("task", "task id (T3 or 3)", {})).add_argument(
                "--undo", action="store_true", help="untick instead of tick")
            _leaf(actions, "resume", "active specs and their next open task").add_argument(
                "--quiet", action="store_true", help="print nothing when no spec is active")
            _leaf(actions, "todos", "create a bd issue for every open task", ref)

    ap = sub.add_parser("agents", help="create and check AGENTS.md / CLAUDE.md for a project",
                        description="AGENTS.md holds shared agent instructions plus a managed kb workflow "
                                    "block; CLAUDE.md imports it with @AGENTS.md.")
    actions = ap.add_subparsers(dest="action", metavar="<action>")
    show_help_for(ap)
    ip = _leaf(actions, "init", "create or refresh AGENTS.md and CLAUDE.md", handler=cmd_agents)
    ip.add_argument("--link", action="store_true", help="prepend @AGENTS.md to an existing CLAUDE.md that lacks it")
    ip.add_argument("--hook", action="store_true",
                    help="add a Claude Code SessionStart hook running `kb spec resume` to .claude/settings.json")
    ip.add_argument("--guard", action="store_true",
                    help="add permissions.ask rules so Claude Code asks you before an agent approves a spec "
                         "or uses --force")
    _leaf(actions, "check", "report AGENTS.md / CLAUDE.md problems (read-only)", handler=cmd_agents)
