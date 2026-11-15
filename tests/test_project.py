"""Tests for pkb_cli/project.py: project docs scaffold, specs/ADRs, agents files."""
import json
import os
import tempfile
import unittest
from unittest import mock

from pkb_cli import project, todos


READY = """# T

## Requirements

- R1: it works
  - Accept: a test proves it

## Tasks

- [ ] first
- [ ] second
"""


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _ready_spec(self):
        r = project.doc_new(self.root, "spec", "Add login")
        path = os.path.join(self.root, r["path"])
        doc = project._doc(path)
        project._write(path, doc["fm"], READY)
        return r["id"]

    def test_init_is_idempotent_and_keeps_existing(self):
        first = project.project_init(self.root)
        self.assertIn("docs/specs/README.md", first["created"])
        self.assertEqual(project.project_init(self.root)["created"], [])

    def test_ids_increment_and_resolve_by_slug(self):
        project.doc_new(self.root, "spec", "One")
        project.doc_new(self.root, "spec", "Two things")
        doc, err = project.find_doc(self.root, "spec", "two")
        self.assertIsNone(err)
        self.assertEqual(doc["id"], "0002")
        self.assertEqual(project.find_doc(self.root, "spec", "2")[0]["id"], "0002")

    def test_fresh_template_is_not_ready(self):
        r = project.doc_new(self.root, "spec", "Fresh")
        out = project.doc_set_status(self.root, "spec", r["id"], "approved")
        self.assertIn("not ready", out["error"])

    def test_lifecycle_gates(self):
        sid = self._ready_spec()
        self.assertIn("next step", project.doc_set_status(self.root, "spec", sid, "done")["error"])
        self.assertEqual(project.doc_set_status(self.root, "spec", sid, "approved")["status"], "approved")
        self.assertEqual(project.doc_set_status(self.root, "spec", sid, "implementing")["status"], "implementing")
        self.assertIn("unchecked", project.doc_set_status(self.root, "spec", sid, "done")["error"])
        project.spec_task(self.root, sid, 1)
        r = project.spec_task(self.root, sid, "T2")
        self.assertEqual(r["progress"], {"done": 2, "total": 2})
        self.assertEqual(project.doc_set_status(self.root, "spec", sid, "done")["status"], "done")

    def test_task_ids_are_stable_when_tasks_are_inserted(self):
        sid = self._ready_spec()
        project.spec_task(self.root, sid, "T2")  # pins ids T1/T2 into the file
        path = os.path.join(self.root, project.doc_list(self.root, "spec")[0]["path"])
        text = open(path).read().replace("- [ ] T1: first", "- [ ] brand new\n- [ ] T1: first")
        open(path, "w").write(text)
        project.spec_task(self.root, sid, "3")  # the new task got T3, not position 3's old meaning
        doc, _ = project.find_doc(self.root, "spec", sid)
        tasks = {t["id"]: t for t in project._parse_tasks(doc["body"])}
        self.assertTrue(tasks["T3"]["done"])
        self.assertEqual(tasks["T3"]["text"], "brand new")
        self.assertTrue(tasks["T2"]["done"])
        self.assertFalse(tasks["T1"]["done"])

    def test_todos_links_bd_issues_and_tick_closes_them(self):
        sid = self._ready_spec()
        with mock.patch.object(todos, "bd_available", return_value=True), \
                mock.patch.object(todos, "bd_create", side_effect=lambda *a, **k: json.dumps({"id": f"bd-{k['labels']}-{a[1][-5:]}"})) as create, \
                mock.patch.object(todos, "bd_action") as action:
            out = project.spec_todos(self.root, sid)
            self.assertEqual(len(out["created"]), 2)
            self.assertEqual(create.call_args.kwargs["labels"], "spec-0001")
            self.assertEqual(project.spec_todos(self.root, sid)["created"], [])  # idempotent
            r = project.spec_task(self.root, sid, "T1")
            self.assertTrue(r["bd_synced"])
            action.assert_called_with(self.root, "close", r["bd"])

    def test_todos_requires_bd(self):
        sid = self._ready_spec()
        with mock.patch.object(todos, "bd_available", return_value=False):
            self.assertIn("not installed", project.spec_todos(self.root, sid)["error"])

    def test_hook_install_merges_and_is_idempotent(self):
        os.makedirs(os.path.join(self.root, ".claude"))
        path = os.path.join(self.root, ".claude", "settings.json")
        with open(path, "w") as f:
            json.dump({"permissions": {"allow": ["Bash(ls)"]}}, f)
        self.assertTrue(project.install_hook(self.root)[0])
        self.assertFalse(project.install_hook(self.root)[0])
        data = json.load(open(path))
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(len(data["hooks"]["SessionStart"]), 1)

    def test_hook_install_refuses_invalid_json(self):
        os.makedirs(os.path.join(self.root, ".claude"))
        path = os.path.join(self.root, ".claude", "settings.json")
        open(path, "w").write("{ nope")
        changed, msg = project.install_hook(self.root)
        self.assertFalse(changed)
        self.assertIn("not valid JSON", msg)
        self.assertEqual(open(path).read(), "{ nope")

    def test_check_suggests_hook_once_specs_exist(self):
        project.agents_init(self.root)
        project.doc_new(self.root, "spec", "X")
        self.assertTrue(any("SessionStart" in f["issue"] for f in project.agents_check(self.root)["findings"]))
        project.agents_init(self.root, hook=True)
        self.assertTrue(any("approve its own" in f["issue"] for f in project.agents_check(self.root)["findings"]))
        project.agents_init(self.root, hook=True, guard=True)
        self.assertEqual(project.agents_check(self.root)["findings"], [])

    def test_guard_merges_rules_and_is_idempotent(self):
        os.makedirs(os.path.join(self.root, ".claude"))
        path = os.path.join(self.root, ".claude", "settings.json")
        with open(path, "w") as f:
            json.dump({"permissions": {"ask": ["Bash(rm *)"], "allow": ["Bash(ls)"]}}, f)
        self.assertTrue(project.install_guard(self.root)[0])
        self.assertFalse(project.install_guard(self.root)[0])
        with open(path) as f:
            perms = json.load(f)["permissions"]
        self.assertEqual(perms["allow"], ["Bash(ls)"])
        self.assertEqual(perms["ask"], ["Bash(rm *)", *project.GUARD_RULES])

    def test_tags_are_validated(self):
        self.assertIn("invalid tag", project.doc_new(self.root, "spec", "X", tags=["a,b"])["error"])
        self.assertEqual(project.doc_new(self.root, "spec", "X", tags=["a-b", "c.d"])["status"], "draft")

    def test_duplicate_ids_are_detected_and_renumbered(self):
        a = project.doc_new(self.root, "spec", "Alpha")
        b = project.doc_new(self.root, "spec", "Beta")
        # simulate a merge of two branches that both created 0002
        dup = os.path.join(self.root, "docs", "specs", "0001-gamma.md")
        src = os.path.join(self.root, b["path"])
        with open(src) as f:
            text = f.read().replace("id: 0002", "id: 0001")
        with open(dup, "w") as f:
            f.write(text)
        self.assertEqual(list(project.duplicate_ids(self.root, "spec")), ["0001"])
        self.assertIn("ambiguous", project.find_doc(self.root, "spec", "1")[1])
        out = project.doc_renumber(self.root, "spec", "gamma")
        self.assertEqual((out["previous"], out["id"]), ("0001", "0003"))
        self.assertEqual(project.duplicate_ids(self.root, "spec"), {})
        self.assertEqual(project.find_doc(self.root, "spec", "gamma")[0]["id"], "0003")
        self.assertEqual(a["id"], "0001")

    def test_hook_is_upgraded_in_place(self):
        os.makedirs(os.path.join(self.root, ".claude"))
        path = os.path.join(self.root, ".claude", "settings.json")
        old = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "kb spec resume"}]}]}}
        with open(path, "w") as f:
            json.dump(old, f)
        self.assertTrue(project.install_hook(self.root)[0])
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(len(data["hooks"]["SessionStart"]), 1)
        self.assertIn("--quiet", data["hooks"]["SessionStart"][0]["hooks"][0]["command"])
        self.assertFalse(project.install_hook(self.root)[0])

    def test_doctor_lines_only_for_projects_using_kb(self):
        self.assertEqual(project.doctor_lines(self.root), ([], []))
        project.doc_new(self.root, "spec", "X")
        ok, todo = project.doctor_lines(self.root)
        self.assertTrue(ok)
        self.assertTrue(any("AGENTS.md" in t for t in todo))

    def test_task_undo_and_range(self):
        sid = self._ready_spec()
        project.spec_task(self.root, sid, 1)
        self.assertFalse(project.spec_task(self.root, sid, "T1", undo=True)["done"])
        self.assertIn("no task T9", project.spec_task(self.root, sid, "9")["error"])

    def test_resume_reports_next_task(self):
        sid = self._ready_spec()
        project.doc_set_status(self.root, "spec", sid, "approved")
        project.spec_task(self.root, sid, 1)
        active = project.spec_resume(self.root)
        self.assertEqual(active[0]["next_task"], {"id": "T2", "text": "second"})

    def test_adr_statuses_and_supersede(self):
        a = project.doc_new(self.root, "adr", "Use sqlite")
        b = project.doc_new(self.root, "adr", "Use postgres")
        self.assertEqual(a["status"], "proposed")
        project.doc_set_status(self.root, "adr", a["id"], "superseded", by=b["id"])
        doc, _ = project.find_doc(self.root, "adr", a["id"])
        self.assertEqual(doc["fm"]["superseded_by"], b["id"])
        self.assertIn("invalid", project.doc_set_status(self.root, "adr", a["id"], "done")["error"])

    def test_agents_init_creates_import_and_is_idempotent(self):
        out = project.agents_init(self.root)
        self.assertEqual(len(out["actions"]), 2)
        with open(os.path.join(self.root, "CLAUDE.md")) as f:
            self.assertTrue(f.read().startswith("@AGENTS.md"))
        self.assertEqual(project.agents_init(self.root)["actions"], [])
        self.assertEqual(project.agents_check(self.root)["findings"], [])

    def test_agents_init_preserves_existing_claude_md_unless_link(self):
        path = os.path.join(self.root, "CLAUDE.md")
        with open(path, "w") as f:
            f.write("# Mine\n")
        project.agents_init(self.root)
        self.assertEqual(open(path).read(), "# Mine\n")
        self.assertTrue(project.agents_check(self.root)["findings"])
        project.agents_init(self.root, link=True)
        self.assertEqual(open(path).read(), "@AGENTS.md\n\n# Mine\n")

    def test_agents_block_refreshes_in_place(self):
        project.agents_init(self.root)
        path = os.path.join(self.root, "AGENTS.md")
        text = open(path).read().replace("Never start from an unapproved spec.", "STALE")
        open(path, "w").write(text + "\nuser notes\n")
        self.assertTrue(project.agents_check(self.root)["findings"])
        project.agents_init(self.root)
        out = open(path).read()
        self.assertNotIn("STALE", out)
        self.assertIn("user notes", out)
        self.assertEqual(out.count(project.MARK_BEGIN), 1)


if __name__ == "__main__":
    unittest.main()
