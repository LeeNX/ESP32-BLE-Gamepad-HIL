"""MCU temperature ramp: does sending BLE reports warm the chip? (opt-in: pass --bench)

Three phases on a connected board, each HIL_THERMAL_SECS long (default 60; 20 with --bench-quick), sampled
with STATS? every HIL_THERMAL_SAMPLE_SECS (default 5) into results/mcu-<board>-<profile>.csv (hil/mcustats.py):

    thermal-quiet   connected, no input reports
    thermal-busy    BURST back to back: one report every 5 ms, faster than the connection interval
    thermal-cool    connected and quiet again

The job summary's MCU table shows the quiet and busy means. Trends only: the classic esp32's sensor is
uncalibrated and moves in 0.56 C steps; the C3/S3 read within ~2 C. Nothing here asserts on temperature --
the only gates are that the board kept answering, didn't restart and stayed connected.
"""

import os
import time

import pytest

PHASE_SECS = float(os.environ.get("HIL_THERMAL_SECS", "60"))
SAMPLE_SECS = float(os.environ.get("HIL_THERMAL_SAMPLE_SECS", "5"))
BURST_COUNT = 1000  # x 5 ms gap = ~5 s per burst, so samples land between bursts every ~SAMPLE_SECS
BURST_GAP_US = 5000


def _sample_for(dut, phase, secs, between=None):
    """STATS? every SAMPLE_SECS for `secs`; `between()` runs between samples (the busy phase's burst).
    Returns (rows, last_ok): last_ok is whether the phase's final sample got a reply. McuLog.sample() stays
    best-effort for the log; only this test's verdict depends on the board still answering."""
    rows = []
    end = time.monotonic() + secs
    while True:
        s = dut.mcu.sample(dut, phase)
        last = time.monotonic() >= end
        if not s and last:
            # The C3/S3 serial bridges drop a byte now and then (SerialDev retries once for the same reason):
            # one more try before calling the board silent.
            time.sleep(0.5)
            s = dut.mcu.sample(dut, phase)
        if s:
            rows.append(dict(s, phase=phase))
        if last:
            return rows, bool(s)
        if between:
            between()
        else:
            time.sleep(min(SAMPLE_SECS, max(0.0, end - time.monotonic())))


def test_mcu_thermal_ramp(bench_enabled, connected_dut, gamepad, pytestconfig):
    dut = connected_dut
    secs = PHASE_SECS
    if pytestconfig.getoption("bench_quick") and "HIL_THERMAL_SECS" not in os.environ:
        secs = 20.0
    _evdev, cap = gamepad
    if getattr(dut, "mcu", None) is None or not dut.mcu.sample(dut, "thermal-start"):
        pytest.skip("firmware has no STATS? (built before it existed)")

    def burst():
        dut.burst(1, BURST_COUNT, BURST_GAP_US)
        cap.drain()  # keep the evdev queue from overflowing; the events themselves aren't checked here

    quiet, _ = _sample_for(dut, "thermal-quiet", secs)
    busy, _ = _sample_for(dut, "thermal-busy", secs, between=burst)
    cool, cool_ok = _sample_for(dut, "thermal-cool", secs)

    def temps(rows):
        return [r["temp"] for r in rows if r.get("temp") is not None]

    def mean(xs):
        return sum(xs) / len(xs) if xs else float("nan")

    print(
        f"\n[thermal] quiet {mean(temps(quiet)):.1f} C  busy {mean(temps(busy)):.1f} C"
        f" (max {max(temps(busy), default=float('nan')):.1f})  cool {mean(temps(cool)):.1f} C"
        f"  bt% busy {max((r.get('bt') or 0) for r in busy):.1f}"
    )
    # A board that went silent at the end would leave only its earlier uptimes, which still look monotonic.
    assert cool_ok, "board stopped answering STATS? by the end of the cool-down"
    ups = [r["up"] for r in quiet + busy + cool if r.get("up") is not None]
    assert ups == sorted(ups), f"board restarted during the ramp (uptime went backwards: {ups})"
    dropped = sorted({r["phase"] for r in quiet + busy + cool if r.get("conn") != 1})
    assert not dropped, f"link dropped during: {', '.join(dropped)}"
