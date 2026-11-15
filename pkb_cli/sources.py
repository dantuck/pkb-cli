"""Pluggable sync sources: auto-discovery of sync_<name>.py modules and the setup/doctor hints derived from their SOURCE_META."""
import functools
import importlib
import os
import sys


PKG_DIR = os.path.dirname(os.path.abspath(__file__))

def discover_sync_sources():
    """Auto-discover sync_<name>.py modules in the package as pluggable sync sources.

    Adding a new one -- with an optional module-level SOURCE_META dict (see
    docs/reference/ingestion.md for the contract) -- registers it as `kb sync
    <name>` and, wherever SOURCE_META declares cli_tool/env_keys, in doctor/
    setup's health checks and the sops-injection decision, without any other
    file needing an edit. A script with no SOURCE_META (or an empty one) still
    works via `kb sync <name>`, just opts out of those automated checks.

    Returns {name: filename}, sorted by name so `kb sync all`, the argparse
    choices list, and --help output all have a stable order.

    This runs on every `kb` invocation (argparse needs the choices list up
    front), so it only lists the files; a source's SOURCE_META is read, by
    importing its module, through source_meta() by the callers that need it."""
    return {fname[len("sync_"):-len(".py")]: fname
            for fname in sorted(os.listdir(PKG_DIR)) if fname.startswith("sync_") and fname.endswith(".py")}


@functools.cache
def source_meta(name):
    """SOURCE_META of the sync source `name`, imported on first use. A module that fails to
    import is a warning with empty metadata rather than taking down every `kb` command that
    asks (doctor, setup); `kb sync <name>` then fails on its own, in its subprocess."""
    try:
        return getattr(importlib.import_module(f".sync_{name}", __package__), "SOURCE_META", {})
    except Exception as e:
        print(f"warning: sync_{name}.py failed to load, ignoring its SOURCE_META "
              f"(kb sync {name} will likely fail): {e}", file=sys.stderr)
        return {}


def sync_tool_todos(sync_sources, tools):
    """todo lines for any discovered sync source whose SOURCE_META declares a
    cli_tool that's missing, or installed but (per auth_check) not
    authenticated. Shared by `kb setup` and `kb doctor` so the two can't drift
    on wording for the same check."""
    todo = []
    for name in sync_sources:
        meta = source_meta(name)
        cli_tool = meta.get("cli_tool")
        if not cli_tool or not meta.get("not_found_hint"):
            continue
        if tools[cli_tool]:
            if meta.get("auth_check") and not tools.get(f"{cli_tool}_auth"):
                todo.append(meta.get("not_authed_hint", f"{cli_tool} is installed but not authenticated"))
        else:
            todo.append(meta["not_found_hint"])
    return todo


def sync_configured_sources(sync_sources, tools):
    """Names of discovered sync sources that look ready to run right now, per
    SOURCE_META: env_keys (if any) all set in the environment, and (if
    cli_tool/auth_check are declared) that tool authenticated. A source with
    neither env_keys nor cli_tool declared (SOURCE_META is empty/absent) is
    never reported here -- there's nothing to check."""
    configured = []
    for name in sync_sources:
        meta = source_meta(name)
        env_keys = meta.get("env_keys") or []
        cli_tool = meta.get("cli_tool")
        if not env_keys and not cli_tool:
            continue
        if env_keys and not all(os.environ.get(k) for k in env_keys):
            continue
        if cli_tool and not (tools.get(f"{cli_tool}_auth") if meta.get("auth_check") else tools.get(cli_tool)):
            continue
        configured.append(name)
    return configured
