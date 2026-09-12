#!/usr/bin/env python3
"""Probe a hidraw node for VIA/QMK's raw-HID protocol.

Usage: probe_via.py <hidraw-path>
Prints one JSON line: {"via": true, "protocolVersion": N} or {"via": false}.

VIA's wire format (see https://www.caniusevia.com/docs/specification and
QMK's `quantum/via.c`) is a fixed-size vendor HID report whose first byte
is a command id; GET_PROTOCOL_VERSION (0x01) is safe to probe blind since
it's read-only and every VIA-compatible board must answer it the same way,
echoing the command id back with a 2-byte big-endian version in bytes 1-2.

Two things aren't standardized and have to be brute-forced:
- report size: most boards use 32-byte reports, some (incl. newer QMK
  defaults) use 64.
- whether hidraw expects a leading 0x00 report-id byte before the payload
  (depends on whether the interface's report descriptor declares report
  IDs at all).

A `hidraw` node for a normal keyboard also carries ordinary keystroke
reports, so a naive "read whatever comes back" probe can misfire: a report
that happens to start with 0x01 (or arrives mid-handshake right after a
Bluetooth reconnect) can look like a match. Caught exactly this live —
a reconnect produced a bogus "protocol v0" hit. So this version: drains
anything already queued before probing, and after writing, keeps reading
past anything that doesn't look like a real reply (never mistaking a
stray keystroke for one) until the timeout budget for that shape runs out,
and rejects implausible version numbers (current VIA protocol versions are
single/low-double digits; 0 is not a valid version).
"""
import json
import os
import select
import sys
import time

VIA_GET_PROTOCOL_VERSION = 0x01
READ_TIMEOUT_S = 0.3
MIN_PLAUSIBLE_VERSION = 1
MAX_PLAUSIBLE_VERSION = 64

REPORT_SIZES = [32, 64]


def drain(fd):
    """Discard anything already sitting in the kernel's read buffer (queued
    keystrokes, LED-state reports, etc.) so it can't be mistaken for our
    probe's response."""
    while True:
        ready, _, _ = select.select([fd], [], [], 0)
        if not ready:
            return
        try:
            if not os.read(fd, 256):
                return
        except OSError:
            return


def looks_like_response(resp):
    if len(resp) < 3:
        return None
    # Response may or may not echo a leading report-id byte too; check both
    # alignments for the "byte0 == command id" anchor VIA guarantees.
    for offset in (0, 1):
        if len(resp) > offset + 2 and resp[offset] == VIA_GET_PROTOCOL_VERSION:
            version = (resp[offset + 1] << 8) | resp[offset + 2]
            if MIN_PLAUSIBLE_VERSION <= version <= MAX_PLAUSIBLE_VERSION:
                return version
    return None


def try_shape(fd, report_size, with_report_id_byte):
    drain(fd)

    payload = bytearray(report_size)
    payload[0] = VIA_GET_PROTOCOL_VERSION
    if with_report_id_byte:
        payload = bytearray([0x00]) + payload

    try:
        os.write(fd, bytes(payload))
    except OSError:
        return None

    deadline = time.monotonic() + READ_TIMEOUT_S
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            return None
        try:
            resp = os.read(fd, 256)
        except OSError:
            return None
        version = looks_like_response(resp)
        if version is not None:
            return version
        # Not our response (e.g. a keystroke landed in the same window) —
        # keep listening until the deadline instead of giving up on it.


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"via": False, "error": "usage: probe_via.py <hidraw-path>"}))
        return

    path = sys.argv[1]
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError as exc:
        print(json.dumps({"via": False, "error": str(exc)}))
        return

    try:
        for report_size in REPORT_SIZES:
            for with_id in (False, True):
                version = try_shape(fd, report_size, with_id)
                if version is not None:
                    print(json.dumps({
                        "via": True,
                        "protocolVersion": version,
                        "reportSize": report_size,
                        "reportIdByte": with_id,
                    }))
                    return
        print(json.dumps({"via": False}))
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"via": False, "error": str(exc)}))
