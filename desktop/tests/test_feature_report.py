"""HID Feature Report round-trip (bidirectional), on the `specials` profile.

  device -> host : firmware `FEATURE SET <hex>`, host reads it via hidapi
  host -> device : host writes via hidapi, firmware `FEATURE?` shows recv=1 + data

Ported from ../../host/tests/test_feature_report.py -- same protocol
(SerialDev.feature_report()/set_feature_report(), shared via ../../host), the
`hidapi` device (hid.device().get_feature_report()/send_feature_report()) --
already a dependency for -m hid -- standing in for the rig's Linux-only
`hidraw` ioctls. Needs the board bonded to this host (pair-assist.py).
"""

import pytest

from hil.hidraw import HIL_PID, HIL_VID

pytestmark = pytest.mark.hid


@pytest.fixture(scope="module")
def feat(dut):
    cfg = dut.config()
    if not cfg.get("feat"):
        pytest.skip("profile has no Feature Report (use --profile specials)")
    try:
        dut.wait_connected(timeout=25.0)  # opening the serial port reset the ESP32; wait for the BLE relink
    except Exception:
        pytest.skip("board not bonded/connected -- run  python pair-assist.py --port <port>  first")
    rid = int(cfg["_raw"].split("reportId=")[1].split(" ")[0])

    hid = pytest.importorskip("hid", reason="pip install hidapi")
    h = hid.device()
    try:
        h.open(HIL_VID, HIL_PID)
    except OSError as e:
        pytest.skip(f"can't open the DUT HID device ({e}) -- bonded?")
    yield h, cfg["feat"], rid
    h.close()


# The rig's Linux hidraw path has a documented strict xfail here: the last
# byte of the configured length doesn't round-trip over HIDIOCGFEATURE (host
# reads length-1 data bytes + a trailing 0). Checked deliberately on this
# platform (test_feature_full_length_roundtrips below, full length, no
# truncation) -- it does NOT reproduce over macOS's native HID stack. So
# that's a Linux/hidraw (or BlueZ) transport quirk, not a firmware bug -- the
# onread-clobber bug these three tests exist to catch was the real,
# platform-independent one. No truncation compensation needed here.
def test_feature_device_to_host(dut, feat):
    h, length, rid = feat
    payload = bytes((0xA0 + i) & 0xFF for i in range(length))
    dut.set_feature_report(payload)
    got = bytes(h.get_feature_report(rid, length))
    assert got == payload, f"{got.hex()} vs {payload.hex()}"


def test_feature_host_to_device(dut, feat):
    h, length, rid = feat
    payload = bytes((0x11 * (i + 1)) & 0xFF for i in range(length))
    h.send_feature_report(bytes([rid]) + payload)
    recv, data = dut.feature_report()
    assert recv, "firmware isFeatureReceived() stayed false after host write"
    assert data == payload, f"device saw {data.hex()} vs {payload.hex()}"


def test_feature_roundtrip_both_ways(dut, feat):
    h, length, rid = feat
    a = bytes([0x5A] * length)
    dut.set_feature_report(a)
    assert bytes(h.get_feature_report(rid, length)) == a
    b = bytes([0xC3] * length)
    h.send_feature_report(bytes([rid]) + b)
    recv, data = dut.feature_report()
    assert recv and data == b


def test_feature_full_length_roundtrips(dut, feat):
    """The rig pins this as a strict xfail (Linux hidraw truncates the last
    byte). On macOS's native HID stack it round-trips in full -- confirmed on
    real hardware, not carried over as an assumption from the rig."""
    h, length, rid = feat
    payload = bytes((0x30 + i) & 0xFF for i in range(length))
    dut.set_feature_report(payload)
    assert bytes(h.get_feature_report(rid, length)) == payload
