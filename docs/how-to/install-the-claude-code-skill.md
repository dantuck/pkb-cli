# How to install the Claude Code skill

This repo bundles a [Claude Code skill](https://code.claude.com/docs/en/skills)
at [skills/kb/SKILL.md](../../skills/kb/SKILL.md) that teaches Claude how and
when to use `kb` on your behalf — search, journal, inbox triage, TODOs, sync.

`kb setup` asks whether to install it (default: no, since not everyone using
`kb` also uses Claude Code):

```bash
kb setup
# ...
# Install the Claude Code skill for kb (lets Claude Code drive kb directly)? [y/N] y
```

Answering yes symlinks `skills/kb` into `~/.claude/skills/kb` — a symlink, not
a copy, so `kb update` keeps it current automatically. There's no flag to
install it to a different directory; symlink it there yourself if you need
that.
