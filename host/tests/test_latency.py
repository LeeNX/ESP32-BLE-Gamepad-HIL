"""Latency / polling-rate benchmark (opt-in: pass --bench).

Runs one full sweep for the flashed profile, writes
results/bench-<board>-<profile>-<stamp>.json (charts.py turns the accumulated
files into tables + SVGs), and asserts a few deliberately loose gates so a
genuine regression trips CI without normal host-scheduling jitter doing so.
"""

import pathlib

import pytest

from hil import bench

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def sweep(bench_enabled, connected_dut, gamepad, rigcfg, pytestconfig):
    _evdev, cap = gamepad
    cfg = connected_dut.config()
    result = bench.run_sweep(
        connected_dut,
        cap,
        cfg,
        board=rigcfg["name"],
        profile=rigcfg["profile"],
        lib_describe=_lib_describe(pytestconfig),
    )
    path = bench.write_result(result, REPO / "results")
    print(f"\n[bench] wrote {path}")
    return result


def _lib_describe(pytestconfig):
    bundle = pytestconfig.getoption("bundle")
    if bundle:
        import json

        try:
            return json.loads((pathlib.Path(bundle) / "manifest.json").read_text())["lib_describe"]
        except Exception:
            pass
    return ""


def test_button_latency_reasonable(sweep):
    st = sweep["latency_ms"]["button"]["e2e"]
    assert st["n"] >= 1
    assert st["p50"] < 60, f"button p50 latency {st['p50']} ms is implausibly high"
    assert sweep["latency_ms"]["button"]["dropped"] < st["n"] * 0.25


def test_conn_interval_recorded(sweep):
    ci = sweep["conn_interval_ms"]
    # ~48.75 ms observed on the Pi -- the library doesn't request a fast
    # interval, which is the throughput ceiling. Just sanity-bound it.
    assert ci is None or 5 <= ci <= 100, f"odd connection interval {ci} ms"


def test_no_dropped_input(sweep):
    """The real accuracy gate: every deliberate, paced input event reaches the
    host. (The burst curve below is informational -- see test_burst_curve.)"""
    for kind, d in sweep["latency_ms"].items():
        assert d["dropped"] == 0, f"{kind}: {d['dropped']}/{d['n']} paced events lost"


def test_clean_rate(sweep):
    """Fastest rate at which every distinct paced state change still reaches the
    host (>=95% of values seen). NimBLE sends several packets per connection
    event, so paced traffic keeps up well past 1/connection-interval -- here
    it's the rig's own serial command channel that's the limit, not BLE. (The
    unpaced-burst ceiling is much lower -- see the `burst` curve.)"""
    if not sweep["clean_rate_curve"]:
        pytest.skip("no axis in this profile to measure a clean rate")
    clean = sweep["clean_rate_hz"]
    assert clean is not None, "clean_rate never hit 95% delivery -- check the curve"
    assert 5 <= clean <= 1000, f"clean rate {clean} Hz out of any plausible range"
    # the slowest curve point should be a clean 100% -- if even ~10 Hz drops,
    # something is wrong with delivery, not just rate.
    slow = min(sweep["clean_rate_curve"], key=lambda c: c["rate_hz"] or 1e9)
    assert slow["delivered_frac"] >= 0.95, (
        f"only {slow['delivered_frac']:.0%} delivered at {slow['rate_hz']} Hz"
    )


def test_env_recorded(sweep):
    e = sweep["env"]
    assert e["kernel"] and e["arch"] and e["bluez"]
    assert sweep["load"]["start"]["loadavg"] is not None


def test_report_size_recorded(sweep):
    assert sweep["report_bytes"] and sweep["descriptor_bytes"]
    assert sweep["descriptor_bytes"] <= 150
