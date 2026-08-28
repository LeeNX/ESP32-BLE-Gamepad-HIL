"""Every gamepad button: press -> exactly one EV_KEY down, release -> that same
key up, and the whole set is a one-to-one mapping onto distinct key codes."""

import pytest
from evdev import ecodes


@pytest.fixture(scope="module")
def button_sweep(connected_dut, gamepad):
    """Press/release each button once. Returns (mapping, problems)."""
    dev, cap = gamepad
    n = connected_dut.config()["buttons"]
    mapping = {}
    problems = []
    for i in range(1, n + 1):
        cap.drain()
        connected_dut.press(i)
        downs = [c for c, v in cap.key_changes(cap.collect()).items() if v == 1]
        if len(downs) != 1:
            problems.append(f"button {i}: {len(downs)} key-down events {downs}")
            connected_dut.release(i)
            continue
        code = downs[0]
        mapping[i] = code

        cap.drain()
        connected_dut.release(i)
        ups = [c for c, v in cap.key_changes(cap.collect()).items() if v == 0]
        if ups != [code]:
            problems.append(f"button {i}: release gave {ups}, expected [{code}]")
    return mapping, problems


def test_every_button_reports_cleanly(button_sweep, connected_dut):
    mapping, problems = button_sweep
    assert not problems, "\n".join(problems)
    assert len(mapping) == connected_dut.config()["buttons"]


def test_button_mapping_is_one_to_one(button_sweep):
    mapping, _ = button_sweep
    codes = list(mapping.values())
    dupes = {c for c in codes if codes.count(c) > 1}
    assert not dupes, f"buttons collide on key codes: {dupes}"


def test_button_codes_are_known_gamepad_keys(button_sweep):
    """Kernel hid-input maps HID Button usages to BTN_* / BTN_TRIGGER_HAPPY*.
    Not asserting the exact code per button (kernel-version sensitive), just
    that they're all in the gamepad/joystick key ranges."""
    mapping, _ = button_sweep
    ok_lo, ok_hi = ecodes.BTN_JOYSTICK, ecodes.BTN_TRIGGER_HAPPY40
    strays = {i: c for i, c in mapping.items() if not (ok_lo <= c <= ok_hi)}
    assert not strays, f"buttons mapped outside the gamepad key range: {strays}"


def test_button_hold_then_release_roundtrip(connected_dut, gamepad, button_sweep):
    """Spot-check that a held button stays down until explicitly released."""
    mapping, _ = button_sweep
    dev, cap = gamepad
    i = 1
    code = mapping[i]

    cap.drain()
    connected_dut.press(i)
    assert cap.key_changes(cap.collect()).get(code) == 1
    assert dev.active_keys() and code in dev.active_keys()

    connected_dut.release(i)
    cap.collect()
    assert code not in dev.active_keys()
