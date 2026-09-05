"""HID Feature Report round-trip (bidirectional), on the `reports` profile.

  device -> host : firmware `FEATURE SET <hex>`, host HIDIOCGFEATURE reads it
  host -> device : host HIDIOCSFEATURE, firmware `FEATURE?` shows recv=1 + data

Needs /dev/hidraw* openable -- the udev rule from bootstrap-host.sh
(KERNELS=="0005:1D34:8010.*", GROUP="plugdev"). Skips if it isn't.
"""

import pytest

from hil import hidraw


@pytest.fixture(scope="module")
def feat(connected_dut, bt_mac):
    cfg = connected_dut.config()
    if not cfg.get("feat"):
        pytest.skip("profile has no Feature Report (use --profile reports)")
    node = hidraw.find_node(mac=bt_mac)
    if node is None:
        pytest.skip("no /dev/hidraw node for the DUT")
    try:
        with hidraw.HidRaw(node):
            pass
    except PermissionError:
        pytest.skip(f"{node} not readable -- run bootstrap-host.sh for the udev rule")
    rid = int(cfg["_raw"].split("reportId=")[1].split(" ")[0])
    return node, cfg["feat"], rid


# The last byte of the configured length does not round-trip -- the host reads
# back length-1 data bytes + a trailing 0. Looks like an off-by-one between
# setFeatureReportLength() and the on-wire length. Compare the usable prefix and
# pin the truncation as a strict xfail.
USABLE = -1  # bytes of the configured length that actually round-trip


def test_feature_device_to_host(connected_dut, feat):
    node, length, rid = feat
    payload = bytes((0xA0 + i) & 0xFF for i in range(length))
    connected_dut.set_feature_report(payload)
    with hidraw.HidRaw(node) as h:
        got = h.get_feature(rid, length)
    assert got[: length + USABLE] == payload[: length + USABLE], f"{got.hex()} vs {payload.hex()}"


def test_feature_host_to_device(connected_dut, feat):
    node, length, rid = feat
    payload = bytes((0x11 * (i + 1)) & 0xFF for i in range(length))
    with hidraw.HidRaw(node) as h:
        h.set_feature(rid, payload)
    recv, data = connected_dut.feature_report()
    assert recv, "firmware isFeatureReceived() stayed false after host write"
    assert data[: length + USABLE] == payload[: length + USABLE], (
        f"device saw {data.hex()} vs {payload.hex()}"
    )


def test_feature_roundtrip_both_ways(connected_dut, feat):
    node, length, rid = feat
    n = length + USABLE
    a = bytes([0x5A] * length)
    connected_dut.set_feature_report(a)
    with hidraw.HidRaw(node) as h:
        assert h.get_feature(rid, length)[:n] == a[:n]
        b = bytes([0xC3] * length)
        h.set_feature(rid, b)
    recv, data = connected_dut.feature_report()
    assert recv and data[:n] == b[:n]


@pytest.mark.xfail(
    reason="last byte of setFeatureReportLength() does not "
    "round-trip -- host reads length-1 + trailing 0",
    strict=True,
)
def test_feature_full_length_roundtrips(connected_dut, feat):
    node, length, rid = feat
    payload = bytes((0x30 + i) & 0xFF for i in range(length))
    connected_dut.set_feature_report(payload)
    with hidraw.HidRaw(node) as h:
        assert h.get_feature(rid, length) == payload
