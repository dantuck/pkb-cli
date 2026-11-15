"""Maintenance commands (sync, index, validate, triage) that shell out to the worker scripts. Shared by the CLI and kb web."""
import os
import shlex
import shutil
import sys

from . import health
from . import index_fts
from . import pkb_common as pc
from . import proc
from . import sources

def cmd_triage(args):
    return proc.run_script("triage_report", ["--json"] if args.json else [])


def cmd_sync(args):
    sync_sources = sources.discover_sync_sources()
    # sources whose SOURCE_META sets uses_sops read credentials from the environment;
    # if this data repo keeps them sops-encrypted, decrypt straight into that one
    # subprocess's env via `sops exec-env` -- no wrapping needed at the call site,
    # and the values never pass through kb's own environment or output.
    targets = list(sync_sources) if args.source in (None, "all") else [args.source]
    root = pc.get_repo_root()
    secrets_path = os.path.join(root, ".pkb", "secrets.enc.env")
    use_sops = shutil.which("sops") and os.path.exists(secrets_path)

    rc = 0
    for t in targets:
        print(f"--- sync: {t} ---")
        cmd = proc.module_cmd(sync_sources[t][:-len(".py")])
        if use_sops and sources.source_meta(t).get("uses_sops"):
            cmd_str = shlex.join(cmd)
            target_rc = proc.run_captured(["sops", "exec-env", secrets_path, cmd_str], env=health.sops_key_env())
        else:
            target_rc = proc.run_captured(cmd)
        rc = target_rc or rc
    pc.git_autocommit(root, f"sync: {', '.join(targets)}")
    # sync writes new/updated files straight to disk -- without this the FTS
    # index goes stale immediately and `kb doctor`/`kb search` won't see them
    # until someone remembers to run `kb index` by hand.
    index_rc = reindex(root)
    return index_rc or rc


def reindex(root, full=False):
    """Run the indexer in-process (no fresh interpreter). Its warnings and errors go to stdout, as the
    old subprocess run's did, so kb web's stdout capture still shows them."""
    return index_fts.run(root, full=full, err=sys.stdout)


def cmd_index(args):
    return reindex(pc.get_repo_root(), full=args.full)


def cmd_validate(args):
    return proc.run_script("validate_frontmatter")
