"""Shared test helpers."""
import ast
import contextlib
import os
import types
from functools import partial
from unittest import mock

from pkb_cli import health
from pkb_cli import pkb_common as pc


def patch_tool_status(testcase):
    """Make health.tool_status skip the auth checks (`gh auth status`, ...): they are slow,
    and they make a test depend on whoever happens to be signed in on the machine."""
    patcher = mock.patch.object(health, "tool_status", partial(health.tool_status, check_auth=False))
    patcher.start()
    testcase.addCleanup(patcher.stop)


def write_test_entry(root, rel_path, body="body\n", **fm_overrides):
    """Write a valid entry at root/rel_path (frontmatter overridable by keyword); returns its path."""
    fm = {
        "id": "2026-01-01-0000", "created": "2026-01-01T00:00:00", "updated": "2026-01-01T00:00:00",
        "type": "how-to", "extension": None, "source": "manual", "source_id": None,
        "tags": [], "links": [], "title": "t",
    }
    fm.update(fm_overrides)
    path = os.path.join(root, rel_path)
    pc.write_entry(path, fm, body)
    return path


# Modules kb_web calls into that tests use for real (queries and fixtures) rather than mock.
_REAL_IN_KB_WEB = {"pc", "search", "index_fts"}


def kb_web_targets():
    """{(module name, attribute)} for every `module.attr` kb_web uses on one of the pkb_cli modules it
    imports, read from its source -- so there is no list to keep in step when kb_web gains a call."""
    from pkb_cli import kb_web
    with open(kb_web.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    mods = {n for n, v in vars(kb_web).items()
            if isinstance(v, types.ModuleType) and v.__name__.startswith("pkb_cli.")} - _REAL_IN_KB_WEB
    return {(node.value.id, node.attr) for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in mods}


class _MockedModule:
    """The mocks standing in for one module's functions. Reads return the mock (still valid for
    assertions after the context exits); assigning a replacement installs it on the real module."""

    def __init__(self, module, mocks):
        object.__setattr__(self, "_module", module)
        object.__setattr__(self, "_mocks", dict(mocks))

    def __getattr__(self, name):
        return self._mocks[name]

    def __setattr__(self, name, value):
        setattr(self._module, name, value)  # restored by the patch entered for this name
        self._mocks[name] = value


@contextlib.contextmanager
def patched_kb_web():
    """Replace every function kb_web calls into another module with a MagicMock (so nothing real runs by
    accident) and yield them by module: `kb.todos.bd_list.return_value = [...]`,
    `kb.admin.cmd_validate = noisy`. kb_web's `__version__` is pinned too. Undone on exit."""
    from pkb_cli import kb_web
    with contextlib.ExitStack() as stack:
        mocks = {}
        for mod_name, attr in sorted(kb_web_targets()):
            mocks.setdefault(mod_name, {})[attr] = stack.enter_context(mock.patch.object(getattr(kb_web, mod_name), attr))
        stack.enter_context(mock.patch.object(kb_web, "__version__", "0.0.0-test"))
        namespace = {name: _MockedModule(getattr(kb_web, name), m) for name, m in mocks.items()}
        namespace["kb_web"] = _MockedModule(kb_web, {})
        yield types.SimpleNamespace(**namespace)
