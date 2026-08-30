"""Latency + throughput measurement primitives for the HIL benchmark.

All timing is host-side (time.perf_counter): the firmware and host clocks
aren't synced, so the firmware's `T`/`BURST` microsecond values are recorded
for reference but not used in the latency maths.

Two latencies per input event:
  * ble    = t_evdev - t_serial_reply   (BLE air time + host input stack)
  * e2e    = t_evdev - t_serial_write   (adds USB-serial + firmware parse;
                                         subtract ping_rtt()/2 for a rough
                                         BLE-only figure independent of the
                                         reply path)
"""

import select
import statistics
import time

from evdev import ecodes


def _wait_event(dev, want_types, timeout=1.5):
    """Block until an event of one of want_types arrives; return it or None."""
    deadline = time.perf_counter() + timeout
    while True:
        budget = deadline - time.perf_counter()
        if budget <= 0:
            return None
        r, _, _ = select.select([dev.fd], [], [], budget)
        if not r:
            return None
        for e in dev.read():
            if e.type in want_types:
                return e


class Stats(dict):
    """min/p50/p90/p99/max/mean/n over a sample, JSON-friendly."""

    @classmethod
    def of(cls, samples):
        s = sorted(samples)
        if not s:
            return cls(n=0)

        def pct(p):
            return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]

        return cls(
            n=len(s),
            min=round(s[0], 3),
            p50=round(pct(50), 3),
            p90=round(pct(90), 3),
            p99=round(pct(99), 3),
            max=round(s[-1], 3),
            mean=round(statistics.fmean(s), 3),
        )


def ping_rtt(dev, n=50):
    """Serial PING/PONG round-trip in ms -- the USB-serial + parse baseline."""
    out = []
    for _ in range(n):
        t = time.perf_counter()
        if dev.ping():
            out.append((time.perf_counter() - t) * 1000)
    return Stats.of(out)


# --- per-kind stimulus: (apply(dev, i), event types it should produce) -----
def _button_stim(dev, cfg):
    return (lambda i: dev.tpress(1, down=(i % 2 == 0)), (ecodes.EV_KEY,))


def _axis_stim(dev, cfg):
    lo, hi = cfg["axesMin"], cfg["axesMax"]
    a, b = lo + (hi - lo) // 5, hi - (hi - lo) // 5
    tok = cfg["axes"][0]
    return (lambda i: dev.axis(tok, b if i % 2 else a), (ecodes.EV_ABS,))


def _hat_stim(dev, cfg):
    hi = cfg["hats"]  # firmware emits hat fields reversed; the last index is the one Linux surfaces
    return (lambda i: dev.hat(hi, 1 if i % 2 else 5), (ecodes.EV_ABS,))


_STIM = {"button": _button_stim, "axis": _axis_stim, "hat": _hat_stim}


def input_latency(dev, cap, kind, cfg, n=200, settle=0.03):
    """Measure `n` transitions of `kind` ('button'|'axis'|'hat').
    Returns {ble: Stats, e2e: Stats, dropped: int, n: int}."""
    apply, want = _STIM[kind](dev, cfg)
    ble, e2e, dropped = [], [], 0
    for i in range(n):
        cap.drain()
        t0 = time.perf_counter()
        apply(i)
        t_ok = time.perf_counter()
        ev = _wait_event(cap.dev, want)
        if ev is None:
            dropped += 1
            continue
        t_evt = time.perf_counter()
        ble.append((t_evt - t_ok) * 1000)
        e2e.append((t_evt - t0) * 1000)
        time.sleep(settle)
    return {"n": n, "dropped": dropped, "ble": Stats.of(ble), "e2e": Stats.of(e2e)}


def burst_rate(dev, cap, count=500, gap_us=0, button=1):
    """Fire `count` button toggles `gap_us` apart from the firmware side, count
    the evdev key transitions that reach the host. At small gaps this measures
    NimBLE TX-queue overflow (the ESP32 drops before air), not link capacity --
    see clean_rate() for the meaningful number."""
    cap.drain()
    t0 = time.perf_counter()
    fw_count, fw_us = dev.burst(button, count, gap_us, timeout=max(30.0, count * 0.05))
    evs = cap.collect(settle=0.6, hard_timeout=max(8.0, count * 0.03))
    wall = time.perf_counter() - t0
    got = sum(1 for e in evs if e.type == ecodes.EV_KEY)
    expected = count + 1  # firmware sends one extra release at the end
    return {
        "requested": count,
        "gap_us": gap_us,
        "fw_send_hz": round(count / (fw_us / 1e6), 1) if fw_us else None,
        "host_transitions": got,
        "delivered_frac": round(got / expected, 3) if expected else 0.0,
        "effective_hz": round(got / wall, 1) if wall else 0.0,
    }


def clean_rate(dev, cap, cfg, steps=40):
    """Fastest rate at which *every* distinct state change still reaches the
    host. Walk the inter-report gap down from ~2x the connection interval;
    at each gap send `steps` monotonically-increasing values (one report each)
    and count how many distinct values the host actually saw.

    Returns {curve: [...], clean_hz: float|None} where clean_hz is the highest
    measured rate with >=95% delivery.
    """
    axes = cfg.get("axes") or []
    if not axes:
        # Needs a monotonic continuous signal to count distinct deliveries; a
        # buttons-only profile has none (and Linux's button->keycode mapping
        # past ~15 is too sparse for a gamepad collection to use as a proxy).
        return {"curve": [], "clean_hz": None, "note": "no axis to measure"}

    code = {
        "x": ecodes.ABS_X,
        "y": ecodes.ABS_Y,
        "z": ecodes.ABS_Z,
        "rx": ecodes.ABS_RX,
        "ry": ecodes.ABS_RY,
        "rz": ecodes.ABS_RZ,
        "s1": ecodes.ABS_THROTTLE,
    }.get(axes[0])
    lo, hi = cfg["axesMin"], cfg["axesMax"]
    span = hi - lo
    step = max(1, span // (steps + 4))
    send = lambda i: dev.axis(axes[0], lo + (i + 1) * step)  # noqa: E731
    rest = lambda: dev.axis(axes[0], lo)  # noqa: E731
    count_seen = lambda evs: len(  # noqa: E731
        {e.value for e in evs if e.type == ecodes.EV_ABS and e.code == code}
    )

    curve = []
    best = None
    for gap_ms in (80, 60, 50, 40, 30, 25, 20, 15, 12, 10, 8, 6, 4, 2, 0):
        rest()
        cap.collect(settle=0.25)
        cap.drain()
        ts = []
        for i in range(steps):
            send(i)
            ts.append(time.perf_counter())
            if gap_ms:
                time.sleep(gap_ms / 1000)
        evs = cap.collect(settle=0.5, hard_timeout=6.0)
        seen = count_seen(evs)
        span_s = ts[-1] - ts[0]
        rate = round((steps - 1) / span_s, 1) if span_s > 0 else None
        delivered = round(seen / steps, 3)
        curve.append(
            {
                "gap_ms": gap_ms,
                "rate_hz": rate,
                "delivered_frac": delivered,
                "seen": seen,
                "sent": steps,
            }
        )
        if delivered >= 0.95 and rate and (best is None or rate > best):
            best = rate
        if delivered < 0.6:
            break  # well past the knee
    return {"curve": curve, "clean_hz": best}
