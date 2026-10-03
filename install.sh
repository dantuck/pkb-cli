#!/usr/bin/env bash
# Install the pkb-cli tool (the `kb` command) via uv or pipx.
#
#   curl -fsSL https://raw.githubusercontent.com/dantuck/pkb-cli/main/install.sh | bash
#
# This installs the TOOL only. It has no opinion about your personal pkb data
# repo (tutorials/how-to/journal/inbox/sources/etc) -- that's a separate,
# typically private repo. Clone it yourself, then run `kb setup` from inside
# it to wire up validation, search indexing, and sync.
#
# Requires uv (https://docs.astral.sh/uv/) or pipx. If you'd rather not run a
# script, the equivalent by hand is:
#   uv tool install git+https://github.com/dantuck/pkb-cli
#   pipx install git+https://github.com/dantuck/pkb-cli
#
# Installs the latest tagged release (falling back to $PKB_CLI_REF, default
# main, if there is none). Override the source with $PKB_CLI_REPO (an
# "owner/repo" GitHub slug) and the version with $PKB_CLI_REF (a tag or
# branch). Safe to re-run: reinstalls in place, and `kb setup --yes` is
# idempotent. Update later with `kb update`.
set -euo pipefail

REPO_SLUG="${PKB_CLI_REPO:-dantuck/pkb-cli}"
REF="${PKB_CLI_REF:-}"

if [ -z "$REF" ]; then
  REF="$(curl -fsSL "https://api.github.com/repos/$REPO_SLUG/releases/latest" 2>/dev/null \
    | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -n1 || true)"
  REF="${REF:-main}"
fi
SPEC="git+https://github.com/$REPO_SLUG@$REF"

if command -v uv >/dev/null 2>&1; then
  echo "installing pkb-cli ($REPO_SLUG@$REF) with uv"
  uv tool install --force "$SPEC"
elif command -v pipx >/dev/null 2>&1; then
  echo "installing pkb-cli ($REPO_SLUG@$REF) with pipx"
  pipx install --force "$SPEC"
else
  echo "error: install uv (https://docs.astral.sh/uv/) or pipx (https://pipx.pypa.io/) first, then re-run this" >&2
  exit 1
fi

if ! command -v kb >/dev/null 2>&1; then
  # the tool's bin dir may not be on PATH yet in this shell
  export PATH="$HOME/.local/bin:$PATH"
fi
kb setup --yes

echo
echo "kb is installed. if the command isn't found, open a new shell"
echo "(or run \`uv tool update-shell\` / \`pipx ensurepath\`)."
echo
echo "next: clone your own pkb data repo (private, separate from this tool),"
echo "then run 'kb setup' from inside it."
