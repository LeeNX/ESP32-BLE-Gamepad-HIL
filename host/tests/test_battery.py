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
    assert gatt.read_battery_level(bt_mac) == level


def test_battery_level_matches_upower(connected_dut, gatt, bt_mac):
    connected_dut.battery(77)
    time.sleep(1.5)
    up = gatt.read_battery_upower(bt_mac)
    if up is None:
        pytest.skip("UPower does not list this device")
    assert up == 77


def test_battery_level_out_of_range_rejected(connected_dut):
    with pytest.raises(Exception):
        connected_dut.battery(101)


# --- Battery Power State (0x2A1A) --------------------------------------
# firmware POWERSTATE <info> <discharging> <charging> <level>, 2-bit fields:
#   info: 3=present   discharging: 2=not-discharging / 3=discharging
#   charging: 2=not-charging / 3=charging   level: 2=good / 3=critically-low
CHARGING = (3, 2, 3, 2)
DISCHARGING = (3, 3, 2, 2)


@pytest.fixture
def _needs_power_state(gatt, bt_mac):
    if gatt.read_power_state(bt_mac) is None:
        pytest.skip("0x2A1A Battery Power State characteristic not exposed")


def test_power_state_charging(connected_dut, gatt, bt_mac, _needs_power_state):
    connected_dut.power_state(*CHARGING)
    time.sleep(1.0)
    ps = gatt.read_power_state(bt_mac)
    assert ps["info"] == 3 and ps["charging"] == 3 and ps["discharging"] == 2
    assert ps["charging_meaning"] == "charging"


def test_power_state_discharging(connected_dut, gatt, bt_mac, _needs_power_state):
    connected_dut.power_state(*DISCHARGING)
    time.sleep(1.0)
    ps = gatt.read_power_state(bt_mac)
    assert ps["discharging"] == 3 and ps["charging"] == 2
    assert ps["discharging_meaning"] == "discharging"


def test_power_state_byte_packing(connected_dut, gatt, bt_mac, _needs_power_state):
    connected_dut.power_state(*CHARGING)
    time.sleep(1.0)
    ps = gatt.read_power_state(bt_mac)
    want = CHARGING[0] | (CHARGING[1] << 2) | (CHARGING[2] << 4) | (CHARGING[3] << 6)
    assert ps["_byte"] == want
