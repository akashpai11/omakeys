#!/usr/bin/env python3
"""One-time privileged setup: group membership + the VIA udev rule.

VIA/QMK raw-HID detection and the keypress heatmap both need access the
current user doesn't have by default:
  - the `keyd` and `input` groups (heatmap reads raw evdev, and some distros
    gate hidraw through group membership as well as udev)
  - a udev rule tagging hidraw nodes `uaccess` so the logged-in seat user
    can open them without root (the same rule VIA/Vial ship themselves)

Both changes happen through a single `pkexec` call so this costs one
authentication prompt, not one per step. Group membership only takes
effect for a session started *after* this runs (a `usermod` change is
read at PAM login, not by processes already running) — the udev rule
takes effect immediately via `udevadm trigger`.

Prints a single JSON line: {"ok": true} or {"ok": false, "error": "..."}.
"""
import getpass
import json
import os
import re
import subprocess
import sys

UDEV_RULE_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "udev", "70-omakeys-via.rules")
UDEV_RULE_DEST = "/etc/udev/rules.d/70-omakeys-via.rules"

# POSIX username rules; this also flows into a shell -c pkexec argument, so
# it's re-validated here rather than trusted from the environment.
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def fail(message):
    print(json.dumps({"ok": False, "error": message}))
    sys.exit(0)


def main():
    user = getpass.getuser()
    if not USERNAME_RE.match(user):
        fail(f"unsafe username: {user!r}")

    rule_src = os.path.abspath(UDEV_RULE_SRC)
    if not os.path.isfile(rule_src):
        fail(f"udev rule missing from plugin install: {rule_src}")

    result = subprocess.run(
        [
            "pkexec",
            "bash",
            "-c",
            'usermod -aG keyd,input "$1" '
            '&& install -Dm644 "$2" "$3" '
            "&& udevadm control --reload-rules "
            "&& udevadm trigger",
            "--",
            user,
            rule_src,
            UDEV_RULE_DEST,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        fail(detail or f"pkexec exited {result.returncode}")

    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        fail(str(exc))
