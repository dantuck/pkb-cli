"""The argparse tree: every command is wired to a handler, documents itself, and a typo gets a suggestion."""
import argparse
import contextlib
import io
import unittest

from pkb_cli import cli


def subparsers(parser):
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction)).choices


class ParserTest(unittest.TestCase):
    def test_every_command_has_a_handler_and_help(self):
        for name, sub in subparsers(cli.build_parser()).items():
            self.assertTrue(callable(sub.get_default("func")), name)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
                sub.parse_args(["-h"])
            self.assertEqual(cm.exception.code, 0, name)
            self.assertIn("usage:", out.getvalue(), name)

    def test_typo_suggests_the_closest_command(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            cli.build_parser().parse_args(["serach"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("did you mean 'search'", err.getvalue())

    def test_help_lists_commands_in_the_epilog_only_if_they_exist(self):
        names = set(subparsers(cli.build_parser()))
        for line in cli.EPILOG.splitlines():
            line = line.strip()
            if line.startswith("kb ") and not line.startswith("kb operates"):
                self.assertIn(line.split()[1], names, line)


if __name__ == "__main__":
    unittest.main()
