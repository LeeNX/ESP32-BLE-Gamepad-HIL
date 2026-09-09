"""Press-to-host latency for the desktop tester.

`TPRESS <n>` on the serial channel -> the firmware toggles the button and calls
`sendReport()`, replying `T <micros>` -- then we block in a hidapi read until
the DUT's HID report reflects the new state, and time the gap.

Two figures per transition (host clock, `time.perf_counter`):

    ble  =  t_report - t_serial_reply    BLE air + macOS HID stack + hidapi read
    e2e  =  t_report - t_serial_write     + the USB-serial round-trip

The firmware's `T <micros>` is on a different clock, so it's recorded for
reference only, not used in the maths.

**Caveat vs the Linux rig:** the rig times off the *kernel* evdev event
timestamp; here `t_report` is a userspace hidapi read, so it carries hidapi's
queue latency and our loop overhead -- numbers run a few ms high and jittier.
Good for regression / same-box comparison, not for absolute air-time.
"""

import statistics
import time

from hidgamepad import BUTTON_BYTE, button_bytes


def stats(samples):
    """min / p50 / p90 / p99 / max / mean over a sample, in ms, JSON-friendly."""
    s = sorted(samples)
    if not s:
        return {"n": 0}

    def pct(p):
        return s[min(len(s) - 1, round(p / 100 * (len(s) - 1)))]

    return {
        "n": len(s),
        "min": round(s[0], 2),
        "p50": round(pct(50), 2),
        "p90": round(pct(90), 2),
        "p99": round(pct(99), 2),
        "max": round(s[-1], 2),
        "mean": round(statistics.fmean(s), 2),
    }


def ping_rtt(dut, n=50):
    """Serial PING/PONG round-trip in ms -- the USB-serial + parse baseline."""
    out = []
    for _ in range(n):
        t = time.perf_counter()
        if dut.ping():
            out.append((time.perf_counter() - t) * 1000)
    return stats(out)


def _button_down(report, button):
    if len(report) <= BUTTON_BYTE:
        return False
    field = int.from_bytes(report[BUTTON_BYTE : BUTTON_BYTE + button_bytes(button)], "little")
    return bool(field & (1 << (button - 1)))


def button_latency(dut, dev, n=100, button=1, settle=0.03, timeout_ms=400):
    """`n` press/release transitions of `button`; for each, wait (blocking
    hidapi read) for the first report that reflects the new state.

    Returns {n, dropped, ble: stats, e2e: stats}.
    """
    dev.set_nonblocking(True)
    while dev.read(64):  # clear any queued reports
        pass
    dev.set_nonblocking(False)

    ble, e2e, dropped = [], [], 0
    for i in range(n):
        want_down = i % 2 == 0
        t0 = time.perf_counter()
        dut.tpress(button, down=want_down)  # firmware replies "T <micros>"
        t_reply = time.perf_counter()

        deadline = t_reply + timeout_ms / 1000
        while True:
            budget_ms = int((deadline - time.perf_counter()) * 1000)
            if budget_ms <= 0:
                dropped += 1
                break
            r = dev.read(64, budget_ms)
            if r and _button_down(bytes(r), button) == want_down:
                t_report = time.perf_counter()
                ble.append((t_report - t_reply) * 1000)
                e2e.append((t_report - t0) * 1000)
                break
        time.sleep(settle)

    return {"n": n, "dropped": dropped, "ble": stats(ble), "e2e": stats(e2e)}


def summary(rec):
    """One-block text render of a record dict from the test."""
    b = rec["button"]
    lines = [
        f"latency  profile={rec.get('profile')}  name={rec.get('name')!r}  "
        f"n={b['n']}  dropped={b['dropped']}",
    ]
    peer = rec.get("peer") or {}
    if "interval_ms" in peer:
        lines.append(
            f"  link: interval {peer['interval_ms']:.2f} ms  MTU {peer.get('mtu')}  "
            f"report {rec.get('sizes', {}).get('report', '?')} B"
        )
    lines.append(f"  ping RTT (serial) : p50 {rec['ping_rtt_ms'].get('p50')} ms")
    for k in ("ble", "e2e"):
        s = b[k]
        if s.get("n"):
            lines.append(
                f"  {k:<4} p50 {s['p50']:6.1f}  p90 {s['p90']:6.1f}  "
                f"p99 {s['p99']:6.1f}  max {s['max']:6.1f}  (ms)"
            )
    return "\n".join(lines)
