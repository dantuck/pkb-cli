#!/usr/bin/env bash
# Install the pkb-cli tool (the `kb` command) on a new machine.
#
#   curl -fsSL https://raw.githubusercontent.com/dantuck/pkb-cli/main/install.sh | bash
#
# This installs the TOOL only. It has no opinion about your personal pkb data
# repo (tutorials/how-to/journal/inbox/sources/etc) -- that's a separate,
# typically private repo. Clone it yourself, then run `kb setup` from inside
# it to wire up validation, search indexing, and sync.
#
# Respects $PKB_CLI_HOME to override the install destination (default: ~/pkb-cli)
# and $PKB_CLI_REPO_URL to override the source. Safe to re-run: pulls latest if
# already cloned, and `kb setup --install` is idempotent.
set -euo pipefail

REPO_URL="${PKB_CLI_REPO_URL:-https://github.com/dantuck/pkb-cli.git}"
DEST="${PKB_CLI_HOME:-$HOME/pkb-cli}"

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is required" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi

if [ -d "$DEST" ]; then
  if [ -d "$DEST/.git" ] && [ -f "$DEST/scripts/kb" ]; then
    echo "pkb-cli already present at $DEST -- pulling latest"
    git -C "$DEST" pull --ff-only
  else
    echo "error: $DEST already exists and isn't a pkb-cli checkout -- set \$PKB_CLI_HOME to a different path" >&2
    exit 1
  fi
else
  echo "cloning $REPO_URL -> $DEST"
  git clone "$REPO_URL" "$DEST"
fi

python3 "$DEST/scripts/kb" setup --install

echo
echo "kb is installed. if the command isn't found, open a new shell, or:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo
echo "next: clone your own pkb data repo (private, separate from this tool),"
echo "then run 'kb setup' from inside it."
