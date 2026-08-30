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
    args = ap.parse_args()

    build_dir = pathlib.Path(args.build_dir)
    ide_text = sys.stdin.read() if args.idedata == "-" else pathlib.Path(args.idedata).read_text()
    extra = extract_idedata(ide_text).get("extra", {})

    images = list(extra.get("flash_images", []))
    images.append(
        {
            "offset": extra.get("application_offset", "0x10000"),
            "path": str(build_dir / "firmware.bin"),
        }
    )

    lib_sha = git(args.lib_dir, "rev-parse", "HEAD")
    lib_describe = git(args.lib_dir, "describe", "--tags", "--always", "--dirty") or lib_sha[:8]

    out = pathlib.Path(args.out_root) / f"{args.board}-{args.profile}-{lib_sha[:8]}"
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
            }
        )

    manifest = {
        "board": args.board,
        "chip": args.chip,
        "pio_env": args.env,
        "profile": args.profile,
        "lib_sha": lib_sha,
        "lib_describe": lib_describe,
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "builder": socket.gethostname(),
        "images": manifest_images,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    main()
