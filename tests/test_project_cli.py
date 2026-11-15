"""CLI-level tests for kb project/spec/adr/agents/docs: argument layout, --json output, exit codes."""
import contextlib
import io
import json
import os
import tempfile
import unittest

from pkb_cli import cli


class ProjectCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def kb(self, *argv):
        """Run `kb <argv> --dir ROOT`, returning (exit code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        args = cli.build_parser().parse_args(list(argv) + ["--dir", self.root])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = args.func(args) or 0
        return code, out.getvalue(), err.getvalue()

    def kbj(self, *argv):
        code, out, err = self.kb(*argv, "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def _write_spec_body(self, spec):
        path = os.path.join(self.root, spec["path"])
        with open(path, encoding="utf-8") as f:
            text = f.read()
        head = text[:text.index("## Problem")]
        with open(path, "w", encoding="utf-8") as f:
            f.write(head + "## Requirements\n\n- R1: works\n  - Accept: tested\n\n"
                           "## Tasks\n\n- [ ] one\n- [ ] two\n")

    def test_full_spec_flow(self):
        self.assertEqual(self.kbj("project", "init")["root"], self.root)
        spec = self.kbj("spec", "new", "Add login")
        self.assertEqual(spec["id"], "0001")
        code, _, err = self.kb("spec", "status", "1", "approved", "--json")
        self.assertEqual(code, 1)
        self.assertIn("not ready", json.loads(err)["error"])
        self._write_spec_body(spec)
        self.assertEqual(self.kbj("spec", "status", "1", "approved")["status"], "approved")
        self.kbj("spec", "status", "1", "implementing")
        resume = self.kbj("spec", "resume")
        self.assertEqual(resume[0]["next_task"], {"id": "T1", "text": "one"})
        self.kbj("spec", "task", "1", "T1")
        self.assertEqual(self.kbj("spec", "task", "1", "2")["progress"], {"done": 2, "total": 2})
        self.assertEqual(self.kbj("spec", "status", "1", "done")["status"], "done")
        self.assertEqual(self.kbj("spec", "list", "--status", "done")[0]["tasks"], {"done": 2, "total": 2})
        self.assertIn("Add login", self.kb("spec", "list")[1])  # text mode renders

    def test_missing_arguments_are_argparse_usage_errors(self):
        for argv in (["spec", "new"], ["spec", "show"], ["spec", "task", "1"], ["spec", "bogus"],
                     ["adr", "task", "1", "T1"], ["docs", "new", "how-to"], ["docs", "search"],
                     ["docs", "new", "nope", "x"]):
            with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(io.StringIO()):
                self.kb(*argv)
            self.assertEqual(ctx.exception.code, 2, argv)

    def test_bare_group_prints_help_and_exits_2(self):
        for group in ("spec", "adr", "agents", "docs"):
            args = cli.build_parser().parse_args([group])
            with contextlib.redirect_stderr(io.StringIO()) as err:
                code = args.func(args)
            self.assertEqual(code, 2, group)
            self.assertIn("usage:", err.getvalue())

    def test_runtime_errors_exit_1_with_message(self):
        for argv in (["spec", "status", "9"], ["spec", "show", "9"], ["adr", "status", "9", "accepted"],
                     ["docs", "new", "how-to", "  "]):
            code, _, err = self.kb(*argv)
            self.assertEqual(code, 1, argv)
            self.assertTrue(err.startswith("error:"), (argv, err))

    def test_adr_via_cli(self):
        self.kbj("adr", "new", "Use sqlite")
        self.assertEqual(self.kbj("adr", "status", "1", "accepted")["status"], "accepted")
        self.assertEqual(self.kb("adr", "status", "1", "bogus")[0], 1)
        self.assertEqual(len(self.kbj("adr", "list")), 1)

    def test_resume_quiet_prints_nothing_without_active_specs(self):
        code, out, _ = self.kb("spec", "resume", "--quiet")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("no active specs", self.kb("spec", "resume")[1])

    def test_agents_flow_with_hook(self):
        self.kbj("spec", "new", "X")
        self.assertTrue(self.kbj("agents", "check")["findings"])
        self.kbj("agents", "init", "--hook", "--guard")
        self.assertEqual(self.kbj("agents", "check")["findings"], [])

    def test_docs_new_check_search(self):
        self.kbj("project", "init")
        page = self.kbj("docs", "new", "how-to", "Deploy to staging")
        self.assertEqual(page["path"], "docs/how-to/deploy-to-staging.md")
        self.assertEqual(self.kb("docs", "new", "how-to", "Deploy to staging")[0], 1)  # never overwrites
        issues = [f["issue"] for f in self.kbj("docs", "check")]
        self.assertIn("not linked from any README index", issues)
        with open(os.path.join(self.root, "docs", "README.md"), "a") as f:
            f.write("- [Deploy](how-to/deploy-to-staging.md)\n- [Gone](how-to/gone.md)\n")
        issues = [f["issue"] for f in self.kbj("docs", "check")]
        self.assertNotIn("not linked from any README index", issues)
        self.assertIn("broken link: how-to/gone.md", issues)
        self.assertEqual(self.kbj("docs", "search", "staging")[0]["path"], "docs/how-to/deploy-to-staging.md")

    def test_docs_check_flags_misfiled_pages(self):
        self.kbj("project", "init")
        with open(os.path.join(self.root, "docs", "stray.md"), "w") as f:
            f.write("# Stray\n")
        with open(os.path.join(self.root, "docs", "explanation", "e.md"), "w") as f:
            f.write("# E\n" + "".join(f"{i}. step\n" for i in range(1, 7)))
        issues = {(f["path"], f["issue"]) for f in self.kbj("docs", "check")}
        self.assertIn(("docs/stray.md", "not filed under a Diataxis quadrant"), issues)
        self.assertIn(("docs/explanation/e.md", "numbered steps in explanation/: looks like a how-to"), issues)


if __name__ == "__main__":
    unittest.main()
