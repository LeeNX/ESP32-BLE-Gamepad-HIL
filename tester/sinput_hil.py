#!/usr/bin/env python3
"""SInput HIL on the RPi rig: hil_runner `sinput` profile, observed by hid-generic (raw hidraw) and by the
LeeNX/linux-hid-sinput kernel driver (evdev / power_supply / force feedback), switching drivers with the module
loaded and unloaded while the board stays connected.

Run from ~/ESP32-BLE-Gamepad-HIL with PYTHONPATH=host, under tester/rig-lock.sh, with the board already flashed
with an `sinput` bundle and the driver installed (tester/bootstrap-sinput.sh + `sudo hil-sinput-driver install`).

  sinput_hil.py <serial port> <name contains> [--cycles N]
"""

import argparse
import glob
import json
import os
import pathlib
import re
import select
import struct
import subprocess
import sys
import time

import evdev
import serial
from evdev import ecodes as e

from hil import bluetooth as bt
from hil import pathlatency as pl
from hil.evdev_utils import find_gamepad

ap = argparse.ArgumentParser()
ap.add_argument("port")
ap.add_argument("name")
ap.add_argument("--cycles", type=int, default=5)
ap.add_argument(
    "--latency",
    type=int,
    default=0,
    metavar="N",
    help="also time N presses/releases per driver on every input path at once -- hidraw and evdev under hid-generic, "
    "then under sinput -- and write results/latency-<board>-sinput-*.json (0 = skip)",
)
args = ap.parse_args()

results = []
gaps = []


def gap(name, detail=""):
    gaps.append(name)
    print(f"GAP  {name}" + (f": {detail}" if detail else ""), flush=True)


def check(name, ok, detail=""):
    results.append(ok)
    print(
        ("ok   " if ok else "FAIL ") + name + ("" if ok or not detail else f": {detail}"),
        flush=True,
    )
    return ok


def section(title):
    print(f"\n== {title}", flush=True)


# ---------------------------------------------------------------------------------------------------------------
# Board (serial) and driver control
ser = serial.Serial(args.port, 115200, timeout=0.2)


def cmd(c, timeout=2.0):
    ser.reset_input_buffer()
    ser.write((c + "\n").encode())
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        line = ser.readline().decode(errors="replace").strip()
        if line and not line.startswith(("[", "HIL hil_runner ready")):
            return line
    return None


def driver_ctl(action):
    p = subprocess.run(
        ["sudo", "-n", "hil-sinput-driver", action], capture_output=True, text=True, timeout=60
    )
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def hid_dev():
    """sysfs HID device dir of the (single) BLE SInput device, or None."""
    devs = sorted(glob.glob("/sys/bus/hid/devices/0005:2E8A:10C6.*"))
    return devs[-1] if devs else None


def bound_driver():
    d = hid_dev()
    if not d or not os.path.exists(f"{d}/driver"):
        return None
    return os.path.basename(os.path.realpath(f"{d}/driver"))


def wait_driver(name, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if bound_driver() == name:
            return True
        time.sleep(0.2)
    return False


def dmesg_lines():
    return subprocess.run(["dmesg"], capture_output=True, text=True).stdout.splitlines()


# ---------------------------------------------------------------------------------------------------------------
# Raw observer (hid-generic): /dev/hidrawN
def find_hidraw():
    d = hid_dev()
    if not d:
        return None
    nodes = glob.glob(f"{d}/hidraw/hidraw*")
    return "/dev/" + os.path.basename(nodes[0]) if nodes else None


class Raw:
    def __init__(self):
        self.fd = None
        for _ in range(20):
            node = find_hidraw()
            if node:
                try:
                    self.fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
                    self.node = node
                    return
                except PermissionError:
                    pass  # udev may not have applied the group yet
            time.sleep(0.25)
        raise RuntimeError("no usable hidraw node for 2E8A:10C6")

    def close(self):
        os.close(self.fd)

    def drain(self):
        while select.select([self.fd], [], [], 0)[0]:
            try:
                os.read(self.fd, 128)
            except BlockingIOError:
                break

    def last(self, report_id, wait=0.6):
        got, end = None, time.monotonic() + wait
        while time.monotonic() < end:
            if select.select([self.fd], [], [], 0.05)[0]:
                try:
                    r = os.read(self.fd, 128)
                except BlockingIOError:
                    continue
                if r and r[0] == report_id:
                    got = r
        return got

    def state_after(self, c):
        self.drain()
        reply = cmd(c)
        return (self.last(0x01), reply) if reply == "OK" else (None, reply)

    def send(self, payload):
        self.drain()
        os.write(self.fd, bytes([0x03]) + bytes(payload) + bytes(47 - len(payload)))


def s16(r, i):
    return struct.unpack_from("<h", r, i)[0]


# ESP32-BLE-Gamepad's SInput packing (BleGamepad.cpp): button n -> SInput button bit. Not n-1: 5/6 are the
# bumpers (bits 10/11), 7/8 the stick clicks (8/9), 13 capture (19). D-pad bits 4-7 come from hat 1, and
# start/back/guide (16/17/18) from special buttons 0 (start), 1 (select) and 3 (home).
LIB_BIT = {
    1: 0,
    2: 1,
    3: 2,
    4: 3,
    5: 10,
    6: 11,
    7: 8,
    8: 9,
    9: 12,
    10: 13,
    11: 14,
    12: 15,
    13: 19,
    14: 20,
    15: 21,
    16: 22,
    17: 23,
    18: 24,
    19: 25,
    20: 26,
    21: 27,
    22: 28,
    23: 29,
    24: 30,
    25: 31,
}
SPECIAL_BIT = {0: 16, 1: 17, 3: 18}  # start, select -> back, home -> guide


def raw_checks(raw):
    cmd("RESET")
    bad = []
    for n in range(1, 26):
        r, reply = raw.state_after(f"PRESS {n}")
        want = (3 + LIB_BIT[n] // 8, LIB_BIT[n] % 8)
        pressed = [(i, b) for i in range(3, 7) for b in range(8) if r and r[i] & (1 << b)]
        if pressed != [want]:
            bad.append(f"{n}->{pressed or reply}")
        cmd(f"RELEASE {n}")
    check("raw: buttons 1..25 -> SInput bits (library order)", not bad, " ".join(bad))
    bad = []
    for k, bit in SPECIAL_BIT.items():
        r, reply = raw.state_after(f"SPECIAL PRESS {k}")
        pressed = [8 * (i - 3) + b for i in range(3, 7) for b in range(8) if r and r[i] & (1 << b)]
        if pressed != [bit]:
            bad.append(f"special {k}->{pressed or reply}")
        cmd(f"SPECIAL RELEASE {k}")
    check("raw: start/select/home -> bits 16/17/18", not bad, " ".join(bad))
    for axis, idx, val in (
        ("x", 7, 16000),
        ("y", 9, -16000),
        ("z", 11, 12345),
        ("rz", 13, -32767),
        ("rx", 15, 32767),
        ("ry", 17, 2000),
    ):
        r, reply = raw.state_after(f"AXIS {axis} {val}")
        got = s16(r, idx) if r else reply
        check(f"raw: axis {axis} {val} -> report[{idx}]", got == val, f"got {got}")
        cmd(f"AXIS {axis} 0")
    r, reply = raw.state_after("HAT 1 1")
    check("raw: hat up -> byte 3 bit 4", bool(r and r[3] & 0x10), r[3] if r else reply)
    cmd("HAT 1 0")
    r, reply = raw.state_after("MOTION 100 -200 300 -4096 4096 8192")
    got = [s16(r, i) for i in (23, 25, 27, 29, 31, 33)] if r else reply
    check(
        "raw: motion -> accel 23/25/27, gyro 29/31/33",
        got == [-4096, 4096, 8192, 100, -200, 300],
        f"got {got}",
    )
    check("raw: imu timestamp nonzero", bool(r and struct.unpack_from("<I", r, 19)[0]))
    r, reply = raw.state_after("TOUCH 0 1000 -1000 500")
    got = [s16(r, 35), s16(r, 37), struct.unpack_from("<H", r, 39)[0]] if r else reply
    check("raw: touch finger 0 -> 35/37/39", got == [1000, -1000, 500], f"got {got}")
    cmd("BATTERY 77")  # sets the level without sending a report; the next state report carries it
    r, reply = raw.state_after("AXIS x 1")
    check("raw: battery 77 -> report[2]", bool(r and r[2] == 77), f"{r[1:3].hex() if r else reply}")
    cmd("AXIS x 0")
    cmd("RESET")

    raw.send([0x02])
    f = raw.last(0x02, wait=1.5)
    if check("raw: features response", bool(f and f[1] == 0x02), f[:4].hex() if f else "none"):
        print(f"      features: {f[:24].hex(' ')}")
        check(
            "raw: features caps0=0xff, caps1 touch+rgb",
            f[4] == 0xFF and (f[5] & 0x03) == 0x03,
            f"caps0=0x{f[4]:02x} caps1=0x{f[5]:02x}",
        )
        check(
            "raw: features poll/accel/gyro 5000/8/2000",
            struct.unpack_from("<HHH", f, 8) == (5000, 8, 2000),
            f"{struct.unpack_from('<HHH', f, 8)}",
        )
    cmd("RUMBLE?"), cmd("RGB?"), cmd("PLED?")  # clear the received flags
    raw.send([0x01, 0x02, 200, 0, 40, 0])
    time.sleep(0.5)
    got = cmd("RUMBLE?")
    check(
        "raw: haptic -> device left=200 right=40",
        got is not None and "left=200 right=40" in got,
        got,
    )
    raw.send([0x04, 10, 20, 30])
    time.sleep(0.5)
    got = cmd("RGB?")
    check("raw: rgb -> device", got is not None and "r=10 g=20 b=30" in got, got)
    raw.send([0x03, 2])
    time.sleep(0.5)
    got = cmd("PLED?")
    check("raw: player LED 2 -> device", got is not None and got.endswith(" 2"), got)


# ---------------------------------------------------------------------------------------------------------------
# Driver observer (sinput): evdev, power_supply, FF
def sinput_inputs(timeout=10):
    """{'pad': InputDevice, 'imu': ..., 'touch': ...} for the sinput driver's input devices."""
    end = time.monotonic() + timeout
    found = {}
    while time.monotonic() < end:
        found = {}
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
            except OSError:
                continue
            if not d.name.startswith("SInput "):
                continue
            if d.name == "SInput Gamepad":
                found["pad"] = d
            elif d.name == "SInput IMU":
                found["imu"] = d
            elif "ouch" in d.name:
                found["touch"] = d
        if "pad" in found:
            time.sleep(0.3)
            return found
        time.sleep(0.25)
    return found


def settle(*devs, wait=0.4):
    """Let the kernel apply queued events; absinfo/active_keys read the current state."""
    end = time.monotonic() + wait
    while time.monotonic() < end:
        for d in devs:
            try:
                while d.read_one():
                    pass
            except (BlockingIOError, OSError):
                pass
        time.sleep(0.02)


# linux-hid-sinput's mapping (sinput_input.c), by SInput button bit. Bits it doesn't map are reported as gaps.
DRIVER_KEY = {
    0: e.BTN_SOUTH,
    1: e.BTN_EAST,
    2: e.BTN_WEST,
    3: e.BTN_NORTH,
    4: e.BTN_DPAD_UP,
    5: e.BTN_DPAD_DOWN,
    6: e.BTN_DPAD_LEFT,
    7: e.BTN_DPAD_RIGHT,
    8: e.BTN_THUMBL,
    9: e.BTN_THUMBR,
    10: e.BTN_TL,
    11: e.BTN_TR,
    12: e.BTN_TL2,
    13: e.BTN_TR2,
    16: e.BTN_START,
    17: e.BTN_SELECT,
    18: e.BTN_MODE,
    19: e.BTN_MISC,
}
# Added in linux-hid-sinput after 0.3.0: paddles -> BTN_GRIPL/GRIPR/GRIPL2/GRIPR2 (hid-steam's back levers), power and
# misc 4-10 -> BTN_TRIGGER_HAPPY1..8. Optional, so the test runs against older drivers too: an unmapped bit is a GAP,
# the expected code is a pass, anything else fails. Numeric fallbacks for python-evdev builds older than the codes.
_GRIPL = getattr(e, "BTN_GRIPL", 0x224)
_HAPPY1 = e.BTN_TRIGGER_HAPPY1
DRIVER_KEY_OPTIONAL = {
    14: _GRIPL,
    15: _GRIPL + 1,
    20: _GRIPL + 2,
    21: _GRIPL + 3,
    **{bit: _HAPPY1 + (bit - 24) for bit in range(24, 32)},
}
# Touchpad clicks report BTN_LEFT on the touchpad input device, not the gamepad. With one touchpad (this
# firmware: 1 pad, 2 fingers) only touchpad 1's click exists; bit 23 is ignored by design.
TOUCHPAD_CLICK_BITS = {22: True, 23: False}
SINPUT_BIT_NAMES = {
    14: "L paddle 1",
    15: "R paddle 1",
    20: "L paddle 2",
    21: "R paddle 2",
    24: "power",
}


def driver_checks(dev):
    pad, imu, touch = dev.get("pad"), dev.get("imu"), dev.get("touch")
    check(
        "driver: SInput Gamepad / IMU / touchpad input devices",
        bool(pad and imu and touch),
        f"found {sorted(dev)}",
    )
    if not pad:
        return
    for k, d in (("imu", imu), ("touch", touch)):
        if d and d.phys != pad.phys:
            check(f"driver: {k} shares the gamepad's phys", False, f"{d.phys} != {pad.phys}")
        if d and (d.info.vendor, d.info.product) != (pad.info.vendor, pad.info.product):
            gap(
                f"driver: {k} input device has no vendor/product",
                f"{d.info.vendor:04x}:{d.info.product:04x} vs gamepad {pad.info.vendor:04x}:{pad.info.product:04x}; "
                "hid-playstation copies the HID ids so userspace can pair sensors with the gamepad",
            )
    cmd("RESET")
    bad, unmapped = [], []
    presses = [(f"PRESS {n}", f"RELEASE {n}", LIB_BIT[n]) for n in range(1, 26)]
    presses += [
        (f"SPECIAL PRESS {k}", f"SPECIAL RELEASE {k}", bit) for k, bit in SPECIAL_BIT.items()
    ]
    presses += [(f"HAT 1 {h}", "HAT 1 0", bit) for h, bit in ((1, 4), (5, 5), (7, 6), (3, 7))]
    mapped_optional, click_bad = 0, []
    for press, release, bit in presses:
        cmd(press)
        settle(pad, *([touch] if touch else []))
        keys = pad.active_keys()
        if bit in DRIVER_KEY:
            if keys != [DRIVER_KEY[bit]]:
                bad.append(f"bit {bit} ({press})->{keys}")
        elif bit in TOUCHPAD_CLICK_BITS:
            if keys:
                bad.append(f"touchpad click bit {bit} ({press}) on the gamepad: {keys}")
            clicked = bool(touch and e.BTN_LEFT in touch.active_keys())
            if clicked != TOUCHPAD_CLICK_BITS[bit]:
                click_bad.append(f"bit {bit}: BTN_LEFT {'set' if clicked else 'not set'}")
        elif keys == [DRIVER_KEY_OPTIONAL.get(bit)]:
            mapped_optional += 1
        elif keys:
            bad.append(
                f"bit {bit} ({press}) produced {keys}, expected {DRIVER_KEY_OPTIONAL.get(bit)} or nothing"
            )
        else:
            unmapped.append(SINPUT_BIT_NAMES.get(bit, f"misc bit {bit}"))
        cmd(release)
    settle(pad)
    check(
        f"driver: {len(DRIVER_KEY) + mapped_optional} mapped SInput buttons -> BTN_* "
        f"(incl. d-pad, start/back/guide; {mapped_optional} paddle/power/misc)",
        not bad,
        " ".join(bad),
    )
    if touch:
        check(
            "driver: touchpad 1 click -> BTN_LEFT on the touchpad (touchpad 2: none, 1 pad)",
            not click_bad,
            "; ".join(click_bad),
        )
    if unmapped:
        gap("driver: SInput buttons with no evdev code", ", ".join(unmapped))

    # hil_runner axes: x/y left stick, z/rz right stick, rx/ry triggers. Driver: ABS_X/Y left, ABS_RX/RY right,
    # ABS_Z/RZ triggers.
    for axis, code, val in (
        ("x", e.ABS_X, 16000),
        ("y", e.ABS_Y, -16000),
        ("z", e.ABS_RX, 12345),
        ("rz", e.ABS_RY, -32767),
        ("rx", e.ABS_Z, 32767),
        ("ry", e.ABS_RZ, 2000),
    ):
        cmd(f"AXIS {axis} {val}")
        settle(pad)
        got = pad.absinfo(code).value
        check(f"driver: axis {axis} {val} -> {e.ABS[code]}", got == val, f"got {got}")
        cmd(f"AXIS {axis} 0")

    if imu:
        props = imu.input_props()
        check(
            "driver: IMU has INPUT_PROP_ACCELEROMETER",
            e.INPUT_PROP_ACCELEROMETER in props,
            f"{props}",
        )
        cmd("MOTION 100 -200 300 -4096 4096 8192")
        settle(imu)
        got = [
            imu.absinfo(c).value for c in (e.ABS_X, e.ABS_Y, e.ABS_Z, e.ABS_RX, e.ABS_RY, e.ABS_RZ)
        ]
        check(
            "driver: IMU accel/gyro values",
            got == [-4096, 4096, 8192, 100, -200, 300],
            f"got {got}",
        )
        res = (imu.absinfo(e.ABS_X).resolution, imu.absinfo(e.ABS_RX).resolution)
        check(
            "driver: IMU resolution 4096/g (8 g), 16/dps (2000 dps)",
            res == (4096, 16),
            f"got {res}",
        )
        cmd("MOTION 0 0 0 0 0 0")

    if touch:
        # absinfo() of an MT axis reads the *current* slot, which ends on finger 2 -- read the event stream instead.
        settle(touch)
        cmd("TOUCH 0 1000 -1000 500")
        seen, slot, end = {}, 0, time.monotonic() + 0.6
        while time.monotonic() < end:
            try:
                ev = touch.read_one()
            except (BlockingIOError, OSError):
                ev = None
            if ev is None:
                time.sleep(0.02)
                continue
            if ev.type == e.EV_ABS and ev.code == e.ABS_MT_SLOT:
                slot = ev.value
            elif ev.type == e.EV_ABS and slot == 0:
                seen[ev.code] = ev.value
        got = (
            seen.get(e.ABS_MT_POSITION_X),
            seen.get(e.ABS_MT_POSITION_Y),
            seen.get(e.ABS_MT_PRESSURE),
        )
        check("driver: touchpad slot 0 x/y/pressure", got == (1000, -1000, 500), f"got {got}")
        cmd("TOUCH 0 0 0 0")

    cmd("BATTERY 77")
    time.sleep(0.6)
    caps = glob.glob("/sys/class/power_supply/sinput-battery-*/capacity")
    got = open(caps[0]).read().strip() if caps else None
    check("driver: power_supply capacity 77", got == "77", f"got {got} ({caps})")

    # Force feedback: strong -> left, weak -> right, 16-bit -> 8-bit (same as SDL and Bluepad32).
    cmd("RUMBLE?")
    rumble = evdev.ff.Rumble(strong_magnitude=0xC800, weak_magnitude=0x2800)
    effect = evdev.ff.Effect(
        e.FF_RUMBLE,
        -1,
        0,
        evdev.ff.Trigger(0, 0),
        evdev.ff.Replay(1000, 0),
        evdev.ff.EffectType(ff_rumble_effect=rumble),
    )
    try:
        eid = pad.upload_effect(effect)
        pad.write(e.EV_FF, eid, 1)
        time.sleep(0.5)
        got = cmd("RUMBLE?")
        check(
            "driver: FF rumble -> device left=200 right=40",
            got is not None and "left=200 right=40" in got,
            got,
        )
        pad.write(e.EV_FF, eid, 0)
        time.sleep(0.5)
        got = cmd("RUMBLE?")
        check(
            "driver: FF stop -> device left=0 right=0",
            got is not None and "left=0 right=0" in got,
            got,
        )
        pad.erase_effect(eid)
    except OSError as ex:
        check("driver: FF rumble", False, repr(ex))
    cmd("RESET")


# ---------------------------------------------------------------------------------------------------------------
section("board")
ident = cmd("ID?")
check("serial: sinput profile", ident is not None and "profile=sinput" in ident, ident)
print("     ", cmd("NAME?"), "|", cmd("PNP?"))
ok, out = driver_ctl("status")
print("     ", out.replace("\n", " | "))


latency = {}  # driver -> pathlatency.measure() result


def key_code(evdev_dev):
    """The key code button 1 produces on this node -- hid-generic and the sinput driver map it differently -- from a
    probe press, so the timing only counts that key."""
    src = pl.EvdevKey("probe", evdev_dev)
    src.drain()
    cmd("PRESS 1")
    code, end = None, time.monotonic() + 1.0
    while code is None and time.monotonic() < end:
        if select.select([evdev_dev.fd], [], [], 0.05)[0]:
            for ev in evdev_dev.read():
                if ev.type == e.EV_KEY and ev.value == 1:
                    code = ev.code
                    break
    cmd("RELEASE 1")
    time.sleep(0.1)
    src.drain()
    return code


def time_paths(driver, raw, evdev_dev):
    """Button 1 (SInput bit 0: report 0x01, byte 3, bit 0) on hidraw and on `driver`'s evdev node, per press."""
    sources = [pl.HidrawBit(f"hidraw/{driver}", raw.fd, 0x01, 3, 0)]
    code = key_code(evdev_dev)
    if code is None:
        # No key event from the probe press: an EvdevKey without a code would time any key, so leave evdev out.
        print(f"      evdev/{driver}: button 1 produced no key event -- evdev not timed")
        gap(
            f"latency: evdev/{driver} not measured",
            "the probe press of button 1 produced no key event",
        )
    else:
        print(f"      evdev/{driver}: button 1 = key {code}")
        sources.append(pl.EvdevKey(f"evdev/{driver}", evdev_dev, code=code))
    cmd("RESET")
    # Send without waiting for the OK: a bridge's reply delay (the esp32c3's FTDI holds input up to 16 ms) would
    # otherwise land in every sample. cmd() below resets the input buffer, dropping the unread OKs.
    res = pl.measure(
        lambda want: ser.write(b"PRESS 1\n" if want else b"RELEASE 1\n"), sources, n=args.latency
    )
    cmd("RESET")
    for line in pl.describe(res, baseline=f"hidraw/{driver}"):
        print(f"LATENCY {line}", flush=True)
    latency[driver] = res


def ping_ms(n=20):
    """Serial PING round trip: the share of every figure that is the command path, not BLE or the host stack."""
    out = []
    for _ in range(n):
        t = time.perf_counter()
        if cmd("PING") == "PONG":
            out.append((time.perf_counter() - t) * 1000)
    return pl.Stats.of(out)


def versions():
    """One VERSIONS line for the report: what this run actually tested against."""

    def run(*a):
        try:
            return subprocess.run(a, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""

    kernel = os.uname().release
    mod = "/sys/module/sinput"
    loaded = os.path.isdir(mod)
    drv = open(f"{mod}/version").read().strip() if os.path.exists(f"{mod}/version") else ""
    drv = drv or run("/usr/sbin/modinfo", "-F", "version", "sinput") or "?"
    src = open(f"{mod}/srcversion").read().strip() if os.path.exists(f"{mod}/srcversion") else ""
    pkg = run("dpkg-query", "-W", "-f", "${Version}", f"sinput-modules-{kernel}") or "not installed"
    cfg = cmd("CONFIG?") or ""
    lib = (re.search(r"libsha=(\S+)", cfg) or [None, "?"])[1]
    bluez = run("bluetoothctl", "--version").replace("bluetoothctl: ", "") or "?"
    return (
        f"kernel={kernel} sinput={drv}(pkg {pkg}{', srcversion ' + src if src else ''}"
        f"{'' if loaded else ', not loaded'}) fw=sinput/{lib} bluez={bluez}"
    )


print("VERSIONS", versions(), flush=True)
dmesg_start = len(dmesg_lines())

section("pair (fresh: the descriptor differs from the board's usual profile)")
with bt.BtCtl() as btctl:
    mac = bt.ensure_paired(btctl, args.name, want_fresh=True)
check("BLE paired + services resolved", bool(mac), mac)

section("A: hid-generic (raw reports)")
# The HID device can land after pairing returns (the esp32dev resolves its GATT services slowest). Unloading before
# it binds lets sinput's modalias autoload bind it afterwards, and hid-generic never gets it.
end = time.monotonic() + 15
while bound_driver() is None and time.monotonic() < end:
    time.sleep(0.2)
if not check(
    "HID device bound after pairing",
    bound_driver() is not None,
    f"{hid_dev() or 'no device'}, 15 s",
):
    # Unloading now would only bring back the race this wait is for.
    print(f"FAILED ({results.count(False)} failures of {len(results)})")
    sys.exit(1)
print(
    f"      after pairing: bound to {bound_driver()} (a new device autoloads sinput via its modalias)"
)
ok, out = driver_ctl("unload")
check("hil-sinput-driver unload", ok, out)
check(
    "hid-generic takes the device back (no reconnect)", wait_driver("hid-generic"), bound_driver()
)
check("BLE link stayed up", bt.is_connected(mac))
raw = Raw()
print(f"      {raw.node}")
raw_checks(raw)
if args.latency:
    time_paths("hid-generic", raw, find_gamepad(args.name))
raw.close()

section("B: load sinput while connected")
ok, out = driver_ctl("load")
check("hil-sinput-driver load", ok, out)
check(
    "sinput takes the connected device over (no reconnect)", wait_driver("sinput"), bound_driver()
)
check("BLE link stayed up", bt.is_connected(mac))
dev = sinput_inputs()
driver_checks(dev)
if args.latency and "pad" in dev:
    raw = Raw()
    time_paths("sinput", raw, dev["pad"])
    raw.close()
for d in dev.values():
    d.close()

section(f"C: {args.cycles} unload/load cycles while connected")
cycle_bad = []
for i in range(args.cycles):
    ok1, out1 = driver_ctl("unload")
    gen = wait_driver("hid-generic")
    r = None
    if gen:
        raw = Raw()
        r, _ = raw.state_after("PRESS 1")
        cmd("RELEASE 1")
        raw.close()
    ok2, out2 = driver_ctl("load")
    sin = wait_driver("sinput")
    keys = None
    if sin:
        dev = sinput_inputs()
        pad = dev.get("pad")
        if pad:
            cmd("PRESS 1")
            settle(pad)
            keys = pad.active_keys()
            cmd("RELEASE 1")
        for d in dev.values():
            d.close()
    good = (
        ok1
        and gen
        and bool(r and r[3] & 1)
        and ok2
        and sin
        and keys == [e.BTN_SOUTH]
        and bt.is_connected(mac)
    )
    if not good:
        cycle_bad.append(
            f"#{i + 1}: unload={ok1} generic={gen} raw={bool(r and r[3] & 1)} "
            f"load={ok2} sinput={sin} keys={keys} link={bt.is_connected(mac)}"
        )
check(
    f"{args.cycles} cycles: driver switches both ways, input works in each, link stays up",
    not cycle_bad,
    "; ".join(cycle_bad),
)

new = dmesg_lines()[dmesg_start:]
alarms = [
    ln
    for ln in new
    if re.search(r"WARNING|BUG|Oops|refcount|use-after-free|leak|Call trace|kernel BUG", ln)
]
check("dmesg: no warnings/oopses during the run", not alarms, " | ".join(alarms[:5]))
print(f"      dmesg: {len(new)} new lines, {sum('sinput' in ln for ln in new)} from sinput")

if latency:
    board = args.name.split()[-1]
    rec = {
        "board": board,
        "profile": "sinput",
        "stimulus": "button 1 press/release over serial; timed from just before the write",
        "versions": versions(),
        "ping_ms": ping_ms(),
        "paths": {label: r for res in latency.values() for label, r in res.items()},
    }
    out = pathlib.Path("results") / f"latency-{board}-sinput-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rec, indent=2) + "\n")
    print(f"      latency -> {out}")

cmd("RESET")
print(f"\nMAC {mac}")
print(f"GAPS ({len(gaps)}): " + "; ".join(gaps) if gaps else "GAPS: none")
print(
    "PASSED" if all(results) else "FAILED", f"({results.count(False)} failures of {len(results)})"
)
sys.exit(0 if all(results) else 1)
