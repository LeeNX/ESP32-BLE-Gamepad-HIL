"""Battery Service: level (0x2A19) and Battery Power State (0x2A1A).

Level is what `setBatteryLevel()` rides on -- BlueZ bridges it to Battery1 /
UPower. Power state is the charging/discharging bitfield from
`setPowerStateAll()`.
"""

import time

import pytest

LEVELS = [0, 1, 42, 99, 100]


@pytest.mark.parametrize("level", LEVELS)
def test_battery_level_roundtrips(connected_dut, gatt, bt_mac, level):
    connected_dut.battery(level)
    time.sleep(1.0)  # let the notification propagate to BlueZ
    assert gatt.read_battery_level(bt_mac) == level  # raw 0x2A19 read
    assert gatt.read_battery_bluez(bt_mac) == level  # BlueZ Battery1 ingested it


def test_battery_level_userland(connected_dut, gatt, bt_mac):
    """Battery reaches Linux userland. This library's battery is the BLE Battery
    Service (not a HID battery usage), so it surfaces via BlueZ's Battery1
    D-Bus interface (-> UPower where installed) and NOT in
    /sys/class/power_supply/hid-*."""
    import pathlib

    connected_dut.battery(63)
    time.sleep(1.5)
    assert gatt.read_battery_bluez(bt_mac) == 63

    up = gatt.read_battery_upower(bt_mac)
    if up is not None:  # UPower present -> must agree with BlueZ
        assert up == 63
    else:
        print("UPower not installed -- Battery1 D-Bus check stands alone")

    hid_ps = [
        p
        for p in pathlib.Path("/sys/class/power_supply").glob("*")
        if bt_mac.replace(":", "-").lower() in p.name.lower()
    ]
    assert not hid_ps, f"unexpected /sys/class/power_supply entry: {hid_ps}"


def test_battery_level_out_of_range_rejected(connected_dut):
    with pytest.raises(Exception):
        connected_dut.battery(101)


# --- Battery Power State (0x2A1A) --------------------------------------
# firmware POWERSTATE <info> <discharging> <charging> <level>, 2-bit fields:
#   info: 3=present   discharging: 2=not-discharging / 3=discharging
#   charging: 2=not-charging / 3=charging   level: 2=good / 3=critically-low
CHARGING = (3, 2, 3, 2)
DISCHARGING = (3, 3, 2, 2)


def _power_state(gatt, bt_mac, rigcfg):
    """Read 0x2A1A. Its absence fails by default -- the library dropped the characteristic once
    (13fb3c6, 2026-08-31, fixed in d21f096) and a skip hid that for two months. A tester for a
    library without it sets [rig] require_power_state = false to skip instead.

    None also comes back when GATT can't be read at all (BlueZ/D-Bus trouble, a dropped link), so
    Battery Level (0x2A19, same service, every library version) tells the two apart: unreadable
    too means it's not about 0x2A1A, and that fails whatever require_power_state says."""
    ps = gatt.read_power_state(bt_mac)
    if ps is not None:
        return ps
    assert gatt.read_battery_level(bt_mac) is not None, (
        "GATT unreadable: Battery Level (0x2A19) is missing as well as 0x2A1A -- a BlueZ/D-Bus or"
        " link problem, not the power-state characteristic"
    )
    if not rigcfg["rig"].get("require_power_state", True):
        pytest.skip(
            "0x2A1A Battery Power State characteristic not exposed (require_power_state = false)"
        )
    pytest.fail(
        "0x2A1A Battery Power State characteristic not exposed -- setPowerStateAll() is a no-op"
        " (set [rig] require_power_state = false to skip)"
    )


def test_power_state_charging(connected_dut, gatt, bt_mac, rigcfg):
    connected_dut.power_state(*CHARGING)
    time.sleep(1.0)
    ps = _power_state(gatt, bt_mac, rigcfg)
    assert ps["info"] == 3 and ps["charging"] == 3 and ps["discharging"] == 2
    assert ps["charging_meaning"] == "charging"


def test_power_state_discharging(connected_dut, gatt, bt_mac, rigcfg):
    connected_dut.power_state(*DISCHARGING)
    time.sleep(1.0)
    ps = _power_state(gatt, bt_mac, rigcfg)
    assert ps["discharging"] == 3 and ps["charging"] == 2
    assert ps["discharging_meaning"] == "discharging"


def test_power_state_byte_packing(connected_dut, gatt, bt_mac, rigcfg):
    connected_dut.power_state(*CHARGING)
    time.sleep(1.0)
    ps = _power_state(gatt, bt_mac, rigcfg)
    want = CHARGING[0] | (CHARGING[1] << 2) | (CHARGING[2] << 4) | (CHARGING[3] << 6)
    assert ps["_byte"] == want
