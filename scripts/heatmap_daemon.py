#!/usr/bin/env python3
"""Long-running keypress counter for the heatmap feature.

Privacy, by construction (see README):
- Counts keycodes only. Never records a sequence of keys, never stores a
  timestamp finer than "which day", and never writes anything that could
  reconstruct what was typed.
- Polls the shell's lock status every ~2s and drops every event while the
  session is locked.
- Honest limitation, not solved here: this reads raw evdev key-down events,
  which happen *before* any application-level knowledge of "this is a
  password field" exists. There is no reliable way to detect that at this
  layer without deep per-toolkit integration, so it is not attempted —
  counting silently continues through password fields in unlocked apps.
  Documented so nobody assumes otherwise.

Storage: SQLite at ~/.config/omarchy/keyboard-heatmap/heatmap.db, one row
per (date, device-phys, keycode), incremented in place — never event-level
data, only running totals.
"""
import json
import os
import select
import sqlite3
import struct
import subprocess
import sys
import time

EVENT_FMT = "<qqHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EV_KEY = 1
KEY_DOWN = 1

RESCAN_INTERVAL_S = 5
LOCK_POLL_INTERVAL_S = 2
FLUSH_INTERVAL_S = 5

CONFIG_HOME = os.path.expanduser("~/.config/omarchy/keyboard-heatmap")
DB_PATH = os.path.join(CONFIG_HOME, "heatmap.db")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(SCRIPT_DIR, "evdev_to_keyd.json")) as f:
    EVDEV_TO_KEYD = {int(k): v for k, v in json.load(f).items()}


def log(msg):
    print(f"[heatmap_daemon] {msg}", file=sys.stderr, flush=True)


def ensure_db():
    os.makedirs(CONFIG_HOME, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS keycounts (
            date TEXT NOT NULL,
            phys TEXT NOT NULL,
            keycode TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (date, phys, keycode)
        )"""
    )
    conn.commit()
    return conn


def discover_keyboards():
    """Same read-only udev enumeration approach as detect_devices.py, kept
    intentionally separate/simple here since this only needs devpath+phys,
    not the richer fields the UI wants."""
    try:
        out = subprocess.run(
            ["udevadm", "info", "-e"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}

    devices = {}
    devpath = ""
    env = {}
    for line in out.split("\n") + [""]:
        if line == "":
            if devpath and env.get("ID_INPUT_KEYBOARD") == "1" and env.get("DEVNAME", "").startswith("/dev/input/event"):
                phys = env.get("PHYS", "").strip('"') or devpath
                devices[env["DEVNAME"]] = phys
            devpath, env = "", {}
            continue
        if line.startswith("P: "):
            devpath = line[3:].strip()
        elif line.startswith("E: "):
            kv = line[3:].split("=", 1)
            if len(kv) == 2:
                env[kv[0]] = kv[1]
    return devices


def is_locked(cache):
    now = time.monotonic()
    if now - cache["checkedAt"] < LOCK_POLL_INTERVAL_S:
        return cache["locked"]
    try:
        out = subprocess.run(
            ["omarchy-shell", "-q", "lock", "status"],
            capture_output=True, text=True, timeout=2,
        ).stdout
        status = json.loads(out) if out.strip() else {}
        cache["locked"] = bool(status.get("locked") or status.get("sessionLocked"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass  # keep previous value — never fail open into "always counting"
    cache["checkedAt"] = now
    return cache["locked"]


def main():
    conn = ensure_db()
    open_fds = {}  # fd -> (devname, phys)
    lock_cache = {"locked": False, "checkedAt": 0.0}
    deltas = {}  # (date, phys, keyd_name) -> count
    last_rescan = 0.0
    last_flush = time.monotonic()

    def rescan():
        nonlocal last_rescan
        last_rescan = time.monotonic()
        current = discover_keyboards()
        known_devnames = {v[0] for v in open_fds.values()}
        for devname, phys in current.items():
            if devname in known_devnames:
                continue
            try:
                fd = os.open(devname, os.O_RDONLY | os.O_NONBLOCK)
                open_fds[fd] = (devname, phys)
                log(f"watching {devname} ({phys})")
            except OSError as exc:
                log(f"cannot open {devname}: {exc}")

    def flush():
        nonlocal deltas, last_flush
        last_flush = time.monotonic()
        if not deltas:
            return
        with conn:
            for (date, phys, keycode), count in deltas.items():
                conn.execute(
                    """INSERT INTO keycounts (date, phys, keycode, count) VALUES (?, ?, ?, ?)
                       ON CONFLICT(date, phys, keycode) DO UPDATE SET count = count + excluded.count""",
                    (date, phys, keycode, count),
                )
        deltas = {}

    rescan()
    log(f"started, db={DB_PATH}")

    try:
        while True:
            ready, _, _ = select.select(list(open_fds.keys()), [], [], 1.0)
            for fd in ready:
                devname, phys = open_fds[fd]
                try:
                    raw = os.read(fd, EVENT_SIZE * 64)
                except OSError:
                    log(f"lost {devname}, will retry on next rescan")
                    del open_fds[fd]
                    os.close(fd)
                    continue
                if is_locked(lock_cache):
                    continue
                for i in range(0, len(raw) - EVENT_SIZE + 1, EVENT_SIZE):
                    _, _, ev_type, ev_code, ev_value = struct.unpack_from(EVENT_FMT, raw, i)
                    if ev_type != EV_KEY or ev_value != KEY_DOWN:
                        continue
                    keyd_name = EVDEV_TO_KEYD.get(ev_code)
                    if not keyd_name:
                        continue
                    date = time.strftime("%Y-%m-%d")
                    key = (date, phys, keyd_name)
                    deltas[key] = deltas.get(key, 0) + 1

            now = time.monotonic()
            if now - last_rescan >= RESCAN_INTERVAL_S:
                rescan()
            if now - last_flush >= FLUSH_INTERVAL_S:
                flush()
    finally:
        flush()
        for fd in list(open_fds.keys()):
            os.close(fd)
        conn.close()


if __name__ == "__main__":
    main()
