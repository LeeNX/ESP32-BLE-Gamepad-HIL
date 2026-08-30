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
    result = bench.run_sweep(connected_dut, cap, cfg, board=rigcfg["name"],
                             profile=rigcfg["profile"],
                             lib_describe=_lib_describe(pytestconfig))
    path = bench.write_result(result, REPO / "results")
    print(f"\n[bench] wrote {path}")
    return result


def _lib_describe(pytestconfig):
    bundle = pytestconfig.getoption("bundle")
    if bundle:
        import json
        try:
            return json.loads(
                (pathlib.Path(bundle) / "manifest.json").read_text())["lib_describe"]
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


def test_burst_curve_recorded(sweep):
    """Firing sendReport() faster than ~1/connection-interval overflows NimBLE's
    TX queue and the ESP32 drops silently -- so a gap=0 burst delivers almost
    nothing. Just assert the curve was measured; the numbers live in the JSON
    and bench-table.md. A proper 'fastest clean rate' metric is TODO."""
    assert sweep["burst"], "no burst data"
    assert any(b["gap_us"] == 0 for b in sweep["burst"])
    ci = sweep["conn_interval_ms"]
    assert ci is None or 5 <= ci <= 100


def test_report_size_recorded(sweep):
    assert sweep["report_bytes"] and sweep["descriptor_bytes"]
    assert sweep["descriptor_bytes"] <= 150
