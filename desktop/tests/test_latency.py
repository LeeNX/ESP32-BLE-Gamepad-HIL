"""Press-to-host latency over BLE -- `pytest -m latency` (opt-in; a bare
`pytest` run skips it).

Host-side hidapi read timestamps, not kernel timestamps like the Linux rig, so
a few ms high and jittery -- for regression / same-box comparison, not absolute
air-time. The recorded JSON (`--latency-json`) is the real deliverable; the
asserts are deliberately loose.
"""

import json
import pathlib
import time

import latency as lat
import pytest

pytestmark = pytest.mark.latency

RESULTS = pathlib.Path(__file__).resolve().parents[1] / "results"


def _try(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


@pytest.fixture(scope="module")
def measured(dut, hidgamepad, pytestconfig):
    n = pytestconfig.getoption("latency_n")
    dut.reset()
    rec = {
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "profile": dut.config().get("profile"),
        "name": dut.device_name(),
        "peer": _try(dut.peer_info),
        "sizes": _try(dut.report_sizes),
        "ping_rtt_ms": lat.ping_rtt(dut),
        "button": lat.button_latency(dut, hidgamepad, n=n),
    }
    print("\n" + lat.summary(rec))
    if pytestconfig.getoption("latency_json"):
        RESULTS.mkdir(exist_ok=True)
        out = RESULTS / f"latency-{rec['profile']}-{int(time.time())}.json"
        out.write_text(json.dumps(rec, indent=2) + "\n")
        print(f"[latency] wrote {out}")
    return rec


def test_ping_baseline(measured):
    p50 = measured["ping_rtt_ms"].get("p50")
    assert p50 is not None and p50 < 15, f"serial PING/PONG p50 {p50} ms -- USB-serial issue?"


def test_not_many_dropped(measured):
    b = measured["button"]
    assert b["dropped"] / b["n"] < 0.10, f"{b['dropped']}/{b['n']} presses never reached the host"


def test_button_latency_sane(measured):
    ble = measured["button"]["ble"]
    assert ble.get("n"), "no latency samples -- every press dropped"
    assert ble["p50"] < 150, f"button->host p50 {ble['p50']} ms is well over a BLE HID budget"
