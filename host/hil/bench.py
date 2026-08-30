"""Assemble one benchmark record for the currently-connected profile.

Pure-ish: hand it a live SerialDev + Capture + the firmware CONFIG dict and it
runs the latency/throughput sweep and returns a JSON-friendly dict. The pytest
suite (test_latency.py, under --bench) calls run_sweep() and write_result();
charts.py turns the accumulated results/bench-*.json into tables + SVGs.
"""

import datetime as dt
import json
import pathlib

from . import latency


def run_sweep(serial, cap, cfg, *, board, profile, lib_describe="", quick=False):
    """serial = the SerialDev (hil_runner command channel); cap = the Capture
    wrapping the DUT's evdev node."""
    n = 40 if quick else 200
    gaps = [0, 3000, 10000] if quick else [0, 1000, 2000, 5000, 10000, 20000]

    peer = {}
    try:
        peer = serial.peer_info()
    except Exception as e:  # noqa: BLE001
        peer = {"error": str(e)}
    try:
        sizes = serial.report_sizes()
    except Exception as e:  # noqa: BLE001
        sizes = {"error": str(e)}

    kinds = ["button"]
    if cfg.get("axes"):
        kinds.append("axis")
    if cfg.get("hats"):
        kinds.append("hat")

    return {
        "board": board,
        "profile": profile,
        "lib_describe": lib_describe,
        "measured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "report_bytes": sizes.get("report"),
        "descriptor_bytes": sizes.get("descriptor"),
        "buttons": cfg.get("buttons"),
        "conn_interval_ms": peer.get("interval_ms"),
        "conn_latency": peer.get("latency"),
        "conn_timeout_ms": peer.get("timeout_ms"),
        "mtu": peer.get("mtu"),
        "ping_rtt_ms": latency.ping_rtt(serial, n=30),
        "latency_ms": {k: latency.input_latency(serial, cap, k, cfg, n=n) for k in kinds},
        "burst": [latency.burst_rate(serial, cap, count=300 if quick else 500, gap_us=g)
                  for g in gaps],
    }


def write_result(result, results_dir):
    results_dir = pathlib.Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = results_dir / f"bench-{result['board']}-{result['profile']}-{stamp}.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    return path
