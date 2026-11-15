"""Pluggable sync sources: auto-discovery of sync_<name>.py modules and the setup/doctor hints derived from their SOURCE_META."""
import importlib
import os
import sys
from collections.abc import Mapping


PKG_DIR = os.path.dirname(os.path.abspath(__file__))

class _LazyMeta(Mapping):
    """A sync module's SOURCE_META, read (by importing the module) the first time it is looked at."""

    def __init__(self, name, fname):
        self._name = name
        self._fname = fname
        self._meta = None

    def _load(self):
        if self._meta is None:
            try:
                module = importlib.import_module(f".sync_{self._name}", __package__)
                self._meta = getattr(module, "SOURCE_META", {})
            except Exception as e:
                print(f"warning: {self._fname} failed to load, ignoring its SOURCE_META "
                      f"(kb sync {self._name} will likely fail): {e}", file=sys.stderr)
                self._meta = {}
        return self._meta

    def __getitem__(self, key):
        return self._load()[key]

    def __iter__(self):
        return iter(self._load())

    def __len__(self):
        return len(self._load())


def discover_sync_sources():
    """Auto-discover sync_<name>.py modules in the package as pluggable sync sources.

    Adding a new one -- with an optional module-level SOURCE_META dict (see
    docs/reference/ingestion.md for the contract) -- registers it as `kb sync
    <name>` and, wherever SOURCE_META declares cli_tool/env_keys, in doctor/
    setup's health checks and the sops-injection decision, without any other
    file needing an edit. A script with no SOURCE_META (or an empty one) still
    works via `kb sync <name>`, just opts out of those automated checks.

    Returns {name: (filename, meta)}, sorted by name so `kb sync all`, the
    argparse choices list, and --help output all have a stable order.

    This runs on every `kb` invocation (argparse needs the choices list up
    front), so it only lists the files: `meta` is a lazy mapping that imports
    the module the first time it is read (doctor/setup/sync), and an import
    error there is a warning with empty metadata rather than taking down every
    `kb` command."""
    sources = {}
    for fname in sorted(os.listdir(PKG_DIR)):
        if fname.startswith("sync_") and fname.endswith(".py"):
            name = fname[len("sync_"):-len(".py")]
            sources[name] = (fname, _LazyMeta(name, fname))
    return sources


def sync_tool_todos(sync_sources, tools):
    """todo lines for any discovered sync source whose SOURCE_META declares a
    cli_tool that's missing, or installed but (per auth_check) not
    authenticated. Shared by `kb setup` and `kb doctor` so the two can't drift
    on wording for the same check."""
    todo = []
    for name, (_fname, meta) in sync_sources.items():
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
    for name, (_fname, meta) in sync_sources.items():
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
