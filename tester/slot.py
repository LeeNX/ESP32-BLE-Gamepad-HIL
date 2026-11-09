#!/usr/bin/env python3
"""TESTER role: inspect and switch the OTA app slots of a board flashed with a multi-slot bundle
(builder/make_multislot.py; PoC, docs/multislot-ota.md).

    tester/slot.py <multislot_bundle> --port /dev/ttyUSB0 list
    tester/slot.py <multislot_bundle> --port /dev/ttyUSB0 verify          # every slot's sha256 == the manifest
    tester/slot.py <multislot_bundle> --port /dev/ttyUSB0 boot 2 [--wipe] # switch, then check what runs
    tester/slot.py <multislot_bundle> --port P boot 1 --via esptool [--flash-port FP]

`boot` via serial (default) asks the running firmware (`BOOT <n> [WIPE]`, hil_runner and the observer alike) to set
the boot slot and restart -- nothing goes over USB but a command line; the chip writes one 4 KB otadata sector.
`--via esptool` writes the bundle's otadata for slot n (8 KB) with esptool instead, for a slot whose firmware doesn't
answer; it can't wipe NVS. Either way the board is then asked `SLOTS?` again and must report: running == n, and every
slot's image sha256 == the manifest's (the chip digests its own flash -- no read-back over USB).
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import time

import serial

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from flash import SLOW_CHIPS, esptool_cmd, flash_lock  # noqa: E402

REPLY = re.compile(r"^(?:HIL )?(slots|ok|err)\b ?(.*)$", re.IGNORECASE)
SLOT = re.compile(r"^(\d+)=(.*)$")


class Board:
    def __init__(self, port):
        # DTR/RTS deasserted before opening: on an auto-reset UART bridge, opening must not reset the chip.
        self.ser = serial.Serial()
        self.ser.port, self.ser.baudrate, self.ser.timeout = port, 115200, 0.2
        self.ser.dtr = self.ser.rts = False
        self.ser.open()

    def command(self, line, timeout=6.0):
        """Send one line; return (kind, rest) of the first SLOTS / OK / ERR reply (either firmware's form)."""
        self.ser.reset_input_buffer()
        self.ser.write((line + "\n").encode())
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = self.ser.readline().decode(errors="replace").strip()
            m = REPLY.match(got)
            if m:
                return m.group(1).lower(), m.group(2)
        return None, None

    def slots(self, timeout=6.0, retries=3):
        """Parsed SLOTS? reply, or None. An ERR is retried too: a command sent while the chip is still booting
        (just reset by esptool, flash.py's verify, a BOOT) arrives garbled and gets ERR unknown -- seen on the rig.
        Only an ERR on every try means firmware without SLOTS?."""
        err = None
        for i in range(retries):
            if i:
                time.sleep(1.0)
            kind, rest = self.command("SLOTS?", timeout)
            if kind == "slots":
                return parse_slots(rest)
            if kind == "err":
                err = rest
        if err is not None:
            raise SystemExit(f"SLOTS? -> ERR {err} (firmware without multi-slot support?)")
        return None


def parse_slots(rest):
    out = {"slots": {}}
    for tok in rest.split():
        m = SLOT.match(tok)
        if m:
            idx, val = int(m.group(1)), m.group(2)
            if val == "empty":
                out["slots"][idx] = None
            else:
                sha, state, proj, ver = (val.split("/") + ["", "", ""])[:4]
                out["slots"][idx] = {"sha256": sha, "state": state, "project": proj, "version": ver}
        elif "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = int(v) if v.lstrip("-").isdigit() else v
    return out


def compare(state, manifest):
    """Lines describing each slot vs the manifest; returns (lines, all_match)."""
    lines, ok = [], True
    for s in manifest["slots"]:
        got = state["slots"].get(s["slot"])
        match = bool(got) and got["sha256"] == s["image_sha256"]
        ok &= match
        mark = "ok " if match else "BAD"
        run = "*" if state.get("running") == s["slot"] else " "
        boot = "boot" if state.get("boot") == s["slot"] else "    "
        dev = f"{got['sha256'][:16]} {got['state']}" if got else "empty"
        lines.append(
            f" {run} {boot} slot {s['slot']} [{mark}] {s['role']:8} {s['profile']:9} "
            f"want {s['image_sha256'][:16]} got {dev}"
        )
    extra = set(state["slots"]) - {s["slot"] for s in manifest["slots"]}
    for i in sorted(extra):
        lines.append(
            f"        slot {i} (not in the bundle) {'empty' if not state['slots'][i] else 'occupied'}"
        )
    return lines, ok


def esptool_otadata(manifest, bundle, slot, port, chip, baud):
    """Write the otadata selecting <slot> (make_multislot.otadata_bin) with esptool, then hard-reset."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "builder"))
    from make_multislot import otadata_bin

    with tempfile.NamedTemporaryFile(suffix=".bin") as f:
        f.write(otadata_bin(slot))
        f.flush()
        flash_lock()
        cmd = esptool_cmd() + ["--chip", chip, "--port", port, "--baud", str(baud)]
        r = subprocess.run(cmd + ["write_flash", manifest["otadata_offset"], f.name])
        return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("bundle", type=pathlib.Path)
    ap.add_argument(
        "--port", required=True, help="the firmware's console (hil_config [board.*].port)"
    )
    ap.add_argument(
        "--flash-port", default="", help="esptool port when it differs (C3/S3 native USB)"
    )
    ap.add_argument(
        "--baud", type=int, default=None, help="esptool baud (default: flash.py's per chip)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("verify")
    b = sub.add_parser("boot")
    b.add_argument("slot", type=int)
    b.add_argument(
        "--wipe", action="store_true", help="erase NVS (BLE bonds) too -- for a role change"
    )
    b.add_argument("--via", choices=("serial", "esptool"), default="serial")
    b.add_argument("--boot-timeout", type=float, default=20.0)
    args = ap.parse_args()

    manifest = json.loads((args.bundle / "manifest.json").read_text())
    if "slots" not in manifest:
        raise SystemExit(f"{args.bundle}: not a multi-slot bundle (no slots in manifest.json)")
    chip = manifest["chip"]
    baud = args.baud or (115200 if chip in SLOW_CHIPS else 460800)
    board = Board(args.port)

    state = board.slots()
    if state is None:
        raise SystemExit("no SLOTS reply -- firmware not running, or without multi-slot support")

    if args.cmd in ("list", "verify"):
        lines, ok = compare(state, manifest)
        print(f"running={state.get('running')} boot={state.get('boot')} count={state.get('count')}")
        print("\n".join(lines))
        return 0 if ok or args.cmd == "list" else 1

    if not any(s["slot"] == args.slot for s in manifest["slots"]):
        raise SystemExit(f"slot {args.slot} is not in the bundle")
    want = next(s for s in manifest["slots"] if s["slot"] == args.slot)
    got = state["slots"].get(args.slot)
    if not got or got["sha256"] != want["image_sha256"]:
        # Don't boot a slot whose contents aren't what the bundle says -- reflash instead.
        raise SystemExit(
            f"slot {args.slot} on the chip is {got['sha256'][:16] if got else 'empty'}, "
            f"bundle wants {want['image_sha256'][:16]}: reflash the bundle (tester/flash.py)"
        )

    t0 = time.monotonic()
    if args.via == "serial":
        kind, rest = board.command(f"BOOT {args.slot}{' WIPE' if args.wipe else ''}")
        if kind != "ok":
            raise SystemExit(f"BOOT {args.slot} -> {kind or 'no reply'} {rest or ''}")
    else:
        if args.wipe:
            raise SystemExit("--wipe needs --via serial (or tester/flash.py --wipe-settings)")
        board.ser.close()
        if not esptool_otadata(
            manifest, args.bundle, args.slot, args.flash_port or args.port, chip, baud
        ):
            raise SystemExit("esptool otadata write failed")
        board = Board(args.port)

    # Poll until the new image answers. SLOTS? digests every slot, so give each try a few seconds; an ERR is a
    # command garbled by the reboot, not a verdict -- keep polling.
    after = None
    while time.monotonic() - t0 < args.boot_timeout:
        time.sleep(1.0)
        kind, rest = board.command("SLOTS?", 5.0)
        if kind == "slots":
            after = parse_slots(rest)
            if after.get("running") == args.slot:
                break
    took = time.monotonic() - t0
    if not after:
        raise SystemExit(f"no SLOTS reply within {args.boot_timeout:.0f}s of the switch")
    lines, ok = compare(after, manifest)
    print(
        f"boot {args.slot} via {args.via}{' +wipe' if args.wipe else ''}: answered in {took:.1f}s"
    )
    print(f"running={after.get('running')} boot={after.get('boot')} count={after.get('count')}")
    print("\n".join(lines))
    if after.get("running") != args.slot:
        print(f"FAIL: running slot {after.get('running')}, wanted {args.slot}", file=sys.stderr)
        return 1
    if not ok:
        print("FAIL: a slot's sha256 doesn't match the bundle", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
