"""AGENTS.md / CLAUDE.md for a project: a managed kb workflow block in AGENTS.md, a CLAUDE.md that
imports it, and the opt-in Claude Code settings (SessionStart hook, approval prompts).

Functions return plain dicts and never print or exit; `kb agents` renders them (see project.py).
"""
import json
import os
import re

from . import specs

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

IMPORT_LINE = "@AGENTS.md"
CLAUDE_STUB = f"{IMPORT_LINE}\n\n## Claude Code\n\n"


def _has_import(text):
    return re.search(rf"^{re.escape(IMPORT_LINE)}\s*$", text, re.M) is not None


def _block():
    return AGENTS_BLOCK.format(begin=MARK_BEGIN, end=MARK_END)


GUARD_RULES = ("Bash(kb spec status * approved*)", "Bash(kb spec * --force*)")


def _settings_path(root):
    return os.path.join(root, ".claude", "settings.json")


def _load_settings(root):
    """(data, error) for .claude/settings.json; ({}, None) when it does not exist yet."""
    path = _settings_path(root)
    if not os.path.exists(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError:
        return None, ".claude/settings.json is not valid JSON"
    if not isinstance(data, dict):
        return None, ".claude/settings.json has an unexpected shape"
    return data, None


def _save_settings(root, data):
    path = _settings_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _section(data, key, kind):
    """data[key] when it has the expected type (dict/list), else None; `{}`/`[]` if absent."""
    value = data.get(key, kind())
    return value if isinstance(value, kind) else None


def _resume_hooks(data):
    hooks = _section(data, "hooks", dict) or {}
    return [h for e in hooks.get("SessionStart", []) if isinstance(e, dict)
            for h in e.get("hooks", []) if isinstance(h, dict) and "kb spec resume" in h.get("command", "")]


def _hook_installed(data):
    return bool(_resume_hooks(data))


def _guard_installed(data):
    ask = (_section(data, "permissions", dict) or {}).get("ask", [])
    return all(r in ask for r in GUARD_RULES)


def install_hook(root):
    """Merge a SessionStart hook running `kb spec resume --quiet` into .claude/settings.json
    (shared, committed project settings), upgrading an older hook that lacks --quiet.
    Returns (changed, message); never overwrites invalid JSON."""
    data, err = _load_settings(root)
    if err or _section(data, "hooks", dict) is None:
        return False, f"{err or '.claude/settings.json has an unexpected shape'}; add the SessionStart hook by hand"
    existing = _resume_hooks(data)
    if not existing:
        data.setdefault("hooks", {}).setdefault("SessionStart", []).append(
            {"hooks": [{"type": "command", "command": HOOK_COMMAND}]})
        message = "added SessionStart hook (kb spec resume) to .claude/settings.json"
    else:
        stale = [h for h in existing if "--quiet" not in h["command"]]
        if not stale:
            return False, None
        for h in stale:
            h["command"] = HOOK_COMMAND
        message = "updated the SessionStart hook to `kb spec resume --quiet`"
    _save_settings(root, data)
    return True, message


def install_guard(root):
    """Merge `permissions.ask` rules so Claude Code asks the user before an agent approves a spec
    or bypasses a gate with --force. Returns (changed, message). Matches the command text as
    written, so it is a prompt for the normal path, not a sandbox."""
    data, err = _load_settings(root)
    perms = _section(data or {}, "permissions", dict)
    if err or perms is None or not isinstance(perms.get("ask", []), list):
        return False, f"{err or '.claude/settings.json has an unexpected shape'}; add the permission rules by hand"
    ask = data.setdefault("permissions", {}).setdefault("ask", [])
    missing = [r for r in GUARD_RULES if r not in ask]
    if not missing:
        return False, None
    ask.extend(missing)
    _save_settings(root, data)
    return True, "added permission prompts for `kb spec ... approved` and `--force` to .claude/settings.json"


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
    settings = _load_settings(root)[0] or {}
    if os.path.isdir(specs.kind_dir(root, "spec")) and not _hook_installed(settings):
        findings.append({"issue": "no SessionStart hook, so sessions only resume specs if the agent "
                                  "remembers to run `kb spec resume`",
                         "fix": "kb agents init --hook"})
    if os.path.isdir(specs.kind_dir(root, "spec")) and not _guard_installed(settings):
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
                    f.write(f"{IMPORT_LINE}\n\n" + text)
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
