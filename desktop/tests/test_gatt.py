"""The GATT side: Device Information (0x180A) + PnP ID (0x2A50) + Battery
(0x180F), read over GATT via `bleak` -- independent of the HID stack.

Ported from ../../host/tests/test_device_info.py + the battery-level part of
test_battery.py -- same characteristics, `gatt.py` (bleak) standing in for the
rig's BlueZ/D-Bus reads. Confirmed on macOS hardware: these stay readable even
though the DUT is bonded here and its HID service is owned by the system HID
stack -- but the HID service itself is not reachable this way (see gatt.py's
docstring, and test_feature_report.py for the characteristic that lives
there).
"""

import time

import pytest

# hil_profile.h HIL_VID / HIL_PID / HIL_GUID_VERSION
HIL_VID = 0x1D34
HIL_PID = 0x8010
HIL_GUID_VERSION = 0x0110

pytestmark = pytest.mark.gatt


def test_dis_matches_firmware(dut, device_info):
    fw = dut.device_info()  # DIS? -- what the firmware set
    host = device_info["device_info"]  # what the GATT read returned
    for key in ("model", "serial", "fw", "hw", "sw", "mfr"):
        assert host.get(key), f"DIS {key} came back empty over GATT"
        assert host[key] == fw[key], f"DIS {key}: GATT {host[key]!r} != firmware {fw[key]!r}"


def test_dis_sentinels(device_info):
    di = device_info["device_info"]
    assert di["model"] == "HIL-MODEL"
    assert di["serial"] == "HIL-SN-0001"
    assert di["fw"].startswith("HIL-FW-")
    assert di["mfr"] == "LeeNX-HIL"


def test_pnp_id(device_info):
    pnp = device_info["pnp"]
    if not pnp or "vendor_id" not in pnp:
        pytest.skip("PnP ID characteristic not exposed")
    assert pnp["vendor_id"] == HIL_VID
    assert pnp["product_id"] == HIL_PID
    assert pnp["product_version"] == HIL_GUID_VERSION
    assert pnp["vendor_id_source"] == 1  # Bluetooth SIG-assigned


def test_pnp_matches_firmware(dut, device_info):
    pnp = device_info["pnp"]
    if not pnp or "vendor_id" not in pnp:
        pytest.skip("PnP ID characteristic not exposed")
    fw = dut.pnp()
    assert pnp["vendor_id"] == int(fw["vid"], 16)
    assert pnp["product_id"] == int(fw["pid"], 16)
    assert pnp["product_version"] == int(fw["ver"], 16)


@pytest.mark.parametrize("level", [0, 1, 42, 99, 100])
def test_battery_level_roundtrips(dut, gatt, device_name, level):
    dut.battery(level)
    time.sleep(1.0)  # let the notification/value propagate
    assert gatt.read_battery_level(device_name) == level


def test_hid_service_not_reachable_via_gatt(gatt, device_name):
    """Documents the platform limit gatt.py's docstring describes: macOS hands
    the HID service (0x1812) to its own Bluetooth HID stack once a HID-class
    peripheral connects, so a generic GATT client (bleak, here) never sees it
    -- only the non-HID services (Device Information, Battery) are reachable
    this way. Starts failing (usefully) if a future macOS ever changes this."""
    import asyncio

    from bleak import BleakClient

    async def _service_uuids():
        device = await gatt._find(device_name)
        async with BleakClient(device) as client:
            return {s.uuid.lower() for s in client.services}

    uuids = asyncio.run(_service_uuids())
    hid_service_uuid = "00001812-0000-1000-8000-00805f9b34fb"
    assert hid_service_uuid not in uuids, (
        "HID service is now visible to a generic GATT client -- "
        "test_feature_report.py's hidapi route may no longer be necessary"
    )
