#!/usr/bin/env python3
"""Save a per-device remap profile and (re)apply it to keyd.

Usage: apply_profile.py <device-key> <base64-json-profile>

`device-key` is "<vendorId>:<productId>" (both hex, uppercase, no leading
0x) for USB/Bluetooth HID devices. The profile is a flat JSON object of
{ "<physical-key>": "<target-key>" }, both using keyd's canonical key names.

Two writes happen:
1. The profile itself, saved as plain JSON under the user's own config dir
   (~/.config/omarchy/keyboard-heatmap/profiles/) — no privilege needed,
   this is just app state for the panel to reload next time it opens.
2. The generated keyd config, which must land in /etc/keyd/ (root-owned).
   That happens through a single `pkexec` call that writes the file and
   triggers `keyd reload` together, so applying a remap costs one
   authentication prompt rather than one per step.

Prints a single JSON line: {"ok": true} or {"ok": false, "error": "..."}.
"""
import base64
import json
import os
import re
import subprocess
import sys

CONFIG_HOME = os.path.expanduser("~/.config/omarchy/keyboard-heatmap")
PROFILES_DIR = os.path.join(CONFIG_HOME, "profiles")
STAGING_DIR = os.path.join(CONFIG_HOME, "staging")

# vendorId:productId, both hex, as produced by detect_devices.py. Strict on
# purpose: device_key flows into a filename that a pkexec'd root command
# later writes to (/etc/keyd/<safe_name>.conf) — the QML UI only ever
# builds it from regex-validated hex fields already, but this script is
# the privileged boundary, so it re-validates rather than trusting the
# caller, closing off any path-traversal via "../" or "/" in the argument.
DEVICE_KEY_RE = re.compile(r"^[0-9A-Fa-f]{1,8}:[0-9A-Fa-f]{1,8}$")
# Every physical key our grid can click is lowercase-alnum (capslock, 1,
# leftcontrol, ...) — no symbol names on the physical side. Targets can be
# symbol names ("!", "["...), so they only get the injection-relevant ban:
# no newline/bracket/equals, which is what could smuggle extra config
# sections or key=value lines into the generated file.
PHYSICAL_KEY_RE = re.compile(r"^[a-z0-9]{1,32}$")
UNSAFE_TARGET_CHARS = re.compile(r"[\n\r\[\]=]")


def fail(message):
    print(json.dumps({"ok": False, "error": message}))
    sys.exit(0)


def build_keyd_config(device_key, profile):
    lines = ["[ids]", device_key, "", "[main]"]
    for physical, target in sorted(profile.items()):
        lines.append(f"{physical} = {target}")
    lines.append("")
    return "\n".join(lines)


def main():
    if len(sys.argv) != 3:
        fail("usage: apply_profile.py <device-key> <base64-json-profile>")

    device_key = sys.argv[1]
    if not DEVICE_KEY_RE.match(device_key):
        fail(f"invalid device key: {device_key!r}")

    try:
        profile = json.loads(base64.b64decode(sys.argv[2]).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        fail(f"invalid profile payload: {exc}")

    if not isinstance(profile, dict):
        fail("profile must be a JSON object of physical-key -> target-key")

    for physical, target in profile.items():
        if not PHYSICAL_KEY_RE.match(str(physical)):
            fail(f"invalid physical key: {physical!r}")
        if not isinstance(target, str) or not target or len(target) > 32 or UNSAFE_TARGET_CHARS.search(target):
            fail(f"invalid remap target for {physical!r}: {target!r}")

    os.makedirs(PROFILES_DIR, exist_ok=True, mode=0o700)
    os.makedirs(STAGING_DIR, exist_ok=True, mode=0o700)

    safe_name = device_key.replace(":", "_")
    profile_path = os.path.join(PROFILES_DIR, f"{safe_name}.json")
    staging_path = os.path.join(STAGING_DIR, f"{safe_name}.conf")

    with open(profile_path, "w") as f:
        json.dump(profile, f, indent=2)

    with open(staging_path, "w") as f:
        f.write(build_keyd_config(device_key, profile))

    result = subprocess.run(
        [
            "pkexec",
            "bash",
            "-c",
            'install -Dm644 "$1" "/etc/keyd/$2.conf" && keyd reload',
            "--",
            staging_path,
            safe_name,
        ],
        capture_output=True,
        text=True,
        timeout=120,
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
