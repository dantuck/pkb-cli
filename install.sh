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
# python3 is the only required dependency -- no git, curl, or tar. This
# downloads a tarball snapshot of the repo (via python's stdlib urllib) rather
# than git-cloning it, so `kb update` later works the same way -- in fact, if
# $PKB_CLI_HOME already looks like a prior tarball install (it has our version
# marker), this script just delegates to that install's own `kb update` rather
# than re-implementing the fetch/apply dance here. If $PKB_CLI_HOME is instead
# a git checkout (e.g. you cloned it yourself to contribute), this runs
# `git pull` there, so that workflow keeps working.
#
# Respects $PKB_CLI_HOME to override the install destination (default: ~/pkb-cli),
# $PKB_CLI_REPO to override the source (default: dantuck/pkb-cli, as an
# "owner/repo" GitHub slug), and $PKB_CLI_BRANCH (default: main). Safe to
# re-run: updates in place if already installed, and `kb setup --yes` is
# idempotent.
set -euo pipefail

REPO_SLUG="${PKB_CLI_REPO:-dantuck/pkb-cli}"
BRANCH="${PKB_CLI_BRANCH:-main}"
DEST="${PKB_CLI_HOME:-$HOME/pkb-cli}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi

python3 - "$REPO_SLUG" "$BRANCH" "$DEST" <<'PYEOF'
import io, json, os, shutil, subprocess, sys, tarfile, tempfile, urllib.request

repo_slug, branch, dest = sys.argv[1], sys.argv[2], os.path.expanduser(sys.argv[3])
headers = {"User-Agent": "pkb-cli-install"}


def get(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
        return resp.read()


def latest_sha():
    return json.loads(get(f"https://api.github.com/repos/{repo_slug}/commits/{branch}"))["sha"]


def write_file_atomic(path, fileobj, mode=None):
    """Same-directory temp file + os.replace, so a process killed mid-write
    leaves the original untouched instead of a half-written file."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            shutil.copyfileobj(fileobj, f)
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def apply_tarball(dest, data):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        top = members[0].name.split("/")[0] + "/"  # e.g. "pkb-cli-<sha>/"
        for member in members:
            if not member.name.startswith(top):
                continue
            rel = member.name[len(top):]
            if not rel:
                continue
            path = os.path.join(dest, rel)
            if member.isdir():
                os.makedirs(path, exist_ok=True)
            elif member.isfile():
                mode = (member.mode & 0o777) if (member.mode & 0o111) else None
                write_file_atomic(path, tar.extractfile(member), mode=mode)


version_file = os.path.join(dest, ".pkb-cli-version")
git_dir = os.path.join(dest, ".git")

if os.path.isdir(git_dir):
    print(f"pkb-cli already present at {dest} as a git checkout -- pulling latest")
    if not shutil.which("git"):
        sys.exit(f"error: {dest} is a git checkout but git isn't installed -- "
                  f"pull manually or remove it and re-run this installer")
    try:
        subprocess.run(["git", "-C", dest, "pull", "--ff-only"], check=True)
    except subprocess.CalledProcessError as e:
        sys.exit(f"error: git pull failed ({e}) -- resolve it manually in {dest}")
    sys.exit(0)

if os.path.exists(version_file):
    # Already a tarball install -- delegate to its own (tested) `kb update`
    # rather than re-implementing the fetch/apply dance here.
    print(f"pkb-cli already present at {dest} -- updating")
    kb_path = os.path.join(dest, "scripts", "kb")
    if os.path.exists(kb_path):
        sys.exit(subprocess.run([sys.executable, kb_path, "update"]).returncode)
    print(f"warning: {version_file} exists but {kb_path} is missing -- reinstalling fresh")

if os.path.isdir(dest) and os.listdir(dest) and not os.path.exists(version_file):
    sys.exit(f"error: {dest} already exists and isn't a pkb-cli checkout -- "
              f"set $PKB_CLI_HOME to a different path")

sha = latest_sha()
print(f"installing pkb-cli ({repo_slug}@{branch}, {sha[:8]}) -> {dest}")
os.makedirs(dest, exist_ok=True)
apply_tarball(dest, get(f"https://github.com/{repo_slug}/archive/{sha}.tar.gz", timeout=60))
with open(version_file, "w") as f:
    f.write(sha + "\n")
PYEOF

python3 "$DEST/scripts/kb" setup --yes

echo
echo "kb is installed. if the command isn't found, open a new shell, or:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo
echo "next: clone your own pkb data repo (private, separate from this tool),"
echo "then run 'kb setup' from inside it."
