#!/usr/bin/env python3
"""SDL3 HIDAPI vs evdev on the rig: what an SDL 3.4 app sees of the hil_runner `sinput` gamepad down each input path.

Two `sdl3probe --json` run at once (host/hil/sdl3probe.py): one through SDL's own SInput driver on /dev/hidraw
("hidapi"), one with HIDAPI off, reading whatever the kernel driver exposes ("evdev"). Both are run under hid-generic
and then under the LeeNX/linux-hid-sinput driver, so a check has four columns:

  hidapi@hid-generic  evdev@hid-generic  hidapi@sinput  evdev@sinput

Each serial stimulus (buttons, hat, sticks, trigger, IMU, touchpad) is checked on every path, plus the device facts
SDL reports (driver, battery, sensors, touchpad) and SDL's outputs (rumble, RGB LED, player LED) read back from the
board. --latency N also times N presses on all paths at once (CLOCK_MONOTONIC from the serial write).

Report-only: differences print as `GAP <path> <check>`, marked `(known: ...)` when host/hil/sdl3probe.py's KNOWN
list already explains them, and `FIXED?` when a known gap no longer shows. The exit code is 0 whatever the gaps
(2 only when it couldn't run: no pairing); a missing sdl3probe (tester/bootstrap-sdl3.sh) prints SKIP and exits 0.

Run from the repo with PYTHONPATH=host, under tester/rig-lock.sh, with the board flashed with an `sinput` bundle
(tester/test-matrix.sh runs it after sinput_hil.py in the sinput BlueZ lane):

  sdl3_compare.py <serial port> <name contains> [--latency N] [--probe PATH] [--json OUT]
"""

import argparse
import json
import os
import pathlib
import re
import signal
import sys
import time

import serial

from hil import bluetooth as bt
from hil import sdl3probe as sp
from hil import sinput_driver as sd
from hil.latency import Stats

ap = argparse.ArgumentParser()
ap.add_argument("port")
ap.add_argument("name")
ap.add_argument(
    "--probe", help="sdl3probe binary (default: $HIL_SDL3_PROBE or ~/sdl3/current/bin/sdl3probe)"
)
ap.add_argument(
    "--latency",
    type=int,
    default=20,
    metavar="N",
    help="presses+releases to time per driver (0 = skip)",
)
ap.add_argument(
    "--json", metavar="OUT", help="report file (default results/sdl3-compare-<board>.json)"
)
ap.add_argument(
    "--drivers",
    default="hid-generic sinput",
    help="kernel drivers to run under, in order (default: 'hid-generic sinput'; only the bound one without "
    "hil-sinput-driver)",
)
args = ap.parse_args()
board = args.name.split()[-1]

# The matrix stops an overrunning lane with SIGTERM (timeout): exit through the cleanup below, like on Ctrl-C.
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

exe = sp.find_probe(args.probe)
if not exe:
    print(
        "SKIP sdl3probe not installed (tester/bootstrap-sdl3.sh, or --probe / $HIL_SDL3_PROBE)",
        flush=True,
    )
    sys.exit(0)

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


records = []


def report(path, check, ok, detail=""):
    why = sp.known(path, check, None if ok else detail)
    if ok and why:
        print(f"FIXED? {path} {check}: matches now, but KNOWN says: {why}", flush=True)
    elif ok:
        print(f"ok   {path} {check}", flush=True)
    else:
        print(f"GAP  {path} {check}: {detail}" + (f"  (known: {why})" if why else ""), flush=True)
    records.append({"path": path, "check": check, "ok": ok, "known": bool(why), "detail": detail})


def section(title):
    print(f"\n== {title}", flush=True)


def versions(sdl="?"):
    mod = "/sys/module/sinput/version"
    drv = open(mod).read().strip() if os.path.exists(mod) else "not loaded"
    lib = (re.search(r"libsha=(\S+)", cmd("CONFIG?") or "") or [None, "?"])[1]
    return f"kernel={os.uname().release} sinput={drv} sdl={sdl} fw=sinput/{lib} probe={exe}"


# ---------------------------------------------------------------------------------------------------------------
# Before connecting: a fresh pairing autoloads sinput (its modalias), which isn't the state to restore. The pairing
# below runs inside the try, so the cleanup puts the module back however the run ends.
was_loaded = sd.module_loaded()
can_switch = sd.driver_ctl("status")[0]
restore_error = None
report_paths = {}
sdl_version = "?"


def skipped(drv, why):
    """Both of drv's paths couldn't run: a gap each (so the table and the INFO line show it), not a silent hole."""
    print(f"ERROR {why}", flush=True)
    for label in (f"hidapi@{drv}", f"evdev@{drv}"):
        report(label, "driver:bind", False, why)
        report_paths[label] = {"error": why}


try:
    section("connect")
    mac = None
    for m, n in bt.known_devices().items():
        if bt.name_matches(n, args.name) and bt.is_connected(m):
            mac = m
            break
    if not mac:
        with bt.BtCtl() as btctl:
            mac = bt.ensure_paired(btctl, args.name, want_fresh=True)
    if not mac:
        print(f"ERROR no BLE link to {args.name!r}", flush=True)
        sys.exit(2)
    print(f"      {mac}", flush=True)
    end = time.monotonic() + 15
    while sd.bound_driver() is None and time.monotonic() < end:
        time.sleep(0.2)

    drivers = args.drivers.split() if can_switch else [sd.bound_driver() or "none"]
    if not can_switch:
        print(
            f"      no hil-sinput-driver: running under the bound driver only ({drivers[0]})",
            flush=True,
        )

    for drv in drivers:
        section(f"under {drv}")
        if can_switch:
            if drv == "hid-generic" and not sd.module_loaded():
                # The wrapper's unload fails on a module that isn't there.
                ok, out = True, "sinput module not loaded"
            else:
                ok, out = sd.driver_ctl("unload" if drv == "hid-generic" else "load")
            if not (ok and sd.wait_driver(drv)):
                skipped(drv, f"could not bind {drv}: {out} (bound: {sd.bound_driver()})")
                continue
        if not bt.is_connected(mac):
            skipped(drv, "BLE link dropped")
            break
        cmd("RESET")
        cmd("BATTERY 77")
        time.sleep(0.3)

        probes = [
            sp.Probe(exe, f"hidapi@{drv}", hidapi=True),
            sp.Probe(exe, f"evdev@{drv}", hidapi=False),
        ]
        live = []
        for p in probes:
            ready = p.wait_ready()
            report(p.label, "probe:open", ready, p.error or "; ".join(p.other[-3:]) or "no gamepad")
            if ready:
                live.append(p)
                sdl_version = p.facts.get("sdl_version", sdl_version)

        # What SDL says about the device.
        for p in live:
            f = p.facts
            want_drv = "hidapi" if p.hidapi else "evdev"
            report(
                p.label,
                "fact:driver",
                f.get("driver") == want_drv,
                f"{f.get('driver')} via {f.get('path')}",
            )
            report(
                p.label,
                "fact:battery",
                f.get("battery") == 77,
                f"power={f.get('power')} battery={f.get('battery')}",
            )
            report(
                p.label,
                "fact:sensors",
                f.get("sensor.accel") == 1 and f.get("sensor.gyro") == 1,
                f"accel={f.get('sensor.accel')} gyro={f.get('sensor.gyro')}",
            )
            report(
                p.label,
                "fact:touchpad",
                (f.get("touchpads") or 0) >= 1,
                f"touchpads={f.get('touchpads')}",
            )
            rest = {k: f.get(f"state.{k}") for k in ("lefttrigger", "righttrigger")}
            report(p.label, "fact:trigger_rest", all(v == 0 for v in rest.values()), f"{rest}")

        # Stimulus steps, every live path watching the same command.
        for c, expect in sp.STEPS:
            befores = {}
            for p in live:
                if expect[0] == "axis":
                    hist = [
                        e["value"]
                        for e in p.events_between(0, 2**63)
                        if e["ev"] == "axis" and e["name"] == expect[1]
                    ]
                    befores[p.label] = hist[-1] if hist else p.facts.get(f"state.{expect[1]}")
            t0 = time.monotonic_ns()
            cmd(c)
            time.sleep(0.45)
            t1 = time.monotonic_ns()
            for p in live:
                evs = p.events_between(t0, t1)
                seen = sp.observe(evs, expect)
                ok = sp.evaluate(expect, seen, befores.get(p.label))
                report(
                    p.label,
                    f"step:{c}",
                    ok,
                    f"want {expect[1] or expect[0]} {expect[2]}; saw {sp.summarize_events(evs)}",
                )

        # Latency: the same press/release on every live path at once. Not quite like for like: SDL stamps an evdev
        # event with the kernel's time for it, a HIDAPI report with when SDL read it -- in its joystick update, which the
        # probe runs every ~2 ms (as an app would once per frame).
        lat = {}
        if args.latency and live:
            samples = {p.label: [] for p in live}
            dropped = {p.label: 0 for p in live}
            for i in range(args.latency * 2):
                down = i % 2 == 0
                t0 = time.monotonic_ns()
                cmd("PRESS 1" if down else "RELEASE 1")
                hits = sp.wait_for(
                    live,
                    lambda e, d=down: e["ev"] == "button" and e["name"] == "a" and e["down"] is d,
                    t0,
                )
                for label, e in hits.items():
                    if e is None:
                        dropped[label] += 1
                    else:
                        samples[label].append((e["mono_ns"] - t0) / 1e6)
                time.sleep(0.03)
            for label in samples:
                lat[label] = {
                    "latency": Stats.of(samples[label]),
                    "dropped": dropped[label],
                    "n": args.latency * 2,
                }
                st = lat[label]["latency"]
                if st.get("n"):
                    print(
                        f"LATENCY {label}: p50 {st['p50']:.3f}  p90 {st['p90']:.3f}  max {st['max']:.3f} ms  "
                        f"n={st['n']} dropped={dropped[label]}",
                        flush=True,
                    )
                else:
                    print(f"LATENCY {label}: no samples ({dropped[label]} dropped)", flush=True)

        for p in probes:
            p.stop()
            report_paths[p.label] = {
                "facts": p.facts,
                "events": len(p.events),
                "latency": lat.get(p.label),
                "other_output": p.other[-10:],
            }

        # Outputs, one path at a time: the board says what arrived. Asked while the 2 s rumble still runs: when it ends
        # (or the probe closes the gamepad) SDL sends a stop, and RUMBLE? would then read left=0 right=0.
        for p in probes:
            for q in ("RUMBLE?", "RGB?", "PLED?"):  # the ? queries clear the received flags
                cmd(q)
            op = sp.Probe(
                exe,
                p.label,
                hidapi=p.hidapi,
                events=2,
                extra=[
                    "--rumble",
                    "51200",
                    "10240",
                    "2000",
                    "--led",
                    "10",
                    "20",
                    "30",
                    "--player",
                    "2",
                ],
            )
            op.wait_ready()
            time.sleep(0.5)
            rumble, rgb, pled = cmd("RUMBLE?") or "", cmd("RGB?") or "", cmd("PLED?") or ""
            op.stop()
            f = op.facts
            via = f"driver={f.get('driver')}"
            report(
                p.label,
                "out:rumble",
                "recv=1 left=200 right=40" in rumble,
                f"sdl={f.get('rumble')} board={rumble!r} {via}",
            )
            report(
                p.label,
                "out:led",
                "recv=1 r=10 g=20 b=30" in rgb,
                f"sdl={f.get('led')} board={rgb!r} {via}",
            )
            # SDL's player index is 0-based, SInput's player LED 1-based: --player 2 arrives as LED 3.
            report(
                p.label,
                "out:player",
                pled.strip() == "PLED recv=1 3",
                f"sdl={f.get('player')} board={pled!r} {via}",
            )
            report_paths[p.label]["outputs"] = {
                "sdl": f,
                "rumble": rumble,
                "rgb": rgb,
                "pled": pled,
            }
finally:
    # Leave the rig as found even after an error or an interrupt (Ctrl-C, the matrix's timeout). Each step on its own:
    # one failing mustn't skip the others.
    for p in sp.Probe.started:
        try:
            p.stop()
        except Exception as e:  # noqa: BLE001 -- cleanup: report and go on
            print(f"cleanup: stopping {p.label}: {e}", flush=True)
    try:
        # The sinput module loaded or not, as it was at the start -- and the device (if connected) bound to its driver.
        if can_switch and sd.module_loaded() != was_loaded:
            action = "load" if was_loaded else "unload"
            ok, out = sd.driver_ctl(action)
            want = "sinput" if was_loaded else "hid-generic"
            bound = sd.hid_dev() is None or sd.wait_driver(want)
            if not (ok and sd.module_loaded() == was_loaded and bound):
                restore_error = (
                    f"sinput {action} failed: {out or 'no output'}; bound: {sd.bound_driver()}"
                )
    except Exception as e:  # noqa: BLE001
        restore_error = f"restoring the sinput module: {e}"
    if restore_error:
        print(
            f"ERROR cleanup: {restore_error} -- the rig's driver state differs from the start",
            flush=True,
        )
    try:
        cmd("RESET")
    except Exception as e:  # noqa: BLE001
        print(f"cleanup: RESET: {e}", flush=True)

# ---------------------------------------------------------------------------------------------------------------
vers = versions(sdl_version)
print(f"\nVERSIONS {vers}", flush=True)
paths = list(report_paths)
checks = list(dict.fromkeys(r["check"] for r in records))
cell = {(r["path"], r["check"]): r for r in records}
section("matrix (✓ match, ✗ gap, ~ known gap, ! known gap that now matches)")
print("| check | " + " | ".join(paths) + " |")
print("|---|" + "---|" * len(paths))
for c in checks:
    row = []
    for p in paths:
        r = cell.get((p, c))
        row.append(
            ""
            if r is None
            else ("!" if r["known"] else "✓")
            if r["ok"]
            else ("~" if r["known"] else "✗")
        )
    print(f"| {c} | " + " | ".join(row) + " |")

gaps = [r for r in records if not r["ok"]]
new = [r for r in gaps if not r["known"]]
fixed = [r for r in records if r["ok"] and r["known"]]
line = (
    f"SDL3 COMPARE {board}: paths={len(paths)} checks={len(records)} gaps={len(gaps)} "
    f"(known {len(gaps) - len(new)}, new {len(new)}) fixed?={len(fixed)}"
    + (" RIG-RESTORE-FAILED" if restore_error else "")
)
out = pathlib.Path(args.json or f"results/sdl3-compare-{board}.json")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(
    json.dumps(
        {
            "board": board,
            "profile": "sinput",
            "versions": vers,
            "summary": line,
            "restore_error": restore_error,
            "paths": report_paths,
            "checks": records,
        },
        indent=2,
    )
    + "\n"
)
print(f"\n      report -> {out}")
print(line, flush=True)
