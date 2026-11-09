#!/usr/bin/env python3
"""TESTER role: flash a firmware bundle (builder/make_bundle.py output) with
nothing but esptool. No PlatformIO, no toolchain -- fine on a Raspberry Pi.

    tester/flash.py <bundle_dir> --port /dev/ttyUSB0 [--console-port /dev/ttyUSB1]

Multi-slot (the default, docs/multislot-ota.md): the board's flash holds four apps in OTA slots behind a shared
partition table (firmware/multislot/partitions.csv), and this treats them as a cache:

  1. the bundle's app already in a slot -> boot it: over the console (`BOOT <n>`, no esptool, no USB bulk transfer,
     no native-USB re-enumeration) when --console-port answers, else an 8 KB otadata write with esptool;
  2. not in any slot -> write just the app into the least recently used slot (plus otadata);
  3. board not on the multi-slot layout yet -> convert it: bootloader, table, blank otadata + NVS, app in slot 0.

Either way the chip restarts into the bundle's app, as a legacy flash does. A slot is trusted only after the chip
digests it (SLOTS? reports each slot's image SHA-256; esptool verify_flash compares MD5s on the chip). A bundle
whose own layout must be kept (a composed multi-slot bundle, builder/make_multislot.py) or --no-multislot /
HIL_MULTISLOT=0 flashes the legacy way: every image at its own offset, skipped when already on the chip.

$HIL_FLASH_RECORD (or --record): write a JSON record of what happened (slot, action, method, bytes written,
seconds) there -- tester/test.sh points it at results/flash-<board>-<profile>.json for the run summary; and
$HIL_FLASH_LOG: append the same record as one JSON line (results/flash-log.jsonl: every flash of a run).
"""

import argparse
import fcntl
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "host"))
from hil import multislot as ms  # noqa: E402

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


# NVS / otadata / phy on both layouts the rig flashes (hil_runner: nvs 0x9000+0x5000, otadata 0xe000, app at 0x10000;
# the Bluepad32 observer: nvs 0x9000+0x6000, otadata 0xf000, phy 0x11000, app at 0x20000). The erase stops at the
# bundle's app (firmware.bin) when that comes first -- the runner's -- so it never touches application bytes.
# Legacy path only: the multi-slot path wipes just the nvs partition, keeping otadata (the slot selection).
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


def run_retried(cmd, retries, capture=False):
    """Run an esptool command up to `retries` times; the last CompletedProcess."""
    r = None
    for attempt in range(1, retries + 1):
        r = subprocess.run(cmd, capture_output=capture, text=capture)
        if r.returncode == 0:
            return r
        print(f"esptool failed (attempt {attempt}/{retries})", file=sys.stderr)
        if capture:
            print((r.stdout + r.stderr).strip()[-600:], file=sys.stderr)
        time.sleep(3)
    return r


# esptool 5.x: "Verifying 0x.. (..) bytes at 0x00020000 in flash against ..." then "Verification successful ..." /
# "Verification failed ..."; 4.x: "... bytes @ 0x00020000 in flash against ..." then "-- verify OK ..." /
# "-- verify FAILED ...".
VERIFY_AT = re.compile(r"Verifying .*?(?:at|@) (0x[0-9a-fA-F]+) in flash")
VERIFY_OK = re.compile(r"Verification successful|verify OK", re.IGNORECASE)
VERIFY_BAD = re.compile(r"Verification failed|verify FAILED", re.IGNORECASE)


def parse_verify(text):
    """{address: True/False} for every region esptool verify_flash reported on."""
    out, cur = {}, None
    for line in text.splitlines():
        m = VERIFY_AT.search(line)
        if m:
            cur = int(m.group(1), 16)
            continue
        if cur is not None and VERIFY_OK.search(line):
            out[cur], cur = True, None
        elif cur is not None and VERIFY_BAD.search(line):
            out[cur], cur = False, None
    return out


def write_record(path, rec):
    if path:
        p = pathlib.Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, indent=2) + "\n")
    log = os.environ.get("HIL_FLASH_LOG")
    if log:
        p = pathlib.Path(log)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(rec) + "\n")


def legacy(args, manifest, base, rec):
    """Every image at its own offset; skipped when already on the chip (verify_flash)."""
    write_args, verify_args, settings_args, sizes = [], [], [], {}
    lo = int(SETTINGS_REGION[0], 16)
    app = [int(i["offset"], 16) for i in manifest["images"] if i["file"] == "firmware.bin"]
    hi = min([lo + int(SETTINGS_REGION[1], 16), *(a for a in app if a > lo)])
    for img in manifest["images"]:
        path = args.bundle / img["file"]
        sizes[str(path)] = path.stat().st_size
        write_args += [img["offset"], str(path)]
        if lo <= int(img["offset"], 16) < hi:
            settings_args += [img["offset"], str(path)]
        if img.get("verify", True):
            verify_args += [img["offset"], str(path)]

    rec.update(mode="legacy", method="esptool")
    what = f"({manifest['chip']} @ {args.baud_eff})"
    if not args.force:
        # Already on the chip? verify_flash compares an on-chip digest of each region: reads only, no wear.
        r = subprocess.run(base + ["verify_flash", *verify_args], capture_output=True, text=True)
        if r.returncode == 0:
            if not args.wipe_settings:
                print(
                    "flash:",
                    manifest["board"],
                    manifest["profile"],
                    manifest["lib_describe"],
                    "already on the chip (verified) -- not rewritten",
                )
                rec.update(action="unchanged", bytes_written=0)
                return 0
            # The app stays, but the wipe is still owed: a board kept on the same bundle otherwise keeps its bonds
            # (2026-11-09: sinput gamepads left over from the previous run then failed BlueZ's fresh pairing with
            # "GATT services never resolved"). Erase, then rewrite just the bundle's images inside the region
            # (otadata, the observer's blank NVS) -- the write also resets the chip into the erased settings.
            write_args = settings_args
            what = "already on the chip (verified) -- settings wiped, app not rewritten"
    if args.wipe_settings:
        r = subprocess.run(
            base + ["erase_region", hex(lo), hex(hi - lo)], capture_output=True, text=True
        )
        if r.returncode != 0:
            # Writing anyway would report success with the old settings (bonds) still in place.
            print(f"erase_region failed:\n{(r.stdout + r.stderr).strip()}", file=sys.stderr)
            return 1

    # default reset-before / hard-reset-after are the esptool defaults; naming
    # them explicitly just trips deprecation warnings across the 4.x/5.x split.
    # "run" when there's nothing to write (no settings image in the bundle): just reboot the chip
    cmd = base + (["write_flash", *write_args] if write_args else ["run"])
    print("flash:", manifest["board"], manifest["profile"], manifest["lib_describe"], what)
    r = run_retried(cmd, args.retries)
    if r.returncode != 0:
        return 1
    rec.update(
        action="written" if "already" not in what else "wiped",
        bytes_written=sum(sizes[f] for f in write_args[1::2]),
    )
    time.sleep(2)  # let it reboot into the new image
    return 0


def multislot(args, manifest, base, rec):
    """The slot cache -- see the module docstring."""
    lay = ms.layout()
    slots, nslots = lay["slots"], len(lay["slots"])
    fw = args.bundle / "firmware.bin"
    try:
        facts = ms.parse_image(fw.read_bytes(), manifest["chip"])
    except ValueError as e:
        raise SystemExit(f"{fw}: {e}") from None
    if facts["image_size"] > slots[0]["size"]:
        raise SystemExit(
            f"{fw}: {facts['image_size']} bytes doesn't fit a {slots[0]['size']}-byte slot "
            "(HIL_MULTISLOT=0 / --no-multislot flashes the legacy layout)"
        )
    want = facts["image_sha256"]
    data = fw.read_bytes()
    # Images built before SLOTS? existed (an older bundle) can't be asked what they run: esptool only, no console
    # check after a write. Both firmwares carry the literal command string.
    speaks_slots = b"SLOTS?" in data
    # A bundle that carries a blank NVS (the observer's: it must start without stale bonds) gets it whenever its app
    # is (re)started from another one -- as the legacy flash writes it whenever it writes the app.
    nvs_off = lay["nvs"]["offset"]
    blank_nvs_on_change = any(
        int(i["offset"], 16) == nvs_off and i.get("verify") is False for i in manifest["images"]
    )
    state = ms.load_state(args.port)
    label = f"{manifest['board']} {manifest['profile']} {manifest['lib_describe']}"
    rec.update(mode="multislot", image_sha256=want, slots=nslots)

    def done(slot, action, method, nbytes, wipe):
        now = time.time()
        for k, v in list(state.items()):
            if v.get("sha256") == want and k != slot:
                del state[k]  # the same image can only be in one slot as far as eviction goes
        state[slot] = {
            "sha256": want,
            "label": label,
            "used": now,
            "written": state.get(slot, {}).get("written", now),
        }
        if action in ("write", "convert"):
            state[slot]["written"] = now
        ms.save_state(args.port, state)
        rec.update(slot=slot, action=action, method=method, bytes_written=nbytes, wipe=wipe)
        verb = {
            "reboot": "already running, restarted",
            "switch": "switched",
            "write": "written",
            "convert": "board converted to multi-slot, written",
        }[action]
        print(
            f"flash: {label} -> slot {slot} ({verb} via {method}, {nbytes} B written"
            f"{', NVS wiped' if wipe else ''})"
        )
        return 0

    # 1. Console: the running firmware knows its slots (on-chip digests) and switches without esptool.
    known = None
    if args.console_port and not args.force:
        con = None
        try:
            con = ms.Console(args.console_port)
            # Short tries: a board on legacy firmware answers ERR, a crashed one nothing -- don't hold up the
            # esptool path for long.
            known = con.slots(timeout=4.0, retries=3)
            if known and known.get("count") == nslots:
                for k, v in known["slots"].items():
                    if not v:
                        state.pop(k, None)
                    elif state.get(k, {}).get("sha256") != v["sha256"]:
                        state[k] = {"sha256": v["sha256"], "label": "?", "used": 0}
                hits = [k for k, v in known["slots"].items() if v and v["sha256"] == want]
                if hits:
                    slot = known.get("running") if known.get("running") in hits else hits[0]
                    same = known.get("running") == slot
                    wipe = args.wipe_settings or (blank_nvs_on_change and not same)
                    ok, why = con.boot(slot, wipe)
                    if ok:
                        after = con.wait_running(slot)
                        got = (after or {}).get("slots", {}).get(slot) or {}
                        if after and after.get("running") == slot and got.get("sha256") == want:
                            return done(slot, "reboot" if same else "switch", "console", 0, wipe)
                        print(
                            f"flash: BOOT {slot} didn't come up as asked ({after}) -- esptool",
                            file=sys.stderr,
                        )
                    else:
                        print(f"flash: BOOT {slot} -> {why} -- esptool", file=sys.stderr)
            else:
                known = None
        except (OSError, ValueError) as e:
            print(f"flash: console {args.console_port}: {e} -- esptool", file=sys.stderr)
            known = None
        finally:
            if con:
                con.close()

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "partitions.bin").write_bytes(lay["table"])
        (tmp / "nvs_blank.bin").write_bytes(b"\xff" * lay["nvs"]["size"])

        def otadata(slot):
            p = tmp / f"otadata{slot}.bin"
            p.write_bytes(ms.otadata_bin(slot))
            return str(p)

        # 2. esptool verify, one session: is the table ours, and which slot (if any) holds the app?
        regions = [hex(ms.TABLE_OFFSET), str(tmp / "partitions.bin")]
        for s in slots:
            regions += [hex(s["offset"]), str(fw)]
        # A mismatch is an ordinary answer here (rc != 0); only no answer at all (no connection) is retried.
        for attempt in range(1, args.retries + 1):
            r = subprocess.run(base + ["verify_flash", *regions], capture_output=True, text=True)
            res = parse_verify(r.stdout + r.stderr)
            if ms.TABLE_OFFSET in res:
                break
            print(
                f"esptool verify_flash: no result (attempt {attempt}/{args.retries})",
                file=sys.stderr,
            )
            time.sleep(3)
        else:
            print((r.stdout + r.stderr).strip()[-800:], file=sys.stderr)
            return 1
        table_ok = res[ms.TABLE_OFFSET]
        hits = [i for i, s in enumerate(slots) if res.get(s["offset"])]
        for i, s in enumerate(slots):
            if not res.get(s["offset"]) and state.get(i, {}).get("sha256") == want:
                del state[i]

        fw_size = fw.stat().st_size
        if not table_ok:
            # 3. Convert: the board is on another layout (a legacy bundle, an erased chip).
            boot = next((i for i in manifest["images"] if i["file"] == "bootloader.bin"), None)
            if not boot:
                raise SystemExit(f"{args.bundle}: no bootloader.bin to convert the board with")
            state.clear()
            slot, action, wipe = 0, "convert", True
            writes = [
                boot["offset"], str(args.bundle / "bootloader.bin"),
                hex(ms.TABLE_OFFSET), str(tmp / "partitions.bin"),
                hex(lay["otadata"]["offset"]), otadata(0),
                hex(nvs_off), str(tmp / "nvs_blank.bin"),
                hex(slots[0]["offset"]), str(fw),
            ]  # fmt: skip
            nbytes = (
                (args.bundle / "bootloader.bin").stat().st_size
                + ms.TABLE_SIZE
                + lay["otadata"]["size"]
                + lay["nvs"]["size"]
                + fw_size
            )
        elif hits and not args.force:
            prev = (known or {}).get("running")
            slot = prev if prev in hits else hits[0]
            same = prev == slot
            action = "reboot" if same else "switch"
            wipe = args.wipe_settings or (blank_nvs_on_change and not same)
            writes = [hex(lay["otadata"]["offset"]), otadata(slot)]
            nbytes = lay["otadata"]["size"]
        else:
            # Not on the chip (or --force): the least recently used slot, never the one that holds it already.
            slot = hits[0] if hits else ms.pick_victim(state, nslots)
            action, wipe = "write", args.wipe_settings or blank_nvs_on_change
            writes = [
                hex(slots[slot]["offset"]), str(fw),
                hex(lay["otadata"]["offset"]), otadata(slot),
            ]  # fmt: skip
            nbytes = fw_size + lay["otadata"]["size"]
            evicted = state.get(slot, {}).get("label")
            if evicted and evicted != "?":
                print(f"flash: slot {slot} evicted: {evicted}")
        if wipe and action != "convert":
            writes += [hex(nvs_off), str(tmp / "nvs_blank.bin")]
            nbytes += lay["nvs"]["size"]

        r = run_retried(base + ["write_flash", *writes], args.retries)
        if r.returncode != 0:
            return 1

    # esptool hard-reset the chip into the slot. With a console, check it really runs it (and the digest holds).
    if args.console_port and speaks_slots:
        con = None
        try:
            con = ms.Console(args.console_port)
            after = con.wait_running(slot, timeout=30.0)
        except (OSError, ValueError) as e:
            after = None
            print(f"flash: console {args.console_port}: {e}", file=sys.stderr)
        finally:
            if con:
                con.close()
        got = (after or {}).get("slots", {}).get(slot) or {}
        if not after or after.get("running") != slot or got.get("sha256") != want:
            print(
                f"flash: after the write the board doesn't run slot {slot} with this image: {after}",
                file=sys.stderr,
            )
            return 1
    else:
        time.sleep(2)  # let it reboot into the new image
    return done(slot, action, "esptool", nbytes, wipe)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("bundle", type=pathlib.Path)
    ap.add_argument(
        "--port", required=True, help="esptool's port (hil_config [board.*].flash_port or port)"
    )
    ap.add_argument(
        "--console-port",
        default="",
        help="the firmware's command console (hil_config [board.*].port): multi-slot switches over it -- no "
        "esptool -- when the app is already in a slot, and it confirms what booted after an esptool write",
    )
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
        help=f"legacy: erase the settings region ({SETTINGS_REGION[0]}+{SETTINGS_REGION[1]}, or up to the app if "
        "that's lower: NVS -- BLE bonds --, otadata, phy) and write the bundle's images in it; done even when the app "
        "is already on the chip (only those images are rewritten then). Multi-slot: erase the nvs partition only. "
        "For a board changing role, or whose stored bonds are unknown",
    )
    ap.add_argument(
        "--multislot",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("HIL_MULTISLOT", "1") not in ("0", "", "false", "no"),
        help="treat the board's OTA slots as a cache (default; HIL_MULTISLOT=0 turns it off)",
    )
    ap.add_argument("--record", default=os.environ.get("HIL_FLASH_RECORD", ""))
    args = ap.parse_args()

    manifest = json.loads((args.bundle / "manifest.json").read_text())
    chip = manifest["chip"]
    args.baud_eff = baud = args.baud or (115200 if chip in SLOW_CHIPS else 460800)
    for img in manifest["images"]:
        path = args.bundle / img["file"]
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        got = sha256(path)
        if got != img["sha256"]:
            raise SystemExit(f"sha256 mismatch for {img['file']}: {got} != {img['sha256']}")

    base = esptool_cmd() + ["--chip", chip, "--port", args.port, "--baud", str(baud)]
    rec = {
        "board": manifest["board"],
        "profile": manifest["profile"],
        "lib_describe": manifest.get("lib_describe", ""),
        "bundle": args.bundle.name,
        "port": args.port,
    }
    flash_lock()
    t0 = time.monotonic()
    use_slots = (
        args.multislot
        and "slots" not in manifest
        and any(i["file"] == "firmware.bin" for i in manifest["images"])
    )
    rc = multislot(args, manifest, base, rec) if use_slots else legacy(args, manifest, base, rec)
    rec.update(rc=rc, seconds=round(time.monotonic() - t0, 1))
    write_record(args.record, rec)
    if rc == 0:
        # For shell callers (tester/test-matrix.sh): slot, action, method, bytes written.
        print(
            f"flash-slot: {rec.get('slot', '-')} {rec.get('action', '?')} {rec.get('method', '?')} "
            f"{rec.get('bytes_written', 0)}"
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
