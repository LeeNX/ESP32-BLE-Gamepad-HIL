"""BEGIN -> connected -> evdev node appears with a sane capability set."""

from evdev import ecodes

from hil.evdev_utils import HAT_ABS_CODES


def test_firmware_alive(dut):
    assert dut.ping()


ALL_AXES = ["x", "y", "z", "rx", "ry", "rz", "s1", "s2"]
PROFILE_LAYOUT = {
    "default": dict(buttons=64, hats=4, special="none", axes=ALL_AXES),
    "signed-axes": dict(buttons=64, hats=4, special="none", axes=ALL_AXES),
    "specials": dict(buttons=16, hats=1, axes=ALL_AXES,
                     special="start,select,menu,home,back,volinc,voldec,volmute"),
    "minimal": dict(buttons=1, hats=0, special="none", axes=["x"]),
    "maxbtn": dict(buttons=128, hats=0, special="none", axes=[]),
    "reports": dict(buttons=16, hats=0, special="none", axes=["x", "y"]),
}


def test_config_matches_profile(dut, rigcfg):
    cfg = dut.config()
    assert cfg["profile"] == rigcfg["profile"]
    expected = PROFILE_LAYOUT[rigcfg["profile"]]
    assert cfg["buttons"] == expected["buttons"]
    assert cfg["hats"] == expected["hats"]
    assert cfg.get("special") == expected["special"]
    assert cfg["axes"] == expected["axes"]


def test_descriptor_within_buffer(connected_dut):
    """BleGamepad assembles the HID report descriptor into a fixed 150-byte
    buffer with no bounds check. RSIZE? reports the size the library computed."""
    sizes = connected_dut.report_sizes()
    assert 0 < sizes["descriptor"] <= 150, (
        f"HID report descriptor is {sizes['descriptor']} bytes -- "
        f"tempHidReportDescriptor[150] overflow risk")
    assert 0 < sizes["report"] <= 63, f"unexpected input report size {sizes['report']}"


def test_ble_connects(connected_dut):
    connected_dut.wait_connected(timeout=30)
    assert connected_dut.connected()


def test_evdev_node_capabilities(gamepad, dut):
    dev, _ = gamepad
    cfg = dut.config()
    caps = dev.capabilities()
    key_codes = set(caps.get(ecodes.EV_KEY, []))
    abs_codes = {c for c, _ in caps.get(ecodes.EV_ABS, [])}
    stick_codes = abs_codes - HAT_ABS_CODES

    # Linux runs out of gamepad key codes at ~79 (BTN_GAMEPAD..0x17e) for a
    # gamepad-application HID collection -- test_ranges::test_maxbtn_button_ceiling
    # pins that. Here just assert a healthy floor.
    assert len(key_codes) >= min(cfg["buttons"], 64)
    # enabled non-hat axes surface as non-hat ABS codes (Linux drops the 2nd
    # bare Usage(Slider) -- see test_axes.py -- so allow one short).
    want_axes = len(cfg["axes"])
    assert len(stick_codes) >= max(0, want_axes - 1)
    # Linux only ever creates one hat (ABS_HAT0) regardless of hat count.
    assert bool(abs_codes & HAT_ABS_CODES) == (cfg["hats"] > 0)


def test_all_input_nodes_listed(all_nodes, rigcfg):
    names = [d.name for d in all_nodes]
    assert names, "no /dev/input nodes matched the DUT name"
    print("DUT nodes:", names)
