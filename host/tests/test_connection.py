"""BEGIN -> connected -> evdev node appears with a sane capability set."""

from evdev import ecodes

from hil.evdev_utils import HAT_ABS_CODES


def test_firmware_alive(dut):
    assert dut.ping()


def test_config_matches_profile(dut, rigcfg):
    cfg = dut.config()
    assert cfg["profile"] == rigcfg["profile"]
    assert cfg["buttons"] == 64
    assert cfg["hats"] == 4
    assert cfg["axes"] == ["x", "y", "z", "rx", "ry", "rz", "s1", "s2"]


def test_ble_connects(connected_dut):
    connected_dut.wait_connected(timeout=30)
    assert connected_dut.connected()


def test_evdev_node_capabilities(gamepad, dut):
    dev, _ = gamepad
    caps = dev.capabilities()
    key_codes = set(caps.get(ecodes.EV_KEY, []))
    abs_codes = {c for c, _ in caps.get(ecodes.EV_ABS, [])}

    # 64 gamepad buttons -> at least 64 distinct key codes.
    assert len(key_codes) >= dut.config()["buttons"]
    # 6 sticks + 2 sliders -> some non-hat ABS axes; 4 hats -> hat ABS pairs.
    assert len(abs_codes - HAT_ABS_CODES) >= 6
    assert abs_codes & HAT_ABS_CODES


def test_all_input_nodes_listed(all_nodes, rigcfg):
    names = [d.name for d in all_nodes]
    assert names, "no /dev/input nodes matched the DUT name"
    print("DUT nodes:", names)
