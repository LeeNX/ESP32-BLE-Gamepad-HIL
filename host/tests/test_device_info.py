"""The GATT side: Device Information (0x180A) + PnP ID (0x2A50).

These characteristics stay readable by a generic GATT client even after BlueZ
bridges the HID service into the kernel, so the host reads them straight off the
existing BlueZ connection (D-Bus, via hil.gatt) and checks they match what the
firmware configured (echoed via `DIS?` / `PNP?`).
"""

import pytest

# hil_profile.h HIL_VID / HIL_PID / HIL_GUID_VERSION
HIL_VID = 0x1D34
HIL_PID = 0x8010
HIL_GUID_VERSION = 0x0110


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
