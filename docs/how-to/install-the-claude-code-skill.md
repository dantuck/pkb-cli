# How to install the Claude Code skill

This repo bundles a [Claude Code skill](https://code.claude.com/docs/en/skills)
at [skills/kb/SKILL.md](../../skills/kb/SKILL.md) that teaches Claude how and
when to use `kb` on your behalf — search, journal, inbox triage, TODOs, sync.

Install it with:

```bash
kb setup --install-skill
```

This symlinks `skills/kb` into `~/.claude/skills/kb` — a symlink, not a copy,
so `kb update` keeps it current automatically. Pass a directory to install it
elsewhere:

```bash
kb setup --install-skill /path/to/skills/dir
```
