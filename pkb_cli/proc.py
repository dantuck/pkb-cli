"""Helpers for running kb itself and its sibling modules as subprocesses."""
import subprocess
import sys

def run_captured(cmd, **kw):
    """Run `cmd` with stderr merged into stdout, print the combined output, and
    return the returncode -- rather than inheriting stdio directly, so kb web
    can capture the real output via redirect_stdout (see kb_web.py's
    _capture_stdout) instead of it only reaching the terminal kb web happens to
    be running in, which, launched from a launchd plist, has no terminal
    anyone can see. errors="replace" keeps this from crashing on any non-UTF-8
    bytes a child process writes (e.g. binary noise in a library error
    message) -- inherited stdio would have passed those bytes through as-is,
    so decoding must not raise here either."""
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, errors="replace", **kw)
    print(result.stdout, end="")
    return result.returncode


def module_cmd(module):
    """argv to run one of this package's modules as a script (`python -m pkb_cli.<module>`)."""
    return [sys.executable, "-m", f"{__package__}.{module}"]


def kb_cmd(*args):
    """argv to run kb itself (`python -m pkb_cli ...`) with this interpreter -- used for
    launchd/systemd units, which can't rely on `kb` being on their minimal PATH."""
    return [sys.executable, "-m", __package__, *args]


def run_script(name, args=None):
    cmd = module_cmd(name) + (args or [])
    return run_captured(cmd)
