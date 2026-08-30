"""HID Output Report (host -> device only), on the `reports` profile.

Host writes a report to /dev/hidraw* (first byte = Report ID), firmware
`OUTPUT?` reports isOutputReceived() + getOutputBuffer(). Skips if the hidraw
node isn't openable (udev rule -- see test_feature_report).
"""

import time

import pytest

from hil import hidraw


@pytest.fixture(scope="module")
def out(connected_dut, bt_mac):
    cfg = connected_dut.config()
    if not cfg.get("out"):
        pytest.skip("profile has no Output Report (use --profile reports)")
    node = hidraw.find_node()
    if node is None:
        pytest.skip("no /dev/hidraw node for the DUT")
    try:
        with hidraw.HidRaw(node):
            pass
    except PermissionError:
        pytest.skip(f"{node} not readable -- run bootstrap-host.sh for the udev rule")
    rid = int(cfg["_raw"].split("reportId=")[1].split(" ")[0])
    return node, cfg["out"], rid


def test_output_received(connected_dut, out):
    node, length, rid = out
    payload = bytes((0x10 + i) & 0xFF for i in range(length))
    with hidraw.HidRaw(node) as h:
        h.write_output(rid, payload)
    time.sleep(0.3)
    recv, data = connected_dut.output_report()
    assert recv, "firmware isOutputReceived() stayed false after host write"
    assert data[:length] == payload, f"device saw {data.hex()} != {payload.hex()}"


def test_output_updates_on_each_write(connected_dut, out):
    node, length, rid = out
    for val in (0x01, 0x7F, 0xFF):
        payload = bytes([val] * length)
        with hidraw.HidRaw(node) as h:
            h.write_output(rid, payload)
        time.sleep(0.3)
        _, data = connected_dut.output_report()
        assert data[:length] == payload
