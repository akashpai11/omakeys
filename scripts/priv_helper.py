#!/usr/bin/env python3
"""The only code in this plugin that ever runs as root.

This file itself lives inside the plugin's own checkout, which the
invoking user can write to — so pkexec must never be pointed at *this*
path directly in production. Piping these bytes over stdin only closes
the *timing* window (the file could still have been corrupted at any
earlier, non-racing moment, with nothing left to detect it); the actual
fix is that priv_invoke.py installs a copy of this file to a location the
invoking user cannot write to at all (root-owned, root:root 0644, under
/usr/local/lib/omakeys/), using a tiny fixed bootstrap that hash-verifies
the staged content before installing it — see priv_invoke.py's docstring
for the full mechanism. Every actual privileged operation then points
pkexec at that fixed, install-owned path, which the checkout's mutability
has no bearing on at all. (This file can still be run directly by path
for local testing — `python3 priv_helper.py apply-keyd ...` — that's just
not part of the trust boundary in production.)

Three more things a naive "pkexec bash -c 'install ...'" one-liner gets
wrong, all closed here:

1. Binary resolution. A privileged process that resolves `bash`, `install`,
   `keyd`, `usermod`, `udevadm` through PATH trusts whatever PATH the
   polkit-elevated environment happens to hand it. Every path below is a
   fixed absolute constant instead, and priv_invoke.py runs this script
   with a minimal, explicit environment rather than inheriting the
   caller's.

2. TOCTOU on the *data* files it reads/writes (the keyd config, the udev
   rule). Validating a path as the unprivileged caller and then having a
   *separate* root process open that same path by name leaves a window
   where the path can be swapped (e.g. replaced with a symlink) between
   the two steps — root would then read or write through whatever the
   symlink now points at. Closed by opening every source with O_NOFOLLOW
   and checking it's a regular file owned by the account pkexec itself
   vouches for (`PKEXEC_UID`, not a caller argument), and opening every
   destination with O_NOFOLLOW too, so a pre-planted symlink at a
   destination path is refused rather than followed. The file is read
   from the already-open descriptor, so nothing about its identity can
   change again after the check.

3. Runaway descendants. The fixed commands this script runs (`keyd`,
   `usermod`, `udevadm`) run in their own process group; a timeout kills
   the whole group, not just the immediate pid, so a hung descendant
   can't outlive the command that spawned it.

Prints a single JSON line: {"ok": true} or {"ok": false, "error": "..."}.
Always exits 0 — callers read the JSON to learn success/failure, the same
convention every other helper in this plugin uses.
"""
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys

KEYD = "/usr/bin/keyd"
USERMOD = "/usr/bin/usermod"
UDEVADM = "/usr/bin/udevadm"

KEYD_DEST_DIR = "/etc/keyd"
UDEV_RULES_DIR = "/etc/udev/rules.d"
UDEV_RULE_DEST = os.path.join(UDEV_RULES_DIR, "70-omakeys-via.rules")

MAX_SOURCE_BYTES = 64 * 1024
SUBPROCESS_TIMEOUT_S = 30

SAFE_NAME_RE = re.compile(r"^[0-9A-Fa-f]{1,8}_[0-9A-Fa-f]{1,8}$")


def fail(message):
    print(json.dumps({"ok": False, "error": str(message)}))
    sys.exit(0)


def invoking_uid():
    # Set by pkexec itself to the uid of the process that invoked it — not
    # something a caller-supplied argument could spoof.
    raw = os.environ.get("PKEXEC_UID")
    if raw is None:
        fail("not invoked via pkexec (PKEXEC_UID unset)")
    try:
        return int(raw)
    except ValueError:
        fail("invalid PKEXEC_UID")


def read_verified_source(path, expected_uid):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        fail(f"cannot open source: {exc}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            fail("source is not a regular file")
        if st.st_uid != expected_uid:
            fail("source is not owned by the invoking user")
        if st.st_size > MAX_SOURCE_BYTES:
            fail("source file too large")
        data = os.read(fd, MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            fail("source file too large")
        return data
    finally:
        os.close(fd)


def write_verified_dest(path, data, mode):
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC,
        mode,
    )
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def run_fixed(argv):
    # start_new_session=True makes this child its own process-group leader,
    # so a timeout can kill the whole group — including any descendant the
    # command itself spawned — rather than just its immediate pid. Both
    # sides of that kill are root here, so there's no permission boundary
    # in the way (unlike trying to kill an already-elevated pkexec target
    # from the original unprivileged caller, which the kernel refuses).
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=SUBPROCESS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Bounded even on the cleanup path — a communicate() with no
        # timeout here would silently reintroduce the exact unboundedness
        # this whole function exists to prevent, in precisely the case
        # (a wedged descendant) where it matters most.
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        fail(f"{argv[0]} timed out after {SUBPROCESS_TIMEOUT_S}s")

    if proc.returncode != 0:
        detail = (err.decode(errors="replace") or out.decode(errors="replace")).strip()
        fail(detail or f"{argv[0]} exited {proc.returncode}")


def cmd_apply_keyd(args):
    if len(args) != 2:
        fail("usage: apply-keyd <safe_name> <staging_path>")
    safe_name, staging_path = args
    if not SAFE_NAME_RE.match(safe_name):
        fail(f"invalid device name: {safe_name!r}")

    data = read_verified_source(staging_path, invoking_uid())
    os.makedirs(KEYD_DEST_DIR, exist_ok=True, mode=0o755)
    dest = os.path.join(KEYD_DEST_DIR, f"{safe_name}.conf")
    write_verified_dest(dest, data, 0o644)
    run_fixed([KEYD, "reload"])
    print(json.dumps({"ok": True}))


def cmd_grant_access(args):
    if len(args) != 1:
        fail("usage: grant-access <udev_rule_src_path>")
    (rule_src,) = args

    uid = invoking_uid()
    data = read_verified_source(rule_src, uid)
    user = pwd.getpwuid(uid).pw_name

    run_fixed([USERMOD, "-aG", "keyd,input", user])
    os.makedirs(UDEV_RULES_DIR, exist_ok=True, mode=0o755)
    write_verified_dest(UDEV_RULE_DEST, data, 0o644)
    run_fixed([UDEVADM, "control", "--reload-rules"])
    run_fixed([UDEVADM, "trigger"])
    print(json.dumps({"ok": True}))


def main():
    if len(sys.argv) < 2:
        fail("usage: priv_helper.py <apply-keyd|grant-access> ...")
    mode, rest = sys.argv[1], sys.argv[2:]
    if mode == "apply-keyd":
        cmd_apply_keyd(rest)
    elif mode == "grant-access":
        cmd_grant_access(rest)
    else:
        fail(f"unknown mode: {mode!r}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        fail(str(exc))
