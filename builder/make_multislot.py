#!/usr/bin/env python3
"""Compose one multi-slot bundle from up to N ordinary bundles (PoC, docs/multislot-ota.md).

Each source bundle's firmware.bin goes into its own OTA slot of a shared partition table
(firmware/multislot/partitions.csv), behind one bootloader, so a board switches profile or role with an OTA reboot
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
import binascii
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import socket
import struct
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_TABLE = REPO / "firmware" / "multislot" / "partitions.csv"

# esptool's image chip ids (extended header), by esptool --chip name.
CHIP_IDS = {
    "esp32": 0,
    "esp32s2": 2,
    "esp32c3": 5,
    "esp32s3": 9,
    "esp32c2": 12,
    "esp32c6": 13,
    "esp32h2": 16,
}
# Bootloader header byte 3, high nibble -> flash size.
FLASH_SIZES = {
    0: 1 << 20,
    1: 2 << 20,
    2: 4 << 20,
    3: 8 << 20,
    4: 16 << 20,
    5: 32 << 20,
    6: 64 << 20,
    7: 128 << 20,
}

TYPES = {"app": 0x00, "data": 0x01}
SUBTYPES = {
    0x00: {"factory": 0x00, "test": 0x20, **{f"ota_{i}": 0x10 + i for i in range(16)}},
    0x01: {
        "ota": 0x00,
        "phy": 0x01,
        "nvs": 0x02,
        "coredump": 0x03,
        "nvs_keys": 0x04,
        "efuse": 0x05,
        "undefined": 0x06,
        "esphttpd": 0x80,
        "fat": 0x81,
        "spiffs": 0x82,
        "littlefs": 0x83,
    },
}
TABLE_OFFSET = 0x8000
TABLE_SIZE = 0xC00
APP_ALIGN = 0x10000
OTADATA_SECTOR = 0x1000
APP_DESC_MAGIC = 0xABCD5432


def sha256_file(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def parse_table(path):
    """partitions.csv -> [dict(name, type, subtype, offset, size, flags)]. Offsets must be explicit (the PoC table
    pins them; no auto-placement)."""
    parts = []
    for n, raw in enumerate(pathlib.Path(path).read_text().splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        f = [x.strip() for x in line.split(",")]
        f += [""] * (6 - len(f))
        name, ptype, sub, off, size, flags = f[:6]
        if ptype not in TYPES:
            raise SystemExit(f"{path}:{n}: unknown type {ptype!r}")
        t = TYPES[ptype]
        if sub not in SUBTYPES[t]:
            raise SystemExit(f"{path}:{n}: unknown {ptype} subtype {sub!r}")
        if not off or not size:
            raise SystemExit(f"{path}:{n}: offset and size must be explicit")
        if len(name.encode()) > 16:
            raise SystemExit(f"{path}:{n}: name {name!r} longer than 16 bytes")
        parts.append(
            {
                "name": name,
                "type": t,
                "subtype": SUBTYPES[t][sub],
                "sub": sub,
                "offset": int(off, 0),
                "size": int(size, 0),
                "flags": 1 if "encrypted" in flags else 0,
            }
        )
    return parts


def check_table(parts, flash_size):
    """Overlaps, alignment, bounds; returns the OTA slots, in slot order."""
    spans = sorted((p["offset"], p["offset"] + p["size"], p["name"]) for p in parts)
    if spans[0][0] < TABLE_OFFSET + 0x1000:
        raise SystemExit(f"partition {spans[0][2]} overlaps the partition table")
    for (a0, a1, an), (b0, _, bn) in zip(spans, spans[1:]):
        if b0 < a1:
            raise SystemExit(f"partitions {an} and {bn} overlap")
    for p in parts:
        if p["offset"] % 0x1000 or p["size"] % 0x1000:
            raise SystemExit(f"partition {p['name']} not 4 KB aligned")
        if p["type"] == 0 and p["offset"] % APP_ALIGN:
            raise SystemExit(f"app partition {p['name']} not 64 KB aligned")
        if p["offset"] + p["size"] > flash_size:
            raise SystemExit(f"partition {p['name']} ends past the {flash_size >> 20} MB flash")
    slots = sorted(
        (p for p in parts if p["type"] == 0 and 0x10 <= p["subtype"] < 0x20),
        key=lambda p: p["subtype"],
    )
    if [p["subtype"] - 0x10 for p in slots] != list(range(len(slots))):
        raise SystemExit("OTA slots must be ota_0..ota_<n-1>, no gaps")
    otadata = [p for p in parts if p["type"] == 1 and p["subtype"] == 0x00]
    if len(otadata) != 1 or otadata[0]["size"] != 2 * OTADATA_SECTOR:
        raise SystemExit("need exactly one otadata partition of 0x2000")
    if any(p["type"] == 0 and p["subtype"] == 0x00 for p in parts):
        raise SystemExit("factory app in the table: a blank otadata would boot it, not ota_0")
    return slots, otadata[0]


def table_bin(parts):
    """The binary partition table, as ESP-IDF's gen_esp32part.py writes it: 32-byte entries, an MD5 entry, 0xFF."""
    out = b""
    for p in parts:
        out += struct.pack(
            "<2sBBLL16sL",
            b"\xaa\x50",
            p["type"],
            p["subtype"],
            p["offset"],
            p["size"],
            p["name"].encode(),
            p["flags"],
        )
    out += b"\xeb\xeb" + b"\xff" * 14 + hashlib.md5(out).digest()
    return out + b"\xff" * (TABLE_SIZE - len(out))


def otadata_bin(slot):
    """otadata selecting OTA slot <slot>: sector 0 holds ota_seq slot+1 (state undefined -- a plain boot, no
    rollback pending), sector 1 is blank. The bootloader boots slot (seq-1) % slot_count. Same crc as
    ESP-IDF's otatool.py."""
    seq = slot + 1
    crc = binascii.crc32(struct.pack("<I", seq), 0xFFFFFFFF) & 0xFFFFFFFF
    entry = struct.pack("<I20sII", seq, b"\xff" * 20, 0xFFFFFFFF, crc)
    sector = entry + b"\xff" * (OTADATA_SECTOR - len(entry))
    return sector + b"\xff" * OTADATA_SECTOR


def parse_image(data, chip):
    """Walk an ESP app image; check magic, chip, checksum, appended SHA-256, exact length. Returns facts."""
    if len(data) < 24 or data[0] != 0xE9:
        raise ValueError("not an ESP image (magic)")
    nseg = data[1]
    chip_id = struct.unpack_from("<H", data, 12)[0]
    hash_appended = data[23] == 1
    if CHIP_IDS.get(chip) != chip_id:
        raise ValueError(
            f"image is for chip id {chip_id}, bundle chip is {chip} ({CHIP_IDS.get(chip)})"
        )
    pos, csum, desc = 24, 0xEF, None
    for i in range(nseg):
        if pos + 8 > len(data):
            raise ValueError(f"truncated in segment {i} header")
        _addr, seglen = struct.unpack_from("<II", data, pos)
        seg = data[pos + 8 : pos + 8 + seglen]
        if len(seg) != seglen:
            raise ValueError(f"truncated in segment {i}")
        if i == 0 and len(seg) >= 256 and struct.unpack_from("<I", seg, 0)[0] == APP_DESC_MAGIC:
            desc = {
                "version": seg[16:48].split(b"\0")[0].decode(errors="replace"),
                "project_name": seg[48:80].split(b"\0")[0].decode(errors="replace"),
                "idf_ver": seg[112:144].split(b"\0")[0].decode(errors="replace"),
            }
        for b in seg:
            csum ^= b
        pos += 8 + seglen
    pos += 15 - (pos % 16)  # pad so the checksum byte ends a 16-byte block
    if pos >= len(data):
        raise ValueError("truncated before the checksum byte")
    if data[pos] != csum:
        raise ValueError(f"checksum byte {data[pos]:#04x} != computed {csum:#04x}")
    end = pos + 1
    digest = None
    if hash_appended:
        digest = data[end : end + 32]
        if len(digest) != 32 or hashlib.sha256(data[:end]).digest() != digest:
            raise ValueError("appended SHA-256 doesn't match the image")
        end += 32
    if end != len(data):
        raise ValueError(f"image is {end} bytes but the file is {len(data)}")
    if desc is None:
        raise ValueError("no esp_app_desc_t at the start of the first segment")
    # What esp_partition_get_sha256() reports for the slot: the appended digest, or (none appended) the image's.
    return {
        "image_size": end,
        "image_sha256": (digest or hashlib.sha256(data).digest()).hex(),
        **desc,
    }


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
        default=0,
        help="slot whose source bundle supplies bootloader.bin (default 0); every app in this repo's bundles is "
        "ESP-IDF 5.x, whose bootloaders all boot each other's apps",
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

    if not 0 <= args.bootloader_from < len(srcs):
        raise SystemExit("--bootloader-from: no such slot")
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
