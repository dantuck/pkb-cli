"""Guards the Claude Code skill against drift from the CLI: every subcommand and
every flag `kb <command> -h` advertises must appear in SKILL.md's command
reference, so an agent reading only the skill can reach everything kb can do."""
import os
import re
import subprocess
import sys
import unittest

REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SKILL = os.path.join(REPO, "pkb_cli", "skills", "kb", "SKILL.md")

IGNORED_FLAGS = {"--help"}  # argparse's own; short aliases (-y, -m) are covered via their long form


def _read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def _reference_blocks(skill):
    """{command: text} for the ``` command-reference block: each `kb <cmd>` line
    plus its indented continuation lines, joined per command."""
    block = re.findall(r"```\n(kb .*?)```", skill, re.S)[-1]
    blocks, current = {}, None
    for line in block.splitlines():
        m = re.match(r"kb ([a-z-]+)", line)
        if m:
            current = m.group(1)
        if current:
            blocks[current] = blocks.get(current, "") + line + "\n"
    return blocks


class SkillTest(unittest.TestCase):
    def test_every_command_and_flag_is_in_the_skill(self):
        commands = sorted(set(re.findall(r'sub\.add_parser\(\s*"([a-z-]+)"', _read("pkb_cli", "cli.py"))))
        self.assertGreater(len(commands), 10)
        blocks = _reference_blocks(_read("pkb_cli", "skills", "kb", "SKILL.md"))

        missing = []
        for cmd in commands:
            if cmd not in blocks:
                missing.append(f"kb {cmd} (command)")
                continue
            help_text = subprocess.run([sys.executable, "-m", "pkb_cli", cmd, "-h"], cwd=REPO,
                                       capture_output=True, text=True).stdout
            usage = help_text.split("\n\n", 1)[0]
            flags = set(re.findall(r"(?<![\w-])(--[a-zA-Z][\w-]*)", usage)) - IGNORED_FLAGS
            for flag in sorted(flags):
                if not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", blocks[cmd]):
                    missing.append(f"kb {cmd} {flag}")
        self.assertEqual(missing, [], "missing from the command reference in skills/kb/SKILL.md")


if __name__ == "__main__":
    unittest.main()
