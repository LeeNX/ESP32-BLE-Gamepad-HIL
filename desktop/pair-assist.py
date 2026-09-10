#!/usr/bin/env python3
"""Tell you which BLE device to pair, then wait for the board to see the bond.

macOS and Windows can't start a BLE-HID bond from an app -- you pair once by
hand in the OS Bluetooth settings. This reads the board over serial so you know
exactly which entry to click, then polls `CONN?` until the firmware reports the
link. No host BLE API, no permissions.

    python pair-assist.py --port /dev/cu.usbserial-110   # macOS
    python pair-assist.py --port COM5                    # Windows
    python pair-assist.py --port ... --clear-bonds       # drop stale bonds first
    python pair-assist.py --port ... --reconnect         # macOS: blueutil re-link (after a re-flash)
    python pair-assist.py --port ... --check             # just print state, don't wait

Falls back to $HIL_PORT for --port.
"""

import argparse
import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
from hil.serialdev import SerialDev, SerialError  # noqa: E402


def macos_reconnect(name):
    """macOS: Bluetooth power-cycle + `blueutil --connect` the paired entry
    named `name`. Re-establishes and refreshes the bond + cached HID descriptor
    after a firmware/profile change -- the case where the OS otherwise clings to
    a stale device. A never-before-paired board still needs one manual pair in
    System Settings (blueutil can't scan for BLE). Returns True if it tried.
    """
    if platform.system() != "Darwin" or not shutil.which("blueutil"):
        print("  (--reconnect needs macOS + `brew install blueutil`)")
        return False
    paired = subprocess.run(["blueutil", "--paired"], capture_output=True, text=True).stdout
    m = re.search(rf'address: ([0-9a-fA-F:-]+),[^\n]*name: "{re.escape(name)}"', paired)
    if not m:
        print(f"  blueutil: nothing paired as {name!r} yet -- pair it once in System Settings")
        return False
    addr = m.group(1)
    print(f"  blueutil: Bluetooth off/on + reconnect {addr} ...")
    subprocess.run(["blueutil", "--power", "0"], check=False)
    time.sleep(3)
    subprocess.run(["blueutil", "--power", "1"], check=False)
    time.sleep(4)
    # prints "Failed to connect" even when it works -- the board's Just Works
    # agent completes the bond a moment later; poll CONN? for the truth.
    subprocess.run(["blueutil", "--connect", addr], capture_output=True, check=False)
    time.sleep(3)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", default=os.environ.get("HIL_PORT"), help="hil_runner serial port")
    ap.add_argument(
        "--clear-bonds", action="store_true", help="CLEARBONDS on the board before pairing"
    )
    ap.add_argument(
        "--reconnect",
        action="store_true",
        help="macOS: blueutil BT power-cycle + reconnect (refreshes a stale bond after a re-flash)",
    )
    ap.add_argument("--check", action="store_true", help="print board state and exit (don't wait)")
    ap.add_argument(
        "--timeout", type=float, default=180.0, help="seconds to wait for the bond (default 180)"
    )
    args = ap.parse_args(argv)

    if not args.port:
        ap.error(
            "no --port (and $HIL_PORT unset). macOS: ls /dev/cu.usbserial-*  Windows: Get-PnpDevice -Class Ports"
        )

    d = SerialDev(args.port)
    try:
        d.wait_ready()
        fid = d.firmware_id()
        name = d.device_name()
        cfg = d.config()
        bonds = d.bonds()

        if not fid.startswith("ID hil_runner"):
            print(f"!! {args.port} is not running hil_runner -- got {fid!r}")
            print("   Flash a bundle first (builder/build.sh + tester/flash.py).")
            return 2

        print(f"board     : {fid.split('ID ', 1)[1]}")
        print(
            f"profile   : {cfg.get('profile')}  ({cfg.get('buttons')} btn / {cfg.get('hats')} hat / {cfg.get('axes')})"
        )
        print(f"advertises : {name}   <-- pair with THIS")
        print(f"stored bonds: {len(bonds)}{' ' + ', '.join(bonds) if bonds else ''}")

        if args.clear_bonds and bonds:
            cleared, remaining = d.clear_bonds()
            print(
                f"cleared {cleared} bond(s) on the board (remaining: {remaining}). "
                f"Also remove it on this computer:"
            )
            print(
                "  macOS: System Settings > Bluetooth > (i) next to the device > Forget This Device"
            )
            print("  Windows: Settings > Bluetooth & devices > device > Remove device")

        if d.connected():
            peer = d.peer_info()
            print(f"\nalready connected: interval {peer['interval_ms']:.1f} ms, MTU {peer['mtu']}")
            return 0

        if args.check:
            return 0

        if args.reconnect:
            print()
            macos_reconnect(name)

        print()
        print(f"Now pair '{name}' in your OS Bluetooth settings.")
        print("  Ignore 'HILpad esp32dev / esp32c3 / esp32s3'  -- that's the reference rig.")
        print(
            "  Ignore 'ESP32 BLE Gamepad'                    -- a board on stock library firmware."
        )
        print(
            f"  If '{name}' isn't listed: unplug/replug the board, or wait for the scan to refresh."
        )
        print()

        deadline = time.time() + args.timeout
        while time.time() < deadline:
            try:
                if d.connected():
                    peer = d.peer_info()
                    print(
                        f"\npaired. interval {peer['interval_ms']:.1f} ms, latency {peer['latency']}, MTU {peer['mtu']}"
                    )
                    return 0
            except SerialError:
                pass
            left = int(deadline - time.time())
            print(f"\r  waiting for the bond... {left:3d}s  (CONN 0)", end="", flush=True)
            time.sleep(1.0)
        print("\n\ntimed out waiting for the bond.")
        return 1
    finally:
        d.close()


if __name__ == "__main__":
    sys.exit(main())
