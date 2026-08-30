#!/usr/bin/env python3
"""TESTER role: flash a firmware bundle (builder/make_bundle.py output) with
nothing but esptool. No PlatformIO, no toolchain -- fine on a Raspberry Pi.

    tester/flash.py <bundle_dir> --port /dev/ttyUSB0
"""

import argparse
import hashlib
import json
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
    args = ap.parse_args()

    manifest = json.loads((args.bundle / "manifest.json").read_text())
    chip = manifest["chip"]
    baud = args.baud or (115200 if chip in SLOW_CHIPS else 460800)

    write_args = []
    for img in manifest["images"]:
        path = args.bundle / img["file"]
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        got = sha256(path)
        if got != img["sha256"]:
            raise SystemExit(f"sha256 mismatch for {img['file']}: {got} != {img['sha256']}")
        write_args += [img["offset"], str(path)]

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
