#!/usr/bin/env python3
"""Compose one multi-slot bundle from up to N ordinary bundles (PoC, docs/multislot-ota.md).

Each source bundle's firmware.bin goes into its own OTA slot of a shared partition table
(firmware/partitions/multislot.csv), behind one bootloader, so a board switches profile or role with an OTA reboot
(`BOOT <n>`, tester/slot.py) instead of a reflash:

    builder/make_multislot.py --out-root bundles/multislot \\
        bundles/esp32dev2-default-<sha> bundles/matrix/esp32dev2-sinput-<sha> bundles/matrix/esp32dev2-bp32obs-<sha>

The result is an ordinary bundle -- tester/flash.py flashes it -- with a `slots` list in its manifest. Checks, all
before anything is written:

  * every source bundle's files match its manifest sha256s, and all are for one chip;
  * the partition table: OTA slots 64 KB aligned, no overlaps, inside the flash, enough slots for the images;
  * each app image parses as an ESP image for that chip, its XOR checksum and appended SHA-256 hold, and the file is
    exactly the image (no truncation, no trailing bytes);
  * each image fits its slot (with the headroom printed).

The manifest records each slot's image_sha256 -- the image's appended digest, which is what the firmware's `SLOTS?`
reports (esp_partition_get_sha256()) -- so tester/slot.py checks the slots on the chip without reading flash back.
"""

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import socket
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "host"))
from hil.multislot import (  # noqa: E402
    DEFAULT_TABLE,
    FLASH_SIZES,
    REPO,
    TABLE_OFFSET,
    check_table,
    otadata_bin,
    parse_image,
    parse_table,
    sha256_file,
    table_bin,
)


def load_bundle(path):
    path = pathlib.Path(path)
    m = json.loads((path / "manifest.json").read_text())
    for img in m["images"]:
        got = sha256_file(path / img["file"])
        if got != img["sha256"]:
            raise SystemExit(f"{path}: {img['file']} sha256 {got} != manifest {img['sha256']}")
    return path, m


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("bundles", nargs="+", type=pathlib.Path, help="source bundles, slot 0 first")
    ap.add_argument("--table", type=pathlib.Path, default=DEFAULT_TABLE)
    ap.add_argument("--out-root", type=pathlib.Path, default=REPO / "bundles" / "multislot")
    ap.add_argument(
        "--boot", type=int, default=0, help="slot the board starts in after flashing (default 0)"
    )
    ap.add_argument(
        "--bootloader-from",
        type=int,
        default=None,
        help="slot whose source bundle supplies bootloader.bin (default: the first hil_runner bundle). It must be a "
        "runner's: under the observer build's bootloader hil_runner's SLOTS? reads every slot empty "
        "(docs/multislot-ota.md, the bootloader rule)",
    )
    ap.add_argument(
        "--name", default="", help="bundle dir name (default <board>-ms-<profiles>-<sha8>)"
    )
    args = ap.parse_args()

    srcs = [load_bundle(b) for b in args.bundles]
    chips = {m["chip"] for _, m in srcs}
    if len(chips) != 1:
        raise SystemExit(f"bundles are for different chips: {sorted(chips)}")
    chip = chips.pop()
    boards = {m["board"] for _, m in srcs}
    board = srcs[0][1]["board"] if len(boards) == 1 else "+".join(sorted(boards))
    if len(boards) != 1:
        print(
            f"note: mixed boards {sorted(boards)} -- each image keeps its own advertised name",
            file=sys.stderr,
        )

    if args.bootloader_from is None:
        args.bootloader_from = next(
            (i for i, (_, m) in enumerate(srcs) if m["profile"] != "bp32obs"), None
        )
        if args.bootloader_from is None:
            raise SystemExit(
                "no hil_runner bundle to take the bootloader from: give --bootloader-from"
            )
    if not 0 <= args.bootloader_from < len(srcs):
        raise SystemExit("--bootloader-from: no such slot")
    if srcs[args.bootloader_from][1]["profile"] == "bp32obs":
        print(
            "warning: bootloader from the observer bundle -- hil_runner's SLOTS? will read every slot empty",
            file=sys.stderr,
        )
    bl_dir, bl_m = srcs[args.bootloader_from]
    bl_img = next((i for i in bl_m["images"] if i["file"] == "bootloader.bin"), None)
    if not bl_img:
        raise SystemExit(f"{bl_dir}: no bootloader.bin")
    bl = (bl_dir / "bootloader.bin").read_bytes()
    if bl[0] != 0xE9:
        raise SystemExit(f"{bl_dir}/bootloader.bin: not an ESP image")
    flash_size = FLASH_SIZES.get(bl[3] >> 4, 4 << 20)

    parts = parse_table(args.table)
    slots, otadata = check_table(parts, flash_size)
    if len(srcs) > len(slots):
        raise SystemExit(f"{len(srcs)} images but the table has {len(slots)} OTA slots")
    if not 0 <= args.boot < len(srcs):
        raise SystemExit("--boot: no image in that slot")
    nvs = next((p for p in parts if p["type"] == 1 and p["sub"] == "nvs"), None)
    if not nvs:
        raise SystemExit("no nvs partition in the table")

    slot_info, ok = [], True
    for i, ((src, m), part) in enumerate(zip(srcs, slots)):
        app = next(img for img in m["images"] if img["file"] == "firmware.bin")
        data = (src / "firmware.bin").read_bytes()
        try:
            facts = parse_image(data, chip)
        except ValueError as e:
            raise SystemExit(f"slot {i} ({src.name}): {e}") from None
        headroom = part["size"] - facts["image_size"]
        fits = headroom >= 0
        ok &= fits
        print(
            f"slot {i} {part['name']:6} @ {part['offset']:#09x} {facts['image_size']:>8} / {part['size']} bytes "
            f"({100 * facts['image_size'] / part['size']:.0f}%, {'headroom ' + str(headroom) if fits else 'TOO BIG'}) "
            f"{m['profile']:9} {facts['project_name']} {facts['version']} idf {facts['idf_ver']} "
            f"sha {facts['image_sha256'][:16]}"
        )
        slot_info.append(
            {
                "slot": i,
                "partition": part["name"],
                "offset": hex(part["offset"]),
                "size": part["size"],
                "file": f"slot{i}.bin",
                "file_sha256": app["sha256"],
                "headroom": headroom,
                "role": "observer" if m["profile"] == "bp32obs" else "dut",
                "source_bundle": src.name,
                "board": m["board"],
                "profile": m["profile"],
                "lib_sha": m.get("lib_sha", ""),
                "lib_describe": m.get("lib_describe", ""),
                "rig_sha": m.get("rig_sha", ""),
                **facts,
            }
        )
    if not ok:
        raise SystemExit("an image doesn't fit its slot")

    sha8 = hashlib.sha256("".join(s["image_sha256"] for s in slot_info).encode()).hexdigest()[:8]
    name = args.name or f"{board}-ms-{'+'.join(s['profile'] for s in slot_info)}-{sha8}"
    out = args.out_root / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    shutil.copy2(bl_dir / "bootloader.bin", out / "bootloader.bin")
    (out / "partitions.bin").write_bytes(table_bin(parts))
    (out / "otadata.bin").write_bytes(otadata_bin(args.boot))
    (out / "nvs_blank.bin").write_bytes(b"\xff" * nvs["size"])
    for s, (src, _) in zip(slot_info, srcs):
        shutil.copy2(src / "firmware.bin", out / s["file"])

    images = [
        {"offset": bl_img["offset"], "file": "bootloader.bin"},
        {"offset": hex(TABLE_OFFSET), "file": "partitions.bin"},
        # Not part of "already on the chip?" (flash.py verify_flash): a slot switch rewrites otadata, NVS changes.
        {"offset": hex(otadata["offset"]), "file": "otadata.bin", "verify": False},
        {"offset": hex(nvs["offset"]), "file": "nvs_blank.bin", "verify": False},
        *({"offset": s["offset"], "file": s["file"]} for s in slot_info),
    ]
    for img in images:
        img["sha256"] = sha256_file(out / img["file"])
    manifest = {
        "board": board,
        "chip": chip,
        "profile": "multislot",
        "lib_sha": "",
        "lib_describe": " ".join(f"{s['slot']}={s['profile']}" for s in slot_info),
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "builder": socket.gethostname(),
        "flash_size": flash_size,
        "table": str(args.table.relative_to(REPO))
        if args.table.is_relative_to(REPO)
        else str(args.table),
        "otadata_offset": hex(otadata["offset"]),
        "boot_slot": args.boot,
        "bootloader_from": args.bootloader_from,
        "slots": slot_info,
        "images": [{k: v for k, v in i.items()} for i in images],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    main()
