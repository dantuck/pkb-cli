"""Shared test helpers."""
import contextlib
import importlib
import os
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


# kb_web name -> (module, attribute) it is looked up through. A test patches by the name kb_web
# calls and never has to know which module the function lives in.
_KB_WEB_TARGETS = {
    "__version__": ("kb_web", "__version__"),
    "_all_tags": ("pkb_entries", "_all_tags"),
    "_find_entry": ("pkb_entries", "_find_entry"),
    "_inbox_discard": ("inbox", "discard"),
    "_inbox_find": ("inbox", "find"),
    "_inbox_promote": ("inbox", "promote"),
    "_inbox_redirect": ("inbox", "redirect"),
    "_iter_all_entries": ("pkb_entries", "_iter_all_entries"),
    "_journal_rollup": ("journal", "journal_rollup"),
    "_readonly_source_info": ("pkb_entries", "_readonly_source_info"),
    "apply_update": ("updater", "apply_update"),
    "bd_action": ("todos", "bd_action"),
    "bd_available": ("todos", "bd_available"),
    "bd_create": ("todos", "bd_create"),
    "bd_delete": ("todos", "bd_delete"),
    "bd_dep_add": ("todos", "bd_dep_add"),
    "bd_dep_remove": ("todos", "bd_dep_remove"),
    "bd_deps": ("todos", "bd_deps"),
    "bd_list": ("todos", "bd_list"),
    "bd_snooze": ("todos", "bd_snooze"),
    "bd_unsnooze": ("todos", "bd_unsnooze"),
    "bd_update": ("todos", "bd_update"),
    "cmd_doctor": ("admin", "cmd_doctor"),
    "cmd_index": ("admin", "cmd_index"),
    "cmd_setup": ("admin", "cmd_setup"),
    "cmd_sync": ("admin", "cmd_sync"),
    "cmd_sync_service": ("service", "cmd_sync_service"),
    "cmd_triage": ("admin", "cmd_triage"),
    "cmd_validate": ("admin", "cmd_validate"),
    "discover_sync_sources": ("sources", "discover_sync_sources"),
    "doctor_checks": ("health", "doctor_checks"),
    "entry_new": ("pkb_entries", "entry_new"),
    "entry_set_links": ("pkb_entries", "entry_set_links"),
    "entry_set_tags": ("pkb_entries", "entry_set_tags"),
    "entry_update_content": ("pkb_entries", "entry_update_content"),
    "inbox_list": ("pkb_entries", "inbox_list"),
    "journal_append": ("journal", "journal_append"),
    "journal_months": ("journal", "journal_months"),
    "open_notification_settings": ("notifier", "open_notification_settings"),
    "push_status": ("health", "push_status"),
    "reminders_info": ("reminders", "reminders_info"),
    "reminders_install": ("reminders", "reminders_install"),
    "schedule_kb_command": ("updater", "schedule_kb_command"),
    "send_test_notification": ("notifier", "send_test_notification"),
    "service_info": ("service", "service_info"),
    "show_links": ("pkb_entries", "show_links"),
    "update_info": ("updater", "update_info"),
}


class _PatchedKbWeb:
    """Every function kb_web calls into is replaced by a MagicMock (as the old `_kb` seam was), so
    nothing real runs by accident: `kb.bd_list.return_value = [...]`, `kb.cmd_validate = noisy`.
    Undone when the context exits."""

    def __init__(self, stack):
        for name in _KB_WEB_TARGETS:
            mod, attr = self._target(name)
            new = "0.0.0-test" if name == "__version__" else mock.DEFAULT
            object.__setattr__(self, name, stack.enter_context(mock.patch.object(mod, attr, new)))

    @staticmethod
    def _target(name):
        mod, attr = _KB_WEB_TARGETS[name]
        return importlib.import_module(f"pkb_cli.{mod}"), attr

    def __setattr__(self, name, value):
        mod, attr = self._target(name)
        setattr(mod, attr, value)  # restored by the patch entered above
        object.__setattr__(self, name, value)


@contextlib.contextmanager
def patched_kb_web():
    with contextlib.ExitStack() as stack:
        yield _PatchedKbWeb(stack)
