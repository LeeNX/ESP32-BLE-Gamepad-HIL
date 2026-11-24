"""SDL3's view of the gamepad, through LeeNX/sdl3-sinput-probe's `sdl3probe --json` (a headless SDL 3.4.x build;
tester/bootstrap-sdl3.sh installs it). Unlike host/hil/sdlreport.py (pysdl2, SDL2: no SInput driver), SDL 3.4 has
two ways to read an SInput gamepad on Linux:

  hidapi   SDL's own SInput driver on /dev/hidraw*: sensors, touchpad, LEDs, rumble straight from the reports
  evdev    whatever the bound kernel driver (hid-generic, or the sinput module) exposes as input devices

`Probe` runs one sdl3probe and collects its JSON lines; two at once (one per path) see the same stimulus, and every
event carries a CLOCK_MONOTONIC time (`mono_ns`), the clock `time.monotonic_ns()` reads, so a script can match it
to the serial write that caused it. `observe()` / `evaluate()` turn one step's events into a verdict.
"""

import fnmatch
import json
import os
import pathlib
import signal
import subprocess
import threading
import time

# Where tester/bootstrap-sdl3.sh installs it: $HIL_SDL3_ROOT (default ~/sdl3), then current/bin/sdl3probe.
DEFAULT_PROBE = (
    pathlib.Path(os.environ.get("HIL_SDL3_ROOT") or pathlib.Path.home() / "sdl3")
    / "current"
    / "bin"
    / "sdl3probe"
)


def find_probe(explicit=None):
    """The sdl3probe to run: --probe, $HIL_SDL3_PROBE, or ~/sdl3/current/bin/sdl3probe; None if not there."""
    for c in (explicit, os.environ.get("HIL_SDL3_PROBE"), DEFAULT_PROBE):
        if c and os.access(c, os.X_OK):
            return str(c)
    return None


class Probe:
    """One `sdl3probe --json` process. `facts` fills in as lines arrive; `events` is every {"ev": ...} line."""

    started = []  # every Probe ever created, for a cleanup that must stop them all (stop() on a finished one is a no-op)

    def __init__(self, exe, label, hidapi=True, wait=10, events=600, extra=()):
        self.label = label
        self.hidapi = hidapi
        self.facts = {}
        self.events = []
        self.other = []  # non-JSON output (SDL log lines, a crash)
        self.listening = threading.Event()
        self.done = threading.Event()
        argv = [exe, "--json", "--wait", str(wait), "--events", str(events), *extra]
        if not hidapi:
            argv.append("--no-hidapi")
        self.argv = argv
        self.proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        Probe.started.append(
            self
        )  # straight after the process exists: a cleanup can always find it
        self._lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            try:
                obj = json.loads(line)
            except ValueError:
                self.other.append(line.rstrip())
                continue
            with self._lock:
                if "ev" in obj:
                    self.events.append(obj)
                elif "fact" in obj:
                    self.facts[obj["fact"]] = obj.get("value")
                    if obj["fact"] in ("listening", "error"):
                        self.listening.set()
        self.listening.set()
        self.done.set()

    @property
    def error(self):
        return self.facts.get("error")

    def wait_ready(self, timeout=20):
        """True once the probe has the gamepad open and streams events."""
        self.listening.wait(timeout)
        return bool(self.facts.get("listening"))

    def events_between(self, t0, t1):
        with self._lock:
            return [e for e in self.events if t0 <= e["mono_ns"] < t1]

    def stop(self, timeout=5):
        """SIGTERM ends the stream cleanly (the probe still prints final.*); then the process's exit code."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.done.wait(timeout)
        return self.proc.returncode


# ---------------------------------------------------------------------------------------------------------------
# Stimulus steps: a hil_runner serial command and what SDL should report for it. SDL names (a, back, dpup, leftx,
# lefttrigger, ...) are SDL's gamepad mapping of the SInput report, as SDL's hidapi driver gives them.
#   ("button", name, down)   a button event with that state
#   ("axis", name, value)    the axis's last value in the window, within AXIS_TOL
#   ("sensor", None, moved)  accel+gyro readings: any non-zero (True) / all zero (False)
#   ("touch", action, None)  a touchpad down/up event
STEPS = [
    ("PRESS 1", ("button", "a", True)),
    ("RELEASE 1", ("button", "a", False)),
    ("PRESS 2", ("button", "b", True)),
    ("RELEASE 2", ("button", "b", False)),
    ("SPECIAL PRESS 1", ("button", "back", True)),
    ("SPECIAL RELEASE 1", ("button", "back", False)),
    ("HAT 1 1", ("button", "dpup", True)),
    ("HAT 1 0", ("button", "dpup", False)),
    ("AXIS x 16000", ("axis", "leftx", 16000)),
    ("AXIS x 0", ("axis", "leftx", 0)),
    ("AXIS z 12345", ("axis", "rightx", 12345)),
    ("AXIS z 0", ("axis", "rightx", 0)),
    ("AXIS rx 32767", ("axis", "lefttrigger", 32767)),
    ("AXIS rx 0", ("axis", "lefttrigger", 0)),
    ("MOTION 100 -200 300 -4096 4096 8192", ("sensor", None, True)),
    ("MOTION 0 0 0 0 0 0", ("sensor", None, False)),
    ("TOUCH 0 1000 -1000 500", ("touch", "down", None)),
    ("TOUCH 0 0 0 0", ("touch", "up", None)),
]
AXIS_TOL = 256  # evdev rescales the descriptor's range into SDL's; allow a little rounding

# What a path is expected to lack, so a report shows new differences apart from ones already understood -- and a
# known gap that stops showing up is reported as possibly fixed. (path glob, check glob, detail substring or None):
# the substring pins an entry to the symptom it explains, so the same check failing another way still counts as new.
KNOWN = [
    (
        "*",
        "step:AXIS rx 0",
        "lefttrigger=16383",
        "released trigger reads 16383 (half): SInput's released trigger is -32768, the library sends 0",
    ),
    (
        "*",
        "fact:battery",
        "power=unknown battery=-1",
        "library reports plug status UNKNOWN unless charging state is set, and SDL then drops the level",
    ),
    (
        "evdev@*",
        "fact:sensors",
        "accel=0 gyro=0",
        "SDL's evdev backend doesn't use the separate IMU input device",
    ),
    (
        "evdev@*",
        "fact:touchpad",
        "touchpads=0",
        "SDL's evdev backend doesn't use the separate touchpad input device",
    ),
    ("evdev@*", "step:MOTION*", "saw no events", "no sensors on the evdev path"),
    ("evdev@*", "step:TOUCH*", "saw no events", "no touchpad on the evdev path"),
    ("evdev@*", "out:led", "sdl=0 ", "SDL's evdev backend has no LED path"),
    ("evdev@*", "out:player", "recv=0", "SDL's evdev backend has no player-LED path"),
    (
        "evdev@hid-generic",
        "out:rumble",
        "sdl=0 ",
        "hid-generic: no force-feedback usage in the SInput descriptor",
    ),
]


def known(path, check, detail=None):
    """The KNOWN reason for this path/check (and, if the entry pins one, this symptom), or None. With detail=None
    (the check passed) only the path and check are matched, to flag a known gap that may be fixed."""
    for pglob, cglob, symptom, why in KNOWN:
        if not (fnmatch.fnmatchcase(path, pglob) and fnmatch.fnmatchcase(check, cglob)):
            continue
        if detail is None or symptom is None or symptom in detail:
            return why
    return None


def observe(events, expect):
    """What `events` (one step's window) show for `expect`, as a short JSON-friendly value."""
    kind, name, _ = expect
    if kind == "button":
        states = [e["down"] for e in events if e["ev"] == "button" and e["name"] == name]
        return states[-1] if states else None
    if kind == "axis":
        vals = [e["value"] for e in events if e["ev"] == "axis" and e["name"] == name]
        return vals[-1] if vals else None
    if kind == "sensor":
        last = {}
        for e in events:
            if e["ev"] == "sensor":
                last[e["name"]] = e["data"]
        return last or None
    if kind == "touch":
        acts = [e["action"] for e in events if e["ev"] == "touch"]
        return acts or None
    return None


def evaluate(expect, seen, before=None):
    """True when `seen` (from observe) matches `expect`. `before` is the axis value before the step: an axis that
    doesn't move sends no event, so no event still matches when it already sat at the target."""
    kind, name, want = expect
    if kind == "button":
        return seen is want
    if kind == "axis":
        v = seen if seen is not None else before
        return v is not None and abs(v - want) <= AXIS_TOL
    if kind == "sensor":
        # Both sensors, either way: a reset seen on one of them doesn't show the other one went back to zero.
        if seen is None or not {"accel", "gyro"} <= set(seen):
            return False
        moved = any(abs(x) > 1e-3 for data in seen.values() for x in data)
        return moved == want
    if kind == "touch":
        return bool(seen) and name in seen
    return False


def summarize_events(events):
    """A one-line digest of a window's events, for a GAP line."""
    out = []
    for e in events:
        if e["ev"] == "button":
            out.append(f"{e['name']}{'+' if e['down'] else '-'}")
        elif e["ev"] == "axis":
            out.append(f"{e['name']}={e['value']}")
        elif e["ev"] == "touch":
            out.append(f"touch:{e['action']}")
        elif e["ev"] == "sensor":
            out.append(f"{e['name']}={[round(x, 3) for x in e['data']]}")
        else:
            out.append(e["ev"])
    return " ".join(out) or "no events"


def wait_for(probes, pred, t0, timeout=1.0):
    """Per probe label: the first event at/after t0 matching pred, or None after timeout."""
    found = {p.label: None for p in probes}
    end = time.monotonic() + timeout
    while time.monotonic() < end and any(v is None for v in found.values()):
        for p in probes:
            if found[p.label] is None:
                hits = [e for e in p.events_between(t0, 2**63) if pred(e)]
                if hits:
                    found[p.label] = hits[0]
        time.sleep(0.003)
    return found
