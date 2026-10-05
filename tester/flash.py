#!/usr/bin/env python3
"""TESTER role: flash a firmware bundle (builder/make_bundle.py output) with
nothing but esptool. No PlatformIO, no toolchain -- fine on a Raspberry Pi.

    tester/flash.py <bundle_dir> --port /dev/ttyUSB0
"""

import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

# Native USB-Serial/JTAG (C3/S3) drops packets above this; a UART bridge is fine
# faster but 115200 is universally safe.
SLOW_CHIPS = {"esp32c3", "esp32s3", "esp32c6", "esp32h2"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# One flash at a time across the whole rig (CI lanes, tester/test-matrix.sh, manual runs). On the reference
# Raspberry Pi 3B+ every USB port and the Ethernet share one dwc_otg controller, and parallel flashing has wedged
# it (dwc_otg_hcd_urb_dequeue timeouts, Ethernet and USB serial dropping off -- 2026-09-11, 2026-10-04). Serial
# I/O and BLE can stay concurrent; esptool's bulk transfers can't. $HIL_FLASH_LOCK overrides the lock file.
FLASH_LOCK = pathlib.Path(
    os.environ.get("HIL_FLASH_LOCK")
    or pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache")
    / "esp32-hil"
    / "flash.lock"
)


# NVS / otadata / phy on both layouts the rig flashes (hil_runner: nvs 0x9000+0x5000, otadata 0xe000; the
# Bluepad32 observer: nvs 0x9000+0x6000, otadata 0xf000, phy 0x11000), up to the first app offset.
SETTINGS_REGION = ("0x9000", "0x9000")


def flash_lock():
    """Block until this process holds the rig-wide flash lock; released when the process exits."""
    FLASH_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(FLASH_LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"flash: waiting for the flash lock ({FLASH_LOCK})", flush=True)
        fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def esptool_cmd():
    for cand in ([sys.executable, "-m", "esptool"], ["esptool.py"], ["esptool"]):
        try:
            subprocess.run(cand + ["version"], capture_output=True, check=True)
            return cand
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    raise SystemExit("esptool not found -- pip install esptool")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", type=pathlib.Path)
    ap.add_argument("--port", required=True)
    ap.add_argument("--baud", type=int, default=None)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument(
        "--force",
        action="store_true",
        help="always write; by default a board already holding this bundle (esptool verify_flash: the chip "
        "digests each image region) is left alone -- no flash wear for an unchanged image",
    )
    ap.add_argument(
        "--wipe-settings",
        action="store_true",
        help=f"when writing, first erase the settings region ({SETTINGS_REGION[0]}+{SETTINGS_REGION[1]}: NVS, "
        "otadata, phy) -- for a board changing role between firmwares that lay it out differently",
    )
    args = ap.parse_args()

    manifest = json.loads((args.bundle / "manifest.json").read_text())
    chip = manifest["chip"]
    baud = args.baud or (115200 if chip in SLOW_CHIPS else 460800)

    write_args, verify_args = [], []
    for img in manifest["images"]:
        path = args.bundle / img["file"]
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        got = sha256(path)
        if got != img["sha256"]:
            raise SystemExit(f"sha256 mismatch for {img['file']}: {got} != {img['sha256']}")
        write_args += [img["offset"], str(path)]
        if img.get("verify", True):
            verify_args += [img["offset"], str(path)]

    base = esptool_cmd() + ["--chip", chip, "--port", args.port, "--baud", str(baud)]
    flash_lock()
    if not args.force:
        # Already on the chip? verify_flash compares an on-chip digest of each region: reads only, no wear.
        r = subprocess.run(base + ["verify_flash", *verify_args], capture_output=True, text=True)
        if r.returncode == 0:
            print(
                "flash:",
                manifest["board"],
                manifest["profile"],
                manifest["lib_describe"],
                "already on the chip (verified) -- not rewritten",
            )
            return 0
    if args.wipe_settings:
        r = subprocess.run(
            base + ["erase_region", *SETTINGS_REGION], capture_output=True, text=True
        )
        if r.returncode != 0:
            # Writing anyway would report success with the old settings (bonds) still in place.
            print(f"erase_region failed:\n{(r.stdout + r.stderr).strip()}", file=sys.stderr)
            return 1

    # default reset-before / hard-reset-after are the esptool defaults; naming
    # them explicitly just trips deprecation warnings across the 4.x/5.x split.
    cmd = esptool_cmd() + [
        "--chip",
        chip,
        "--port",
        args.port,
        "--baud",
        str(baud),
        "write_flash",
        *write_args,
    ]
    print(
        "flash:",
        manifest["board"],
        manifest["profile"],
        manifest["lib_describe"],
        f"({chip} @ {baud})",
    )

    for attempt in range(1, args.retries + 1):
        r = subprocess.run(cmd)
        if r.returncode == 0:
            time.sleep(2)  # let it reboot into the new image
            return 0
        print(f"esptool failed (attempt {attempt}/{args.retries})", file=sys.stderr)
        time.sleep(3)
    return 1


if __name__ == "__main__":
    sys.exit(main())
