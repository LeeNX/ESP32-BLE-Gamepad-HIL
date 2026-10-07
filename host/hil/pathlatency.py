"""Input latency per delivery path, from one stimulus: the same BLE report timed as it surfaces on several paths.

`measure()` sends one press or release (over hil_runner's serial) and waits on every source at once -- the raw report
on hidraw, the evdev event from whichever HID driver is bound, a Bluepad32 observer's console line. Each sample of each
path then comes from the same report, so the BLE connection-interval jitter that dominates absolute latency cancels
out of the comparison between paths. All timing is host-side (time.perf_counter), from just before the serial write.
"""

import os
import select
import time

from evdev import ecodes

from .latency import Stats


class HidrawBit:
    """A hidraw node: arrived when a report `report_id` has bit `bit` of byte `byte` in the wanted state. Reports that
    don't change it (an SInput gamepad streams its IMU continuously) are skipped."""

    def __init__(self, label, fd, report_id, byte, bit):
        self.label, self.fd, self.report_id, self.byte, self.bit = label, fd, report_id, byte, bit

    def fileno(self):
        return self.fd

    def drain(self):
        while select.select([self.fd], [], [], 0)[0]:
            try:
                os.read(self.fd, 256)
            except BlockingIOError:
                return

    def arrived(self, want):
        while True:
            try:
                r = os.read(self.fd, 256)
            except BlockingIOError:
                return False
            if (
                len(r) > self.byte
                and r[0] == self.report_id
                and bool(r[self.byte] & (1 << self.bit)) == bool(want)
            ):
                return True


class EvdevKey:
    """An evdev node: arrived on an EV_KEY event with the wanted value (1 press, 0 release) -- for key `code` only,
    when given (the measured button's), so another key's event can't stand in for it."""

    def __init__(self, label, dev, code=None):
        self.label, self.dev, self.code = label, dev, code

    def fileno(self):
        return self.dev.fd

    def drain(self):
        while True:
            try:
                if self.dev.read_one() is None:
                    return
            except (BlockingIOError, OSError):
                return

    def arrived(self, want):
        while True:
            try:
                ev = self.dev.read_one()
            except (BlockingIOError, OSError):
                return False
            if ev is None:
                return False
            if ev.type == ecodes.EV_KEY and ev.value == want and self.code in (None, ev.code):
                return True


def measure(stim, sources, n=100, settle=0.03, timeout=1.0):
    """`stim(want)` applies a press (want=1) or release (want=0); alternates, starting with a press. It must only
    send the command, not wait for a reply: a serial bridge can hold the reply back (an FTDI's 16 ms latency timer)
    well past the report's arrival. Timing starts just before `stim` runs, so each latency includes the call itself
    (a serial write: microseconds).
    Returns {label: {"latency": Stats (ms), "dropped": int, "n": int}}."""
    samples = {s.label: [] for s in sources}
    dropped = {s.label: 0 for s in sources}
    for i in range(n):
        want = 1 if i % 2 == 0 else 0
        for s in sources:
            s.drain()
        t0 = time.perf_counter()
        stim(want)
        pending = list(sources)
        deadline = t0 + timeout
        while pending:
            budget = deadline - time.perf_counter()
            if budget <= 0:
                break
            ready, _, _ = select.select(pending, [], [], budget)
            for s in ready:
                if s.arrived(want):
                    samples[s.label].append((time.perf_counter() - t0) * 1000)
                    pending.remove(s)
        for s in pending:
            dropped[s.label] += 1
        time.sleep(settle)
    return {
        label: {"latency": Stats.of(v), "dropped": dropped[label], "n": n}
        for label, v in samples.items()
    }


def describe(results, baseline=None):
    """One line per path, fastest first: p50/p90/p99 in ms, and its p50 relative to `baseline` (a label)."""
    base = results.get(baseline, {}).get("latency", {}).get("p50") if baseline else None
    lines = []
    for label, r in sorted(results.items(), key=lambda kv: kv[1]["latency"].get("p50", 1e9)):
        st = r["latency"]
        if not st.get("n"):
            lines.append(f"{label}: no samples ({r['dropped']} dropped)")
            continue
        rel = (
            f"  ({st['p50'] - base:+.3f} vs {baseline})"
            if base is not None and label != baseline
            else ""
        )
        lines.append(
            f"{label}: p50 {st['p50']:.3f}  p90 {st['p90']:.3f}  p99 {st['p99']:.3f} ms  n={st['n']} "
            f"dropped={r['dropped']}{rel}"
        )
    return lines
