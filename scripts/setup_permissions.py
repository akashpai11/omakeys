#!/usr/bin/env python3
"""One-time privileged setup: group membership + the VIA udev rule.

VIA/QMK raw-HID detection and the keypress heatmap both need access the
current user doesn't have by default:
  - the `keyd` and `input` groups (heatmap reads raw evdev, and some distros
    gate hidraw through group membership as well as udev)
  - a udev rule tagging hidraw nodes `uaccess` so the logged-in seat user
    can open them without root (the same rule VIA/Vial ship themselves)

Both changes happen through a single `pkexec` call into priv_helper.py
(via priv_invoke.py, which pipes it in rather than pointing pkexec at a
path — see that module's docstring), so this costs one authentication
prompt, not one per step. priv_helper.py reads the invoking username from
PKEXEC_UID itself (not a caller argument) and only ever touches fixed,
absolute-path binaries. Group membership only takes effect for a session
started *after* this runs (a `usermod` change is read at PAM login, not
by processes already running) — the udev rule takes effect immediately
via `udevadm trigger`.

Prints a single JSON line: {"ok": true} or {"ok": false, "error": "..."}.
"""
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import priv_invoke  # noqa: E402

UDEV_RULE_SRC = os.path.join(SCRIPT_DIR, "..", "udev", "70-omakeys-via.rules")


def fail(message):
    print(json.dumps({"ok": False, "error": message}))
    sys.exit(0)


def main():
    rule_src = os.path.abspath(UDEV_RULE_SRC)
    if not os.path.isfile(rule_src):
        fail(f"udev rule missing from plugin install: {rule_src}")

    priv_invoke.run_priv_helper("grant-access", [rule_src], timeout=60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        fail(str(exc))
