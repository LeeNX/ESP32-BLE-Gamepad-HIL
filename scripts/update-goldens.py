#!/usr/bin/env python3
"""Regenerate firmware/golden/<profile>.hiddesc from a live board.

Builds a profile, flashes it to one board, reads the HID report descriptor back
over the serial channel (`RMAP?`), and writes the golden. Serial only -- no BLE,
no Linux, no hidraw -- so it runs anywhere `builder/build.sh` does (PlatformIO +
a wired ESP32). The reference rig can't build; do this on a dev box instead.

Use it after an *intentional* descriptor change (a new profile, a changed
layout). It refuses to write a descriptor that overruns the library's fixed
150-byte buffer. Review `git diff firmware/golden/` and commit.

    scripts/update-goldens.py specials minimal
    scripts/update-goldens.py "specials minimal"        # one quoted arg is fine too
    scripts/update-goldens.py --board esp32dev --port /dev/cu.usbserial-110 default
    scripts/update-goldens.py --no-build specials       # reuse ./bundles/ from a prior build

Run it with an interpreter that has `pyserial` + `esptool` (the desktop venv
works: `desktop/.venv/bin/python scripts/update-goldens.py ...`).
"""

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO / "firmware" / "golden"
BUFFER_LIMIT = 150  # BleGamepad's tempHidReportDescriptor[150], no bounds check


def cfg(key):
    r = subprocess.run(
        [sys.executable, str(REPO / "host" / "hil" / "config.py"), key],
        capture_output=True,
        text=True,
    )
    return r.stdout.strip()


def find_bundle(bundles_dir, board, profile):
    hits = [d for d in bundles_dir.glob(f"{board}-{profile}-*") if (d / "manifest.json").exists()]
    if not hits:
        have = sorted(p.name for p in bundles_dir.glob(f"{board}-*") if p.is_dir())
        sys.exit(
            f"no {board}-{profile}-* bundle in {bundles_dir}"
            + (f" -- have: {', '.join(have)}" if have else " (empty)")
        )
    return max(hits, key=lambda d: d.stat().st_mtime)  # newest, ignore stale


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "profiles",
        nargs="+",
        metavar="PROFILE",
        help="profile name(s) -- 'specials minimal' or 'specials' 'minimal'",
    )
    ap.add_argument("--board", default="esp32dev", help="board to flash (default: esp32dev)")
    ap.add_argument("--port", help="serial command port (default: $HIL_PORT or hil_config)")
    ap.add_argument("--flash-port", help="esptool port, if different from --port")
    ap.add_argument("--no-build", action="store_true", help="reuse ./bundles/ instead of building")
    args = ap.parse_args()

    # accept "a b c" as one arg or a b c as three
    profiles = [p for chunk in args.profiles for p in chunk.split()]

    sys.path.insert(0, str(REPO / "host"))
    try:
        from hil.serialdev import SerialDev
    except ModuleNotFoundError as e:
        sys.exit(
            f"{e} -- run this with an interpreter that has pyserial + esptool, "
            f"e.g. desktop/.venv/bin/python {pathlib.Path(__file__).name}"
        )

    port = args.port or os.environ.get("HIL_PORT") or cfg(f"board.{args.board}.port")
    if not port or "CHANGE-ME" in port:
        sys.exit(
            f"no serial port for {args.board} -- pass --port or set "
            f"[board.{args.board}] port in hil_config.local.toml"
        )
    flash_port = (
        args.flash_port
        or os.environ.get("HIL_FLASH_PORT")
        or cfg(f"board.{args.board}.flash_port")
        or port
    )

    if args.no_build:
        bundles_dir = REPO / "bundles"
    else:
        # build into a private dir so a stale ./bundles/ can't shadow a profile
        bundles_dir = pathlib.Path(tempfile.mkdtemp(prefix="hil-goldens-"))
        subprocess.run(
            [
                str(REPO / "builder" / "build.sh"),
                "--boards",
                args.board,
                "--profiles",
                " ".join(profiles),
                "--out",
                str(bundles_dir),
            ],
            check=True,
        )

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    touched = []
    for profile in profiles:
        bundle = find_bundle(bundles_dir, args.board, profile)
        print(f"\n== {profile}: flash {bundle.name} on {args.board} ({flash_port})")
        subprocess.run(
            [sys.executable, str(REPO / "tester" / "flash.py"), str(bundle), "--port", flash_port],
            check=True,
        )
        time.sleep(2)  # board reboots into the new image

        dev = SerialDev(port)
        try:
            dev.wait_ready()
            fid = dev.firmware_id()
            if f"profile={profile}" not in fid:
                sys.exit(f"board reports {fid!r}, expected profile={profile}")
            desc = dev.report_descriptor()  # RMAP? -- serial, not BLE
            size = dev.report_sizes()["descriptor"]
        finally:
            dev.close()

        if len(desc) != size:
            sys.exit(f"{profile}: RMAP? is {len(desc)} B but RSIZE? says {size} B")
        if len(desc) > BUFFER_LIMIT:
            sys.exit(
                f"{profile}: descriptor is {len(desc)} B -- over the "
                f"{BUFFER_LIMIT}-byte tempHidReportDescriptor buffer. Trim the profile "
                f"(fewer axes / no hat) before regenerating its golden."
            )

        out = GOLDEN_DIR / f"{profile}.hiddesc"
        was = out.read_text().strip() if out.exists() else None
        now = desc.hex()
        out.write_text(now + "\n")
        state = "unchanged" if was == now else ("new" if was is None else "updated")
        print(f"== {profile}: {len(desc)} B descriptor -> {out.relative_to(REPO)}  ({state})")
        if state != "unchanged":
            touched.append(profile)

    print()
    subprocess.run(["git", "-C", str(REPO), "diff", "--stat", "--", "firmware/golden"])
    if touched:
        print(f"\nregenerated: {', '.join(touched)}")
        print("review the diff, then:  git add firmware/golden/ && git commit")


if __name__ == "__main__":
    main()
