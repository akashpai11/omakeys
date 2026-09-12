"""Shared logic for invoking priv_helper.py under pkexec.

The core problem this solves: priv_helper.py lives inside the plugin's
own checkout, which the invoking user can write to at any time — so
pkexec can never be pointed at that path directly (an earlier version of
this file tried piping the checkout's bytes over stdin instead, which
only closed a *timing* window, not the actual trust question: content
read from a mutable path, however it's transported, is still unverified
content). The real fix has two parts:

1. Install priv_helper.py's current content to a location the invoking
   user cannot write to at all — root:root, 0644, under
   /usr/local/lib/omakeys/ (root-owned 0755 directories all the way up,
   verified below rather than assumed). Once installed, that copy is
   immune to checkout tampering for as long as it matches: nothing ever
   reads the mutable checkout path again until a plugin update actually
   changes priv_helper.py's content.

2. The install step itself is done by a tiny, fixed bootstrap — a Python
   string literal baked into *this* file, never written to its own disk
   path — passed to `pkexec python3 -I -c <bootstrap>`. Because it exists
   only as a literal inside already-running, already-trusted code, there
   is no separate path for it to be swapped at; it carries the same trust
   as the rest of this plugin's non-privileged logic, nothing more is
   needed. Its only job is to verify a *staged* copy of priv_helper.py by
   file descriptor (owner check, same as priv_helper.py's own data-file
   checks) *and* by a SHA-256 digest computed by the caller from the
   bytes it just read — so even a same-owner content swap of the staging
   file in the moment between writing it and the bootstrap reading it
   would produce a digest mismatch and be refused, not just a symlink
   swap. Only once verified does it get installed.

Every actual privileged operation (apply-keyd, grant-access) then points
pkexec directly at the fixed, installed path — a normal, safe path-based
invocation, because by this point the path genuinely isn't writable by
the invoking user any more.

`python3 -I` (isolated: no PYTHONPATH, no user site-packages) with an
explicit minimal environment is used for every pkexec call here, so
nothing about the caller's environment can influence what gets imported.
"""
import hashlib
import json
import os
import secrets
import signal
import stat
import subprocess
import sys

PKEXEC = "/usr/bin/pkexec"
PYTHON3 = "/usr/bin/python3"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PRIV_HELPER_PATH = os.path.join(SCRIPT_DIR, "priv_helper.py")

INSTALL_DIR = "/usr/local/lib/omakeys"
INSTALLED_HELPER_PATH = os.path.join(INSTALL_DIR, "priv_helper.py")

STAGING_DIR = os.path.expanduser("~/.config/omarchy/omakeys/staging")
MAX_HELPER_BYTES = 1 << 20  # generous; priv_helper.py is a few KB

# Only what python3 itself needs; nothing from the caller's own
# environment is passed through to any pkexec'd process below.
MINIMAL_ENV = {"PATH": "/usr/bin"}

# Never written to disk on its own — exists only as this string inside
# already-running, already-trusted code. Verifies a staged copy of the
# real helper by descriptor (O_NOFOLLOW + owner check) and by a digest
# computed by the caller before installing it somewhere the caller can no
# longer write to. Kept deliberately tiny: less surface, less reason to
# ever need to change it.
_BOOTSTRAP = """
import hashlib, os, stat, sys

staging_path, expected_digest, install_path = sys.argv[1], sys.argv[2], sys.argv[3]
uid = int(os.environ["PKEXEC_UID"])
max_bytes = int(sys.argv[4])

fd = os.open(staging_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
try:
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        sys.exit("staged helper is not a regular file")
    if st.st_uid != uid:
        sys.exit("staged helper is not owned by the invoking user")
    if st.st_size > max_bytes:
        sys.exit("staged helper too large")
    data = os.read(fd, max_bytes + 1)
    if len(data) > max_bytes:
        sys.exit("staged helper too large")
finally:
    os.close(fd)

if hashlib.sha256(data).hexdigest() != expected_digest:
    sys.exit("staged helper digest mismatch")

install_dir = os.path.dirname(install_path)
os.makedirs(install_dir, exist_ok=True, mode=0o755)
dir_st = os.stat(install_dir, follow_symlinks=False)
if not stat.S_ISDIR(dir_st.st_mode) or dir_st.st_uid != 0:
    sys.exit("install directory is not a root-owned real directory")

tmp_path = install_path + ".new"
fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
try:
    os.write(fd, data)
finally:
    os.close(fd)
os.chmod(tmp_path, 0o644)
os.replace(tmp_path, install_path)
"""


def fail(message):
    print(json.dumps({"ok": False, "error": message}))
    sys.exit(0)


def _run_pkexec(argv, timeout):
    # start_new_session=True + killpg on timeout, same reasoning as
    # priv_helper.py's own run_fixed: this can only interrupt pkexec
    # *before* it fully transitions to the elevated target (the kernel
    # correctly refuses to let the original unprivileged uid signal it
    # afterwards) — bounding the cleanup wait itself matters precisely
    # because that later case is where killpg silently does nothing.
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=MINIMAL_ENV,
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        fail(f"timed out after {timeout}s")
    return proc.returncode, out, err


def _installed_copy_matches(helper_source):
    try:
        with open(INSTALLED_HELPER_PATH, "rb") as f:
            installed = f.read()
    except OSError:
        return False
    # Compare against a real root-owned regular file, not e.g. a symlink
    # some other process planted at this path — os.stat here follows
    # symlinks on purpose: if it's not a genuine root-owned regular file,
    # treat it as "not installed" and let the bootstrap re-lay it down
    # properly (which itself only ever writes real files, never through
    # a pre-existing symlink, since its own writes use O_NOFOLLOW).
    try:
        st = os.stat(INSTALLED_HELPER_PATH)
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0:
        return False
    return installed == helper_source


def _install_helper(helper_source):
    os.makedirs(STAGING_DIR, exist_ok=True, mode=0o700)
    staging_path = os.path.join(STAGING_DIR, f"priv_helper.{secrets.token_hex(16)}.py")
    fd = os.open(staging_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        os.write(fd, helper_source)
    finally:
        os.close(fd)

    digest = hashlib.sha256(helper_source).hexdigest()
    try:
        returncode, out, err = _run_pkexec(
            [
                PKEXEC, PYTHON3, "-I", "-c", _BOOTSTRAP,
                staging_path, digest, INSTALLED_HELPER_PATH, str(MAX_HELPER_BYTES),
            ],
            timeout=60,
        )
    finally:
        try:
            os.unlink(staging_path)
        except OSError:
            pass

    if returncode != 0:
        detail = (err or out).decode(errors="replace").strip()
        fail(detail or f"helper install failed (pkexec exited {returncode})")


def run_priv_helper(mode, args, timeout):
    with open(PRIV_HELPER_PATH, "rb") as f:
        helper_source = f.read()

    if not _installed_copy_matches(helper_source):
        _install_helper(helper_source)

    returncode, out, err = _run_pkexec(
        [PKEXEC, PYTHON3, "-I", INSTALLED_HELPER_PATH, mode, *args],
        timeout=timeout,
    )

    if returncode != 0:
        detail = (err or out).decode(errors="replace").strip()
        fail(detail or f"pkexec exited {returncode}")

    text = out.decode(errors="replace")
    if not text.strip():
        fail("priv_helper produced no output")
    # priv_helper.py already prints {"ok": ...} in the exact shape callers
    # expect — forward it rather than re-wrap it.
    sys.stdout.write(text)
