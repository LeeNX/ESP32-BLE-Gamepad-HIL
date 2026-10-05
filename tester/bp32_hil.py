#!/usr/bin/env python3
"""Bluepad32 as the observer: hil_runner's `sinput` profile on one board, Bluepad32 (SInput parser) on another.

The gamepad board is driven over its serial port; the Bluepad32 board runs the HIL host firmware from the Bluepad32
HIL rig (leenx-foss/antBot-hil host/, built without NuS/OTA, with HIL_ALLOW_ADDR baked in) and prints what it parsed
as `HIL ...` lines on its serial console. Both boards are already flashed; run under tester/rig-lock.sh.

  bp32_hil.py <gamepad serial port> <bluepad32 serial port>

Output: `ok` / `FAIL` per check, `GAP` for things Bluepad32 doesn't expose (not counted as failures).
"""

import argparse
import re
import sys
import threading
import time

import serial

ap = argparse.ArgumentParser()
ap.add_argument("gamepad_port")
ap.add_argument("bp32_port")
ap.add_argument(
    "--settle", type=float, default=0.5, help="seconds to wait for Bluepad32 after each change"
)
ap.add_argument(
    "--profile",
    choices=("sinput", "minimal", "maxbtn", "maxfeat"),
    default="sinput",
    help="the gamepad's hil_runner profile; minimal/maxbtn go through Bluepad32's generic HID parser",
)
ap.add_argument(
    "--addr",
    default="",
    help="gamepad BLE address for the observer's `allow` filter (default: ask the gamepad, ADDR?)",
)
args = ap.parse_args()

results, gaps = [], []


def check(name, ok, detail=""):
    results.append(ok)
    print(
        ("ok   " if ok else "FAIL ") + name + ("" if ok or not detail else f": {detail}"),
        flush=True,
    )
    return ok


def gap(name, detail=""):
    gaps.append(name)
    print(f"GAP  {name}" + (f": {detail}" if detail else ""), flush=True)


def section(title):
    print(f"\n== {title}", flush=True)


# ---------------------------------------------------------------------------------------------------------------
# Gamepad board: hil_runner, sinput profile
pad = serial.Serial(args.gamepad_port, 115200, timeout=0.2)


def cmd(c, timeout=2.0):
    pad.reset_input_buffer()
    pad.write((c + "\n").encode())
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        line = pad.readline().decode(errors="replace").strip()
        if line and not line.startswith(("[", "HIL hil_runner ready")):
            return line
    return None


# ---------------------------------------------------------------------------------------------------------------
# Bluepad32 board: HIL host console
class Bluepad32:
    def __init__(self, port):
        # Open with the board held in reset (RTS -> EN), so it only starts scanning once the gamepad is ready.
        self.ser = serial.Serial()
        self.ser.port, self.ser.baudrate, self.ser.timeout = port, 115200, 0.2
        self.ser.dtr, self.ser.rts = False, True
        self.ser.open()
        self.lock = threading.Lock()
        self.state, self.ready, self.features, self.log = None, None, None, []
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            line = self.ser.readline().decode(errors="replace").strip()
            if not line.startswith("HIL "):
                continue
            with self.lock:
                self.log.append(line)
                if line.startswith("HIL state"):
                    self.state = {k: int(v, 0) for k, v in re.findall(r"(\w+)=(-?\w+)", line)}
                elif line.startswith("HIL ready"):
                    self.ready = line
                elif line.startswith("HIL features"):
                    self.features = (
                        {k: int(v, 0) for k, v in re.findall(r"(\w+)=(\w+)", line)}
                        if "none" not in line
                        else None
                    )

    def release_reset(self):
        self.ser.rts = False

    def send(self, line):
        self.ser.write((line + "\n").encode())

    def mark(self):
        with self.lock:
            return len(self.log)

    def wait_for(self, prefix, since, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                for line in self.log[since:]:
                    if line.startswith(prefix):
                        return line
            time.sleep(0.05)
        return None

    def snapshot(self):
        with self.lock:
            return dict(self.state) if self.state else {}


# Bluepad32 constants (uni_gamepad.h)
BTN_A, BTN_B, BTN_X, BTN_Y = 0x0001, 0x0002, 0x0004, 0x0008
BTN_SHOULDER_L, BTN_SHOULDER_R, BTN_TRIGGER_L, BTN_TRIGGER_R = 0x0010, 0x0020, 0x0040, 0x0080
BTN_THUMB_L, BTN_THUMB_R = 0x0100, 0x0200
DPAD_UP, DPAD_DOWN, DPAD_RIGHT, DPAD_LEFT = 1, 2, 4, 8
MISC_SYSTEM, MISC_SELECT, MISC_START, MISC_CAPTURE = 1, 2, 4, 8

# hil_runner button n (ESP32-BLE-Gamepad's SInput packing) -> what Bluepad32's SInput parser reports.
# None = Bluepad32 has no field for it (paddles, touchpad clicks, power, misc): reported as a gap.
BUTTONS = {
    1: ("btn", BTN_A),
    2: ("btn", BTN_B),
    3: ("btn", BTN_X),
    4: ("btn", BTN_Y),
    5: ("btn", BTN_SHOULDER_L),
    6: ("btn", BTN_SHOULDER_R),
    7: ("btn", BTN_THUMB_L),
    8: ("btn", BTN_THUMB_R),
    9: ("btn", BTN_TRIGGER_L),
    10: ("btn", BTN_TRIGGER_R),
    13: ("misc", MISC_CAPTURE),
}
BUTTON_NAMES = {
    11: "L paddle 1",
    12: "R paddle 1",
    14: "L paddle 2",
    15: "R paddle 2",
    16: "touchpad 1 click",
    17: "touchpad 2 click",
    18: "power",
}
SPECIALS = {
    0: ("start", MISC_START),
    1: ("select/back", MISC_SELECT),
    3: ("home/guide", MISC_SYSTEM),
}
HATS = {1: DPAD_UP, 5: DPAD_DOWN, 3: DPAD_RIGHT, 7: DPAD_LEFT, 4: DPAD_DOWN | DPAD_RIGHT}

# AXIS <name> <int16>: x/y left stick, z/rz right stick, rx/ry triggers. Bluepad32: sticks -512..511 (>> 6),
# triggers 0..1023 (>> 5).
AXES = [
    ("x", 16000, "lx", 250),
    ("y", -16000, "ly", -250),
    ("z", -32767, "rx", -512),
    ("rz", 32767, "ry", 511),
    ("rx", 16384, "brake", 512),
    ("ry", 32767, "thr", 1023),
]
# MOTION <gx> <gy> <gz> <ax> <ay> <az> in raw counts; +/-8 g and +/-2000 dps, so 4096 = 1 g = 9807 mm/s^2 and
# 16384 = 1000 dps = 17453 mrad/s. Bluepad32's frame (X right, Y up, Z toward the player) = SDL's: (-x, +z, -y).
IMU = [
    ("flat at rest (+1 g up)", "MOTION 0 0 0 0 0 4096", {"ax": 0, "ay": 9807, "az": 0}),
    ("accel axes", "MOTION 0 0 0 -4096 4096 8192", {"ax": 9807, "ay": 19613, "az": -9807}),
    ("gyro axes", "MOTION -16384 16384 -8192 0 0 0", {"gx": 17453, "gy": -8727, "gz": -17453}),
    (
        "full scale",
        "MOTION 32767 -32768 0 32767 -32768 0",
        {"ax": -78451, "az": 78453, "gx": -34906, "gz": 34907},
    ),
]
TOL = {
    "lx": 2,
    "ly": 2,
    "rx": 2,
    "ry": 2,
    "brake": 2,
    "thr": 2,
    "ax": 5,
    "ay": 5,
    "az": 5,
    "gx": 5,
    "gy": 5,
    "gz": 5,
}


def expect(name, after, want):
    """Run gamepad command(s), let Bluepad32 settle, compare its state line."""
    for c in [after] if isinstance(after, str) else after:
        reply = cmd(c)
        if reply != "OK":
            return check(name, False, f"'{c}' -> {reply}")
    time.sleep(args.settle)
    got = bp.snapshot()
    bad = {
        k: (v, got.get(k))
        for k, v in want.items()
        if got.get(k) is None or abs(got[k] - v) > TOL.get(k, 0)
    }
    return check(name, not bad, f"expected/got {bad}")


# ---------------------------------------------------------------------------------------------------------------
section("boards")
ident = cmd("ID?")
check(
    f"gamepad: {args.profile} profile",
    ident is not None and f"profile={args.profile}" in ident,
    ident,
)
print("     ", cmd("NAME?"), "|", cmd("PNP?"))
addr = args.addr
if not addr:
    reply = cmd("ADDR?") or ""
    addr = reply.split()[1] if reply.startswith("ADDR ") else ""
print(f"      gamepad BLE address: {addr or '(unknown: relying on the observer image filter)'}")
cmd("RESET")

bp = Bluepad32(args.bp32_port)
time.sleep(0.5)
bp.release_reset()
section("Bluepad32 pairs with the gamepad")
if addr:
    # The observer ignores HILpad* names until told otherwise; its filter runs on every advertisement.
    time.sleep(2.5)
    m = bp.mark()
    bp.send(f"allow {addr}")
    check("Bluepad32: allow filter set", bp.wait_for("HIL ok allow", m) is not None)
for _ in range(90):
    if bp.ready:
        break
    time.sleep(1)
print(f"      {bp.ready}")
m = bp.mark()
bp.send("version?")
bp32_build = (bp.wait_for("HIL version", m) or "HIL version ?").split()[-1]
cfg_line = cmd("CONFIG?") or ""
lib = (re.search(r"libsha=(\S+)", cfg_line) or [None, "?"])[1]
print(f"VERSIONS bluepad32={bp32_build} fw={args.profile}/{lib}", flush=True)
want_ids = "type=58 vid=0x2e8a pid=0x10c6" if args.profile == "sinput" else "vid=0x1d34 pid=0x8010"
if not check(
    f"Bluepad32: device ready ({want_ids})",
    bool(bp.ready and all(t in bp.ready for t in want_ids.split())),
    bp.ready,
):
    with bp.lock:
        print("\n".join(bp.log[-15:]))
    sys.exit(1)
time.sleep(1.5)  # feature response / first state report


def run_sinput():
    section("features")
    f = bp.features
    if check("Bluepad32: feature response parsed", bool(f), f"{f}"):
        check(
            "features: caps0=0xff caps1 touch+rgb",
            f.get("caps0") == 0xFF and (f.get("caps1", 0) & 0x03) == 0x03,
            f"{f}",
        )
        check(
            "features: IMU poll/ranges 5000us/8g/2000dps",
            (f.get("poll"), f.get("accel"), f.get("gyro")) == (5000, 8, 2000),
            f"{f}",
        )

    section("inputs")
    expect(
        "idle",
        "RESET",
        {"btn": 0, "misc": 0, "dpad": 0, "lx": 0, "ly": 0, "rx": 0, "ry": 0, "brake": 0, "thr": 0},
    )
    missing = []
    for n in range(1, 26):
        want = BUTTONS.get(n)
        cmd(f"PRESS {n}")
        time.sleep(args.settle)
        got = bp.snapshot()
        if want:
            field, bit = want
            others = "misc" if field == "btn" else "btn"
            check(
                f"button {n} -> {field}=0x{bit:x}",
                got.get(field) == bit and got.get(others) == 0,
                f"btn={got.get('btn')} misc={got.get('misc')}",
            )
        elif got.get("btn") or got.get("misc"):
            check(
                f"button {n} (no Bluepad32 field) changes nothing",
                False,
                f"btn={got.get('btn')} misc={got.get('misc')}",
            )
        else:
            missing.append(BUTTON_NAMES.get(n, f"misc {n - 18}"))
        cmd(f"RELEASE {n}")
    if missing:
        gap("Bluepad32: SInput buttons with no gamepad field", ", ".join(missing))
    for k, (name, bit) in SPECIALS.items():
        expect(f"special {name} -> misc=0x{bit:x}", f"SPECIAL PRESS {k}", {"misc": bit, "btn": 0})
        cmd(f"SPECIAL RELEASE {k}")
    for h, bits in HATS.items():
        expect(f"hat {h} -> dpad=0x{bits:x}", f"HAT 1 {h}", {"dpad": bits})
    cmd("HAT 1 0")
    for axis, raw, field, val in AXES:
        expect(f"axis {axis} {raw} -> {field}={val}", f"AXIS {axis} {raw}", {field: val})
        cmd(f"AXIS {axis} 0")
    expect("imu idle", "MOTION 0 0 0 0 0 0", {"ax": 0, "ay": 0, "az": 0, "gx": 0, "gy": 0, "gz": 0})
    for name, c, want in IMU:
        expect(f"imu {name}", c, want)
    cmd("RESET")
    cmd("TOUCH 0 1000 -1000 500")
    gap("Bluepad32: touchpad has no API (uni_gamepad_t has no touch fields)")
    cmd("RESET")

    section("Bluepad32 -> gamepad commands")
    cmd("PLED?"), cmd("RGB?"), cmd("RUMBLE?")  # clear the received flags

    def host(c, ack):
        m = bp.mark()
        bp.send(c)
        return bp.wait_for(ack, m)

    for n in (1, 2, 3, 4, 0):
        ack = host(f"led {n}", "HIL ok led")
        time.sleep(args.settle + 0.4)
        got = cmd("PLED?")
        check(
            f"player LED {n}",
            ack is not None and got is not None and got.endswith(f" {n}"),
            f"ack={ack} gamepad={got}",
        )
    for r, g, b in ((255, 0, 128), (0, 255, 0), (1, 2, 3), (0, 0, 0)):
        ack = host(f"rgb {r} {g} {b}", "HIL ok rgb")
        time.sleep(args.settle + 0.4)
        got = cmd("RGB?")
        check(
            f"RGB {r},{g},{b}",
            ack is not None and got is not None and f"r={r} g={g} b={b}" in got,
            f"ack={ack} gamepad={got}",
        )
    # Rumble: SInput haptic type 2, left = strong (low frequency), right = weak (high frequency), like SDL.
    for weak, strong in ((0, 200), (120, 0), (77, 255)):
        ack = host(f"rumble {weak} {strong} 5000", "HIL ok rumble")
        time.sleep(args.settle + 0.4)
        got = cmd("RUMBLE?")
        check(
            f"rumble weak={weak} strong={strong}",
            ack is not None and got is not None and f"left={strong} right={weak}" in got,
            f"ack={ack} gamepad={got}",
        )
    ack = host("rumble 0 0 0", "HIL ok rumble")
    time.sleep(args.settle + 0.4)
    got = cmd("RUMBLE?")
    check(
        "rumble stop",
        ack is not None and got is not None and "left=0 right=0" in got,
        f"ack={ack} gamepad={got}",
    )
    ack = host("rumble 50 60 2000", "HIL ok rumble")
    time.sleep(args.settle + 0.4)
    got = cmd("RUMBLE?")
    check(
        "rumble timed: on",
        ack is not None and got is not None and "left=60 right=50" in got,
        f"gamepad={got}",
    )
    time.sleep(2.0)
    got = cmd("RUMBLE?")
    check(
        "rumble timed: stops after duration",
        got is not None and "left=0 right=0" in got,
        f"gamepad={got}",
    )


# Which parser Bluepad32 picks for a gamepad it doesn't recognise: on BLE that's the Android profile
# (uni_hid_device.c: "Fallback: using Android profile", CONTROLLER_TYPE_AndroidController = 37), whose
# parser differs from uni_hid_parser_generic.c -- so the expectations follow the `type=` Bluepad32 reports.
#
# Generic parser: HID buttons 1,2,4,5,7,8 -> A,B,X,Y,L,R; 11,12,13 -> select, start, system; 14,15 -> thumb
# L/R; others dropped. Axes X,Y -> lx,ly; Z and Rx both -> rx; Ry (pedal) and Rz both -> ry.
GENERIC_BUTTONS = {
    1: ("btn", BTN_A),
    2: ("btn", BTN_B),
    4: ("btn", BTN_X),
    5: ("btn", BTN_Y),
    7: ("btn", BTN_SHOULDER_L),
    8: ("btn", BTN_SHOULDER_R),
    11: ("misc", MISC_SELECT),
    12: ("misc", MISC_START),
    13: ("misc", MISC_SYSTEM),
    14: ("btn", BTN_THUMB_L),
    15: ("btn", BTN_THUMB_R),
}


# Android parser (uni_hid_parser_android.c): as generic, plus 9,10 -> trigger L/R (and 19,20 -> trigger L/R,
# Stadia). Axes X,Y -> lx,ly; Z -> rx; Rz -> ry; Rx and Ry aren't read.
ANDROID_BUTTONS = {
    **GENERIC_BUTTONS,
    9: ("btn", BTN_TRIGGER_L),
    10: ("btn", BTN_TRIGGER_R),
    19: ("btn", BTN_TRIGGER_L),
    20: ("btn", BTN_TRIGGER_R),
}
ANDROID_TYPE = 37


def run_generic():
    m = re.search(r"type=(\d+)", bp.ready or "")
    android = bool(m) and int(m.group(1)) == ANDROID_TYPE
    parser = "android" if android else "generic"
    table = ANDROID_BUTTONS if android else GENERIC_BUTTONS
    print(f"      Bluepad32 parser: {parser} (type={m.group(1) if m else '?'})")
    config = cmd("CONFIG?") or ""
    n_buttons = int(re.search(r"buttons=(\d+)", config).group(1)) if "buttons=" in config else 0
    m = re.search(r"axes=(\S*)", config)  # maxbtn has none: "axes= special=..."
    axes = [a for a in m.group(1).split(",") if a] if m else []

    def csv(key):
        m = re.search(rf"{key}=(\S*)", config)
        return [x for x in m.group(1).split(",") if x and x != "none"] if m else []

    n_hats = int(re.search(r"hats=(\d+)", config).group(1)) if "hats=" in config else 0
    specials, sims = csv("special"), csv("sim")
    print(
        f"      gamepad: {n_buttons} buttons, {n_hats} hats, axes {axes}, specials {specials}, sim {sims}"
    )
    section("features")
    check(
        "Bluepad32: no SInput feature response (generic device)",
        bp.features is None,
        f"{bp.features}",
    )

    section("inputs")
    cmd("RESET")
    time.sleep(args.settle)
    got = bp.snapshot()
    check("idle: no buttons", got.get("btn") == 0 and got.get("misc") == 0, f"{got}")
    seen, dropped = 0, 0
    for n in range(1, n_buttons + 1):
        want = table.get(n)
        cmd(f"PRESS {n}")
        time.sleep(args.settle)
        got = bp.snapshot()
        if want:
            field, bit = want
            others = "misc" if field == "btn" else "btn"
            if check(
                f"button {n} -> {field}=0x{bit:x}",
                got.get(field) == bit and got.get(others) == 0,
                f"btn={got.get('btn')} misc={got.get('misc')}",
            ):
                seen += 1
        elif got.get("btn") or got.get("misc"):
            check(
                f"button {n} (dropped by the generic parser) changes nothing",
                False,
                f"btn={got.get('btn')} misc={got.get('misc')}",
            )
        else:
            dropped += 1
        cmd(f"RELEASE {n}")
    if dropped:
        gap(
            f"Bluepad32 {parser} parser: {dropped} of {n_buttons} buttons have no gamepad field",
            f"{seen} mapped ({parser} parser)",
        )

    # Axes, unsigned 0..32767. X -> lx, Y -> ly (-512..511). Z and Rx both feed rx, Ry and Rz both feed ry, so
    # only one of each pair is visible: find which (drive one at full scale, the other at 0), check its scaling,
    # report the hidden one as a gap. Ry goes through Bluepad32's pedal scaling (0..1023), the rest axis scaling.
    # uni_hid_parser_process_axis / _process_pedal, for the 0..32767 logical range (C integer division truncates).
    def c_div(a, b):
        return int(a / b)

    def axis_scale(axis, raw):
        if axis == "ry":  # pedal: (v - min) * 1024 / range
            return c_div(raw * 1024, 32768)
        return c_div((raw - 16384) * 1024, 32768)  # axis: (v - range/2 - min) * 1024 / range

    direct = {"x": "lx", "y": "ly"}
    if android:
        direct.update({"z": "rx", "rz": "ry"})
    for axis, field in direct.items():
        if axis in axes:
            for raw in (32767, 0, 16384):
                want = axis_scale(axis, raw)
                expect(f"axis {axis} {raw} -> {field}={want}", f"AXIS {axis} {raw}", {field: want})
            cmd(f"AXIS {axis} 0")
    if android:
        # Rx and Ry aren't read by the Android parser: driving them must not move anything.
        unread = []
        for axis in ("rx", "ry"):
            if axis not in axes:
                continue
            cmd("RESET")
            time.sleep(args.settle)
            before = bp.snapshot()
            cmd(f"AXIS {axis} 32767")
            time.sleep(args.settle)
            after = bp.snapshot()
            moved = {
                k: (before.get(k), after.get(k))
                for k in ("lx", "ly", "rx", "ry", "brake", "thr")
                if before.get(k) != after.get(k)
            }
            if moved:
                check(
                    f"axis {axis} (not read by the android parser) moves nothing", False, f"{moved}"
                )
            else:
                unread.append(axis)
        if unread:
            gap(f"Bluepad32 {parser} parser: axes not read", ", ".join(unread))
    for a, b, field in () if android else (("z", "rx", "rx"), ("ry", "rz", "ry")):
        present = [x for x in (a, b) if x in axes]
        if not present:
            continue
        winner = present[0]
        if len(present) == 2:
            seen = {}
            for hi in (a, b):
                lo = b if hi == a else a
                cmd(f"AXIS {lo} 0")
                cmd(f"AXIS {hi} 32767")
                time.sleep(args.settle)
                seen[hi] = bp.snapshot().get(field)
            hits = [x for x in (a, b) if seen[x] == axis_scale(x, 32767)]
            if not check(f"exactly one of {a}/{b} drives {field}", len(hits) == 1, f"{seen}"):
                continue
            winner, hidden = hits[0], b if hits[0] == a else a
            gap(
                f"Bluepad32 {parser} parser: {a} and {b} both map to {field}; only {winner} is visible ({hidden} hidden)"
            )
        other = [x for x in (a, b) if x != winner and x in axes]
        for x in other:
            cmd(f"AXIS {x} 0")
        for raw in (32767, 0, 16384):
            want = axis_scale(winner, raw)
            expect(f"axis {winner} {raw} -> {field}={want}", f"AXIS {winner} {raw}", {field: want})
    cmd("RESET")

    if n_hats:
        for h, bits in HATS.items():
            expect(f"hat {h} -> dpad=0x{bits:x}", f"HAT 1 {h}", {"dpad": bits})
        cmd("HAT 1 0")

    # Simulation controls: accelerator -> throttle, brake -> brake (pedal scaling, 0..1023).
    for name, field in (("accelerator", "thr"), ("brake", "brake")):
        if name in sims:
            for raw in (32767, 16384, 0):
                want = c_div(raw * 1024, 32768)
                expect(f"sim {name} {raw} -> {field}={want}", f"SIM {name} {raw}", {field: want})
    unmapped_sims = [x for x in sims if x not in ("accelerator", "brake")]
    if unmapped_sims:
        gap(
            f"Bluepad32 {parser} parser: simulation controls with no gamepad field",
            ", ".join(unmapped_sims),
        )

    # Specials: only Consumer AC Home (-> MISC_BUTTON_START) and AC Back (-> SELECT) are mapped.
    special_index = {
        "start": 0,
        "select": 1,
        "menu": 2,
        "home": 3,
        "back": 4,
        "volinc": 5,
        "voldec": 6,
        "volmute": 7,
    }
    special_want = {"home": MISC_START, "back": MISC_SELECT}
    unmapped_specials = []
    for name in specials:
        i = special_index[name]
        cmd(f"SPECIAL PRESS {i}")
        time.sleep(args.settle)
        got = bp.snapshot()
        if name in special_want:
            check(
                f"special {name} -> misc=0x{special_want[name]:x}",
                got.get("misc") == special_want[name],
                f"misc={got.get('misc')}",
            )
        elif got.get("btn") or got.get("misc"):
            check(
                f"special {name} (unmapped) changes nothing",
                False,
                f"btn={got.get('btn')} misc={got.get('misc')}",
            )
        else:
            unmapped_specials.append(name)
        cmd(f"SPECIAL RELEASE {i}")
    if unmapped_specials:
        gap(
            f"Bluepad32 {parser} parser: special buttons with no gamepad field",
            ", ".join(unmapped_specials),
        )
    cmd("RESET")


if args.profile == "sinput":
    run_sinput()
else:
    run_generic()

cmd("RESET")
print(f"\nGAPS ({len(gaps)}): " + "; ".join(gaps) if gaps else "\nGAPS: none")
print(
    "PASSED" if all(results) else "FAILED", f"({results.count(False)} failures of {len(results)})"
)
sys.exit(0 if all(results) else 1)
