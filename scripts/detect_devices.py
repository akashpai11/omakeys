#!/usr/bin/env python3
"""Enumerate physical keyboards visible to udev and report them as JSON.

Reads `udevadm info -e` once (a full export of the udev device database),
groups records into blocks separated by blank lines, and keeps the ones that
are USB or Bluetooth keyboards (ID_INPUT_KEYBOARD=1, ID_BUS set). For each
match it also looks for a sibling hidraw node so higher layers (VIA/QMK
detection, polling-rate readout) know whether raw HID access is possible.

Deliberately read-only: this script never writes device state, only reports
what udev already knows.
"""
import json
import os
import re
import shutil
import subprocess
import sys

# USB HID interrupt-endpoint bInterval -> polling rate. This is the
# *configured* rate (what host and device negotiated), not a measured one
# — cheap and instant, but only meaningful over USB. Over Bluetooth there's
# no USB endpoint to read, so callers get null and should show "not
# available over Bluetooth" rather than guessing.
BINTERVAL_HZ = {1: 1000, 2: 500, 4: 250, 8: 125}

CONNECTION_LABELS = {"usb": "Wired", "bluetooth": "Bluetooth", "i8042": "Built-in"}


def polling_hz_for(devpath):
    """Walk up from the HID device's sysfs path looking for the USB
    interface directory (the one with a bInterval file) and convert it to
    Hz. Best-effort: returns None on anything unexpected rather than
    raising — this is a nice-to-have readout, never worth crashing
    detection over.
    """
    try:
        current = "/sys" + devpath
        for _ in range(8):
            candidate = os.path.join(current, "bInterval")
            if os.path.isfile(candidate):
                with open(candidate) as f:
                    raw = f.read().strip()
                interval = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
                return BINTERVAL_HZ.get(interval)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    except (OSError, ValueError):
        return None
    return None


def parse_udevadm(raw):
    blocks = raw.split("\n\n")
    records = []
    for block in blocks:
        if not block.strip():
            continue
        env = {}
        devpath = ""
        for line in block.splitlines():
            if line.startswith("P: "):
                devpath = line[3:].strip()
            elif line.startswith("E: "):
                kv = line[3:].split("=", 1)
                if len(kv) == 2:
                    env[kv[0]] = kv[1]
        if devpath:
            records.append({"devpath": devpath, "env": env})
    return records


def run_udevadm():
    try:
        out = subprocess.run(
            ["udevadm", "info", "-e"], capture_output=True, text=True, timeout=5
        )
        return out.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def hid_id_from_env(env):
    # HID_ID looks like "0005:000019F5:00003246" (bus:vendor:product), all hex.
    hid_id = env.get("HID_ID", "")
    m = re.match(r"^[0-9A-Fa-f]+:0*([0-9A-Fa-f]+):0*([0-9A-Fa-f]+)$", hid_id)
    if not m:
        return None, None
    return m.group(1).upper(), m.group(2).upper()


def main():
    raw = run_udevadm()
    records = parse_udevadm(raw)

    # First pass: index hidraw nodes and hid parents by their devpath prefix,
    # so we can attach "/dev/hidrawN" to the matching keyboard entry below.
    hidraw_by_prefix = {}
    hid_env_by_prefix = {}
    for rec in records:
        devpath = rec["devpath"]
        env = rec["env"]
        if "/hidraw/" in devpath and env.get("DEVNAME"):
            prefix = devpath.split("/hidraw/")[0]
            hidraw_by_prefix[prefix] = env["DEVNAME"]
        if env.get("HID_ID") and "/input/" not in devpath and "/hidraw/" not in devpath:
            hid_env_by_prefix[devpath] = env

    keyboards = []
    seen_phys = set()
    for rec in records:
        env = rec["env"]
        if env.get("ID_INPUT_KEYBOARD") != "1":
            continue
        if env.get("SUBSYSTEM") != "input":
            continue
        # A device's input node splits into several sub-nodes (the keyboard
        # itself, a ::capslock LED node, sometimes a consumer-control node);
        # only the one carrying NAME is the actual keyboard we want to show.
        name = env.get("NAME", "").strip('"')
        if not name:
            continue
        # Real keyboards carry ID_BUS (usb/bluetooth/i8042); virtual/software
        # input nodes (xdotool, on-screen keyboards, etc.) generally don't.
        bus = env.get("ID_BUS", "")
        if not bus:
            continue
        phys = env.get("PHYS", "").strip('"') or rec["devpath"]
        if phys in seen_phys:
            continue
        seen_phys.add(phys)

        hid_prefix = rec["devpath"].split("/input/")[0]
        hid_env = hid_env_by_prefix.get(hid_prefix, {})
        vendor_id, product_id = hid_id_from_env(hid_env)
        hidraw = hidraw_by_prefix.get(hid_prefix)

        keyboards.append(
            {
                "name": name,
                "phys": phys,
                "bus": bus,
                "connectionLabel": CONNECTION_LABELS.get(bus, bus),
                "vendorId": vendor_id,
                "productId": product_id,
                "hidraw": hidraw,
                "integration": env.get("ID_INTEGRATION", "external"),
                "pollingHz": polling_hz_for(hid_prefix) if bus == "usb" else None,
            }
        )

    # Built-in laptop keyboards report ID_INTEGRATION=internal; surface them
    # too but let the UI de-emphasize them since they're not swappable.
    keyboards.sort(key=lambda k: (k["integration"] == "internal", k["name"]))

    keyd_path = shutil.which("keyd")
    keyd_active = False
    if keyd_path:
        try:
            status = subprocess.run(
                ["systemctl", "is-active", "keyd"], capture_output=True, text=True, timeout=3
            )
            keyd_active = status.stdout.strip() == "active"
        except (OSError, subprocess.TimeoutExpired):
            keyd_active = False

    print(
        json.dumps(
            {
                "schemaVersion": 1,
                "keyboards": keyboards,
                "keyd": {"installed": keyd_path is not None, "active": keyd_active},
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - surface any failure as JSON, not a traceback
        print(json.dumps({"schemaVersion": 1, "keyboards": [], "error": str(exc)}), file=sys.stdout)
        sys.exit(0)
