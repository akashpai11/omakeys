#!/usr/bin/env python3
"""Read aggregated keypress counts out of the heatmap daemon's SQLite db.

Usage: heatmap_query.py [days]  (default 1 = today only)
Prints one JSON line: {"total": N, "byKey": {code: count, ...}, "days": N}

Global, not per-device, by design: once keyd is active it merges every
physical keyboard's output into a single virtual device before anything
else can see it (confirmed empirically — `keyd monitor` and this daemon
both only ever observe "keyd virtual keyboard", never the originating
physical device), so per-device attribution isn't recoverable downstream
of keyd. The heatmap is a global "what you type" view, not "what this
specific board sees" — and once any remap is applied, it's the *logical*
(post-remap) key that gets counted, not the physical one under your finger.
"""
import json
import os
import sqlite3
import sys
import time

DB_PATH = os.path.expanduser("~/.config/omarchy/omakeys/heatmap.db")


def main():
    days = 1
    if len(sys.argv) > 1:
        try:
            days = max(1, int(sys.argv[1]))
        except ValueError:
            pass

    if not os.path.isfile(DB_PATH):
        print(json.dumps({"total": 0, "byKey": {}, "days": days}))
        return

    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - (days - 1) * 86400))
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT keycode, SUM(count) FROM keycounts WHERE date >= ? GROUP BY keycode",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    by_key = {code: total for code, total in rows}
    print(json.dumps({"total": sum(by_key.values()), "byKey": by_key, "days": days}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"total": 0, "byKey": {}, "error": str(exc)}))
