"""Maintenance commands (sync, index, validate, triage, setup, doctor) that shell out to the worker scripts. Shared by the CLI and kb web."""
import contextlib
import io
import os
import shlex
import shutil
import subprocess

from . import health
from . import pkb_common as pc
from . import proc
from . import notifier
from . import projdocs
from . import reminders
from . import semantic
from . import service
from . import sources

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_confirm = pc.confirm


def cmd_triage(args):
    return proc.run_script("triage_report", ["--json"] if args.json else [])


def cmd_sync(args):
    sync_sources = sources.discover_sync_sources()
    # sources whose SOURCE_META sets uses_sops read credentials from the environment;
    # if this data repo keeps them sops-encrypted, decrypt straight into that one
    # subprocess's env via `sops exec-env` -- no wrapping needed at the call site,
    # and the values never pass through kb's own environment or output.
    targets = list(sync_sources.keys()) if args.source in (None, "all") else [args.source]
    root = pc.get_repo_root()
    secrets_path = os.path.join(root, ".pkb", "secrets.enc.env")
    use_sops = shutil.which("sops") and os.path.exists(secrets_path)

    rc = 0
    for t in targets:
        print(f"--- sync: {t} ---")
        fname, meta = sync_sources[t]
        cmd = proc.module_cmd(fname[:-len(".py")])
        if use_sops and meta.get("uses_sops"):
            cmd_str = shlex.join(cmd)
            target_rc = proc.run_captured(["sops", "exec-env", secrets_path, cmd_str], env=health.sops_key_env())
        else:
            target_rc = proc.run_captured(cmd)
        rc = target_rc or rc
    pc.git_autocommit(root, f"sync: {', '.join(targets)}")
    # sync writes new/updated files straight to disk -- without this the FTS
    # index goes stale immediately and `kb doctor`/`kb search` won't see them
    # until someone remembers to run `kb index` by hand.
    index_rc = proc.run_script("index_fts")
    return index_rc or rc


def cmd_index(args):
    return proc.run_script("index_fts", ["--full"] if args.full else [])


def cmd_validate(args):
    return proc.run_script("validate_frontmatter")


def cmd_setup(args):
    # root is optional: --install (and the tool/sync checks below) don't need a data
    # repo at all -- only the hook/index/bd/secrets steps do.
    ok, todo = [], []
    try:
        root = pc.get_repo_root()
    except SystemExit:
        # No data repo found anywhere (walking up from cwd) and no central kb at
        # ~/.pkb either -- this is a brand-new machine. Bootstrap the central kb
        # here instead of requiring a manual `mkdir -p ~/.pkb/.pkb && git init`
        # before setup can do anything, since that step was easy to miss (e.g.
        # when a scripted `kb setup --yes` runs non-interactively).
        central = os.path.join(os.path.expanduser("~"), ".pkb")
        os.makedirs(os.path.join(central, ".pkb"), exist_ok=True)
        if shutil.which("git") and not os.path.isdir(os.path.join(central, ".git")):
            subprocess.run(["git", "init", "-q"], cwd=central, check=True)
        pc.REPO_ROOT = central
        root = central
        ok.append(f"created central kb at {central} (none found)")

    # 0. kb itself is put on PATH by the installer (pipx/uv/pip); just confirm it.
    on_path = shutil.which("kb")
    if on_path:
        ok.append(f"kb is on PATH ({on_path})")
    else:
        todo.append("`kb` isn't on your PATH -- if installed with pipx, run `pipx ensurepath` and open a new shell")

    # 0b. install the bundled Claude Code skill -- guided, default no (not
    # everyone using kb also uses Claude Code). A symlink, not a copy --
    # Claude Code follows a skill-directory symlink and reads SKILL.md from
    # the target, so this repo stays the single source of truth and a `kb
    # update` picks up any skill changes automatically, same reasoning as
    # symlinking the kb binary itself above.
    skill_src = os.path.join(PKG_DIR, "skills", "kb")
    skill_dir = os.path.expanduser("~/.claude/skills")
    skill_target = os.path.join(skill_dir, "kb")
    if os.path.islink(skill_target) and os.readlink(skill_target) == skill_src:
        ok.append(f"kb skill already installed at {skill_target}")
    elif os.path.islink(skill_target) and os.readlink(skill_target).endswith(os.path.join("skills", "kb")):
        # our own symlink from an older layout/install location -- repoint it
        os.remove(skill_target)
        os.symlink(skill_src, skill_target)
        ok.append(f"repointed kb skill -> {skill_src}")
    elif os.path.lexists(skill_target):
        todo.append(f"{skill_target} already exists and isn't this repo's skill -- remove it yourself "
                     f"if you want kb's version there")
    elif _confirm("Install the Claude Code skill for kb (lets Claude Code drive kb directly)?",
                   default=False, assume_yes=args.yes):
        os.makedirs(skill_dir, exist_ok=True)
        os.symlink(skill_src, skill_target)
        ok.append(f"installed kb skill -> {skill_target}")

    sync_sources = sources.discover_sync_sources()
    tools = health.tool_status(sync_sources)

    # 0c. install missing optional CLI deps via brew -- guided, default yes
    # (this is the "including optional tooling" part of setup: bd/beads,
    # glab/gh for sync sources, rg/fzf for Tier 1 search, sops for secrets).
    to_install = health.missing_deps_with_formulas(tools, sync_sources)
    if not to_install:
        ok.append("all optional CLI deps (bd/glab/gh/rg/fzf/sops) already installed")
    elif not shutil.which("brew"):
        todo.append("brew not found -- install manually: " +
                    ", ".join(f"{t} (`{f}`)" for t, f in to_install))
    else:
        names = ", ".join(t for t, _f in to_install)
        if _confirm(f"Install missing optional CLI deps via brew ({names})?", default=True, assume_yes=args.yes):
            formulas = sorted({f for _t, f in to_install})
            result = subprocess.run(["brew", "install", *formulas], capture_output=True, text=True)
            if result.returncode == 0:
                ok.append(f"installed missing deps via brew: {', '.join(formulas)}")
                # just recheck presence of what we installed -- no need to re-discover
                # sync sources or re-run every auth_check subprocess again
                tools.update({t: shutil.which(t) is not None for t, _f in to_install})
            else:
                todo.append(f"brew install {' '.join(formulas)} failed: "
                            f"{result.stderr.strip() or result.stdout.strip()}")
        else:
            todo.append(f"skipped installing optional deps: {names} -- rerun `kb setup` "
                        f"or `brew install {' '.join(sorted({f for _t, f in to_install}))}` later")

    if root is None:
        todo.append("not inside a pkb data repo (no .pkb/ found) -- cd into one to also set up the "
                     "pre-commit hook, search index, and local bd store")
    else:
        # 1. pre-commit hook -- generated directly (not a symlinked template) since it
        # must hardcode this machine's path to the tool install, which lives in a
        # separate repo from the data repo the hook runs in.
        git_dir = os.path.join(root, ".git")
        if not os.path.isdir(git_dir):
            todo.append("not a git repo (or .git not found here) -- pre-commit hook skipped")
        else:
            hook_content = health.hook_expected_content()
            hook_path, existing = health.read_hook(root)
            if existing == hook_content:
                ok.append("pre-commit hook already installed")
            elif existing is not None and health.HOOK_MARKER not in existing:
                todo.append(f"{os.path.relpath(hook_path, root)} exists and isn't our hook -- leaving it alone")
            else:
                os.makedirs(os.path.dirname(hook_path), exist_ok=True)
                if os.path.lexists(hook_path):  # e.g. a stale/broken symlink from an older kb setup
                    os.remove(hook_path)
                with open(hook_path, "w", encoding="utf-8") as f:
                    f.write(hook_content)
                os.chmod(hook_path, 0o755)
                ok.append("installed pre-commit hook" if existing is None else "refreshed pre-commit hook path")

            # 1b. auto-commit excludes (fts.db, entry locks) -- every kb mutation
            # commits automatically, so these must be in place before that ever
            # runs, not just lazily on first write
            pc._ensure_git_excludes(root)
            ok.append("auto-commit excludes (fts.db, entry locks) are in place")

        # 2. FTS index
        db_path = health.index_status(root)
        full = not os.path.exists(db_path)
        rc = proc.run_script("index_fts", ["--full"] if full else [])
        ok.append(f"{'built' if full else 'refreshed'} the FTS5 search index" if rc == 0 else "index build failed")
        sem = health.semantic_status(root)
        if sem:
            (ok if sem[0] else todo).append(sem[1])
        elif semantic.missing_dependency() and semantic.config(root)[0]:
            ok.append("semantic search not installed (optional) -- `uv tool install 'pkb-cli[semantic]'` "
                      "adds meaning-based search")

        # 3. local bd store
        if tools["bd"]:
            store_exists, _git_excluded = health.bd_store_status(root)
            if store_exists:
                ok.append("local bd (beads) store already initialized")
            else:
                result = subprocess.run(
                    ["bd", "init", "--stealth", "--non-interactive"],
                    cwd=root, capture_output=True, text=True,
                )
                if result.returncode == 0:
                    ok.append("initialized a local bd store (--stealth: excluded from git)")
                else:
                    todo.append(f"bd init failed: {result.stderr.strip() or result.stdout.strip()}")
            # 3b. TODO reminders -- opt-in, default no (a background job on a timer is
            # not something to turn on unasked, including under --yes)
            kind = service.service_kind()
            reminders_on, reminder_msg = reminders.reminder_status_line(kind)
            if reminders_on:
                ok.append(reminder_msg)
            elif kind is not None and not service._service_state(kind, "remind")["installed"]:
                if _confirm("Check for due TODOs every 5 minutes and send desktop notifications "
                            "(kb remind install)?", default=False, assume_yes=args.yes):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        rc = service._interval_service_install(kind, "remind", root, ["remind", "check"], 5,
                                                       "kb remind -- notify about due TODOs")
                    if rc != 0:
                        todo.append(f"kb remind install failed: {out.getvalue().strip()}")
                    else:
                        ok.append("installed the TODO reminder check (every 5 minutes)")
                        notified, note = notifier.finish_reminder_install()
                        (ok if notified else todo).append(note.replace("\n", "\n         "))
                else:
                    todo.append(reminder_msg)
            else:
                todo.append(reminder_msg)
        else:
            todo.append("bd CLI not found -- `brew install beads` if you want local TODO tracking via kb sync beads")

        # 4. secrets (sops), informational only -- never touch/create real secrets ourselves
        sops_configured, secrets_exists = health.secrets_status(root)
        if sops_configured:
            if secrets_exists:
                ok.append(".pkb/secrets.enc.env already set up")
            elif tools["sops"]:
                example_path = os.path.join(PKG_DIR, "templates", "secrets.env.example")
                todo.append("run `kb secrets` to create encrypted memos/gitlab/github credentials "
                            f"(see {os.path.normpath(example_path)} for the expected keys)")
            else:
                todo.append("sops CLI not found -- install it to use encrypted credentials, or export "
                            "PKB_MEMOS_URL/PKB_MEMOS_TOKEN/PKB_GITLAB_PROJECT/PKB_GITHUB_REPO directly")

    # 5. cli_tool sync sources (e.g. glab/gh, required for GitLab/GitHub issue sync)
    todo.extend(sources.sync_tool_todos(sync_sources, tools))

    # 6. rg/fzf (Tier 1 search, optional -- kb search/index work without them)
    missing_search_tools = [t for t in ("rg", "fzf") if not tools[t]]
    if missing_search_tools:
        todo.append(f"{'/'.join(missing_search_tools)} not found -- optional, only needed for zero-setup "
                     f"search (`brew install ripgrep fzf`); kb search/index don't need them")

    # 7. what's actually configured for sync right now (informational)
    configured = sources.sync_configured_sources(sync_sources, tools)
    if configured:
        ok.append(f"sync sources ready in this shell: {', '.join(configured)}")
    else:
        todo.append("no sync source has credentials in this shell yet (memos/gitlab/github need env vars, "
                     "see README 'Ingestion credentials')")

    print("kb setup\n")
    for line in ok:
        print(f"  [ok]   {line}")
    for line in todo:
        print(f"  [todo] {line}")
    return 1 if any("failed" in t for t in todo) else 0


def cmd_doctor(args):
    """Read-only health check -- reuses the same detection helpers as `kb setup`
    but never writes anything, and adds a few deeper checks setup doesn't do."""
    try:
        root = pc.get_repo_root()
    except SystemExit:
        root = None
    ok, todo = health.doctor_checks(root)

    p_ok, p_todo = projdocs.doctor_lines()
    ok, todo = ok + p_ok, todo + p_todo

    print("kb doctor\n")
    for line in ok:
        print(f"  [ok]   {line}")
    for line in todo:
        print(f"  [todo] {line}")
    if not todo:
        print("\nall clear")
    return 1 if todo else 0
