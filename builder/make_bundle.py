#!/usr/bin/env python3
"""Collect a PlatformIO build into a self-contained, machine-portable firmware
bundle the tester can flash with nothing but esptool.

A bundle is a directory:

    bundles/<board>-<profile>-<libsha8>/
        bootloader.bin  partitions.bin  boot_app0.bin  firmware.bin
        manifest.json     # chip, flash offsets, sha256s, lib provenance

Flash offsets come from `pio run -e <env> -t idedata` (its `extra.flash_images`
+ `extra.application_offset`) so they stay correct per chip without a hardcoded
table.
"""

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import socket
import subprocess
import sys


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_idedata(text):
    """`pio run -t idedata` prints one big JSON object on its own line, wrapped
    in ordinary build output. Find and parse it."""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and '"flash_images"' in line:
            return json.loads(line)
    raise SystemExit("could not find idedata JSON in the pio output")


def git(lib_dir, *args):
    return subprocess.run(
        ["git", "-C", str(lib_dir), *args], capture_output=True, text=True
    ).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir", required=True, help=".pio/build/<env>")
    ap.add_argument(
        "--idedata",
        required=True,
        help="file with captured `pio run -t idedata` output, or - for stdin",
    )
    ap.add_argument("--env", required=True)
    ap.add_argument("--profile", required=True)
    ap.add_argument("--board", required=True)
    ap.add_argument("--chip", required=True, help="esptool --chip value, e.g. esp32 / esp32c3")
    ap.add_argument("--lib-dir", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument(
        "--device-name",
        default="",
        help="advertised BLE name this image was built with, when overridden "
        "(builder/build.sh --name / $HIL_DEVICE_NAME). Recorded in the manifest "
        "so the tester can assert NAME? against it. Empty = firmware default.",
    )
    ap.add_argument(
        "--name-suffix",
        default="",
        help="extra component for the bundle dir name, after the library sha (build-observers.sh: the rig sha, "
        "since an observer's firmware source is this repo)",
    )
    ap.add_argument(
        "--app-offset",
        default="",
        help="flash offset for firmware.bin, overriding idedata's application_offset (which is the first "
        "app partition PlatformIO finds -- an OTA slot, for a table with a factory app)",
    )
    ap.add_argument(
        "--extra-image",
        action="append",
        default=[],
        metavar="OFFSET:FILE",
        help="another image to flash, e.g. a blank NVS so a role-swapped board starts clean; repeatable",
    )
    args = ap.parse_args()

    build_dir = pathlib.Path(args.build_dir)
    ide_text = sys.stdin.read() if args.idedata == "-" else pathlib.Path(args.idedata).read_text()
    extra = extract_idedata(ide_text).get("extra", {})

    images = list(extra.get("flash_images", []))
    images.append(
        {
            "offset": args.app_offset or extra.get("application_offset", "0x10000"),
            "path": str(build_dir / "firmware.bin"),
        }
    )
    for spec in args.extra_image:
        offset, _, path = spec.partition(":")
        # Not part of "is this firmware already on the chip?": e.g. a blank NVS the firmware writes to.
        images.append({"offset": offset, "path": path, "verify": False})

    lib_sha = git(args.lib_dir, "rev-parse", "HEAD")
    lib_describe = git(args.lib_dir, "describe", "--tags", "--always", "--dirty") or lib_sha[:8]
    # The rig commit the firmware source came from (hil_runner, the observer): this repo.
    rig_dir = pathlib.Path(__file__).resolve().parent.parent
    rig_sha = git(rig_dir, "rev-parse", "HEAD")
    rig_describe = git(rig_dir, "describe", "--tags", "--always", "--dirty") or rig_sha[:8]

    name = f"{args.board}-{args.profile}-{lib_sha[:8]}" + (
        f"-{args.name_suffix}" if args.name_suffix else ""
    )
    out = pathlib.Path(args.out_root) / name
    out.mkdir(parents=True, exist_ok=True)

    manifest_images = []
    for img in images:
        src = pathlib.Path(img["path"])
        if not src.is_file():
            raise SystemExit(f"missing build artifact: {src}")
        dst = out / src.name
        shutil.copy2(src, dst)
        manifest_images.append(
            {
                "offset": img["offset"]
                if str(img["offset"]).startswith("0x")
                else hex(int(img["offset"])),
                "file": src.name,
                "sha256": sha256(dst),
                **({"verify": False} if img.get("verify") is False else {}),
            }
        )

    manifest = {
        "board": args.board,
        "chip": args.chip,
        "pio_env": args.env,
        "profile": args.profile,
        "lib_sha": lib_sha,
        "lib_describe": lib_describe,
        "rig_sha": rig_sha,
        "rig_describe": rig_describe,
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "builder": socket.gethostname(),
        "images": manifest_images,
    }
    if args.device_name:
        manifest["device_name"] = args.device_name
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    main()
