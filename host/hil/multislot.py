"""Multi-slot flash layout helpers, shared by builder/make_multislot.py, tester/flash.py and tester/slot.py
(docs/multislot-ota.md): the partition table (parse, check, binary), otadata, and ESP app image checks.

Standard library only, but for Console (pyserial, imported when used): tester/flash.py runs on the Pi with nothing
but esptool and pyserial.
"""

import binascii
import hashlib
import json
import os
import pathlib
import re
import struct
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_TABLE = REPO / "firmware" / "partitions" / "multislot.csv"

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
    if len(out) > TABLE_SIZE:
        raise SystemExit(
            f"partition table: {len(parts)} entries don't fit in {TABLE_SIZE:#x} bytes"
        )
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


def layout(path=DEFAULT_TABLE, flash_size=4 << 20):
    """The checked table: parts, OTA slots (in order), otadata, nvs, and its binary."""
    parts = parse_table(path)
    slots, otadata = check_table(parts, flash_size)
    nvs = next((p for p in parts if p["type"] == 1 and p["sub"] == "nvs"), None)
    if not nvs:
        raise SystemExit(f"{path}: no nvs partition")
    return {
        "parts": parts,
        "slots": slots,
        "otadata": otadata,
        "nvs": nvs,
        "table": table_bin(parts),
    }


# --- the firmware's console side: SLOTS? / BOOT <n> [WIPE] (firmware/include/hil_slots.h) ---------------------------

REPLY = re.compile(r"^(?:HIL )?(slots|ok|err)\b ?(.*)$", re.IGNORECASE)
SLOT_TOKEN = re.compile(r"^(\d+)=(.*)$")


def parse_slots(rest):
    """`running=<n> boot=<n> count=<n> <i>=<sha>/<state>/<proj>/<ver>|empty ...` -> dict."""
    out = {"slots": {}}
    for tok in rest.split():
        m = SLOT_TOKEN.match(tok)
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


class Console:
    """A board's command console (hil_runner and the observer answer the same SLOTS? / BOOT). Opened with DTR/RTS
    deasserted, so an auto-reset bridge isn't pulsed on purpose -- the Linux tty can still reset the chip on open
    (seen on the rig's CP2102/CH340), which the retries below ride out."""

    def __init__(self, port):
        import serial  # pyserial: only the console side needs it

        self.ser = serial.Serial()
        self.ser.port, self.ser.baudrate, self.ser.timeout = port, 115200, 0.2
        self.ser.dtr = self.ser.rts = False
        self.ser.open()

    def close(self):
        try:
            self.ser.close()
        except OSError:
            pass

    def command(self, line, timeout=6.0):
        """Send one line; (kind, rest) of the first SLOTS / OK / ERR reply in either firmware's form, or (None, None)."""
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
        """Parsed SLOTS? reply, or None. An ERR is retried: a line sent while the chip boots arrives garbled and gets
        ERR unknown. Returns None also when every try was an ERR (firmware without SLOTS?)."""
        for i in range(retries):
            if i:
                time.sleep(1.0)
            kind, rest = self.command("SLOTS?", timeout)
            if kind == "slots":
                return parse_slots(rest)
        return None

    def boot(self, slot, wipe=False):
        """BOOT <slot> [WIPE]; (True, "") once the firmware said OK (it then restarts), else (False, reason)."""
        kind, rest = self.command(f"BOOT {slot}{' WIPE' if wipe else ''}")
        if kind == "ok":
            return True, ""
        return False, f"{kind or 'no reply'} {rest or ''}".strip()

    def wait_running(self, slot, timeout=25.0):
        """Poll SLOTS? after a restart until it reports running == slot; the parsed reply, or the last one / None."""
        t0, last = time.monotonic(), None
        while time.monotonic() - t0 < timeout:
            time.sleep(1.0)
            kind, rest = self.command("SLOTS?", 5.0)
            if kind == "slots":
                last = parse_slots(rest)
                if last.get("running") == slot:
                    return last
        return last


# --- the tester's record of what each slot holds (for least-recently-used eviction and labels) ---------------------
# Only a hint: tester/flash.py checks a slot's contents on the chip (SLOTS? digests or esptool verify_flash) before it
# trusts it, and corrects this from what it finds.


def state_path(port):
    base = pathlib.Path(
        os.environ.get("HIL_SLOT_STATE_DIR")
        or pathlib.Path(os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache")
        / "esp32-hil"
        / "slots"
    )
    return base / (re.sub(r"[^A-Za-z0-9._-]+", "_", str(port)).strip("_") + ".json")


def load_state(port):
    try:
        st = json.loads(state_path(port).read_text())
        return {int(k): v for k, v in st.get("slots", {}).items()}
    except (OSError, ValueError):
        return {}


def save_state(port, slots):
    p = state_path(port)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"slots": {str(k): v for k, v in sorted(slots.items())}}, indent=2) + "\n"
    )
    tmp.replace(p)


def pick_victim(state, nslots, keep=()):
    """Slot to overwrite, never one in keep: a second copy of an image another slot also holds first (the copy used
    least recently goes), then a slot the record knows nothing about (empty, or garbage from another layout), then the
    least recently used. Rig, 2026-11-09: without the duplicate rule a board holding the observer twice evicted the
    profile the run needed next, and wrote it back a minute later."""
    cands = [i for i in range(nslots) if i not in keep] or list(range(nslots))

    def used(i):
        return state.get(i, {}).get("used", 0)

    def dup(i):
        sha = state.get(i, {}).get("sha256")
        return bool(sha) and any(
            j != i and state.get(j, {}).get("sha256") == sha and (used(j), j) > (used(i), i)
            for j in state
        )

    return min(cands, key=lambda i: (not dup(i), i in state, used(i), i))
