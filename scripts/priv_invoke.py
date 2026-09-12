"""Shared logic for invoking priv_helper.py under pkexec.

The elevated process is given priv_helper.py's source over stdin — read
once, right here, by the unprivileged caller — rather than a path for
root to open later. See priv_helper.py's module docstring for why that
distinction matters: a path is re-openable (and therefore swappable) in
the window between the pkexec prompt appearing and the user answering it;
already-read bytes handed over a pipe are not.

`python3 -I -` also runs the elevated interpreter in isolated mode (no
PYTHONPATH, no user site-packages) with a minimal, explicit environment,
so nothing about the caller's environment can influence what it imports
or how it behaves.
"""
import json
import os
import signal
import subprocess
import sys

PKEXEC = "/usr/bin/pkexec"
PYTHON3 = "/usr/bin/python3"
PRIV_HELPER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "priv_helper.py")

# Only what python3 itself needs to start up; nothing from the caller's own
# environment is passed through.
MINIMAL_ENV = {"PATH": "/usr/bin"}


def fail(message):
    print(json.dumps({"ok": False, "error": message}))
    sys.exit(0)


def run_priv_helper(mode, args, timeout):
    with open(PRIV_HELPER_PATH, "rb") as f:
        helper_source = f.read()

    proc = subprocess.Popen(
        [PKEXEC, PYTHON3, "-I", "-", mode, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=MINIMAL_ENV,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(input=helper_source, timeout=timeout)
    except subprocess.TimeoutExpired:
        # Best-effort: this can only succeed while pkexec hasn't yet
        # transitioned into the fully-elevated target (real uid still
        # matches ours) — once it has, the kernel correctly refuses to let
        # the original unprivileged caller signal it. That's a property of
        # pkexec's own privilege boundary, not something this script can
        # or should work around; priv_helper.py's own internal timeouts
        # (which run as root killing root's own children) are what actually
        # bound the privileged work itself.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.communicate()
        fail(f"priv_helper timed out after {timeout}s")

    if proc.returncode != 0:
        detail = (err.decode(errors="replace") or out.decode(errors="replace")).strip()
        fail(detail or f"pkexec exited {proc.returncode}")

    text = out.decode(errors="replace")
    if not text.strip():
        fail("priv_helper produced no output")
    # priv_helper.py already prints {"ok": ...} in the exact shape callers
    # expect — forward it rather than re-wrap it.
    sys.stdout.write(text)
