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
    dev, cap = gamepad
    cfg = connected_dut.config()
    result = bench.run_sweep(dev, cap, cfg, board=rigcfg["name"],
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
    assert ci is None or 5 <= ci <= 50, f"odd connection interval {ci} ms"


def test_sustained_rate_floor(sweep):
    b0 = next(b for b in sweep["burst"] if b["gap_us"] == 0)
    assert b0["effective_hz"] >= 20, f"only {b0['effective_hz']} Hz sustained at gap=0"
    assert b0["delivered_frac"] >= 0.5, f"dropped {1 - b0['delivered_frac']:.0%} of a gap=0 burst"


def test_report_size_recorded(sweep):
    assert sweep["report_bytes"] and sweep["descriptor_bytes"]
    assert sweep["descriptor_bytes"] <= 150
