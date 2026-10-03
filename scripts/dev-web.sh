#!/bin/sh
# Run `kb web` from this checkout against a throwaway data repo, on its own port,
# so the real `kb web` service (and your real notes/config) are never touched.
#   KB_DEV_REPO   data repo to serve   (default: /tmp/kb-dev; created if missing)
#   KB_DEV_PORT   port to bind         (default: 4199)
set -e
here=$(cd "$(dirname "$0")/.." && pwd)
repo=${KB_DEV_REPO:-/tmp/kb-dev}
port=${KB_DEV_PORT:-4199}
mkdir -p "$repo/.pkb"
[ -d "$repo/.git" ] || git -C "$repo" init -q
cd "$repo"
exec env PYTHONPATH="$here" KB_CONFIG_DIR="${KB_CONFIG_DIR:-$repo-cfg}" \
  python3 -m pkb_cli web --port "$port"
