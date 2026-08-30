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
            problems.append((i, f"button {i}: {len(downs)} key-down events {downs}"))
            connected_dut.release(i)
            continue
        code = downs[0]
        mapping[i] = code

        cap.drain()
        connected_dut.release(i)
        ups = [c for c, v in cap.key_changes(cap.collect()).items() if v == 0]
        if ups != [code]:
            problems.append((i, f"button {i}: release gave {ups}, expected [{code}]"))
    return mapping, problems


def test_every_button_reports_cleanly(button_sweep, connected_dut):
    """Strict for buttons Linux can represent (<=80); buttons 81..128 on the
    maxbtn profile are handled by test_buttons_beyond_80."""
    mapping, problems = button_sweep
    low_problems = [msg for i, msg in problems if i <= 80]
    assert not low_problems, "\n".join(low_problems)
    n = min(connected_dut.config()["buttons"], 80)
    assert len([i for i in mapping if i <= 80]) == n


def test_button_mapping_is_one_to_one(button_sweep):
    mapping, _ = button_sweep
    codes = [c for i, c in mapping.items() if i <= 80]
    dupes = {c for c in codes if codes.count(c) > 1}
    assert not dupes, f"buttons collide on key codes: {dupes}"


def test_button_codes_are_known_gamepad_keys(button_sweep):
    """Kernel hid-input maps HID Button usages 1..16 to BTN_SOUTH.. then 17+ to
    BTN_TRIGGER_HAPPY1.. (which runs to +63, past the named BTN_TRIGGER_HAPPY40).
    Not asserting the exact code per button (kernel-version sensitive), just
    that they land in the gamepad/joystick key space. Only buttons <=80 have a
    defined code -- the HAPPY block runs out after that (see
    test_buttons_beyond_80)."""
    mapping, _ = button_sweep
    ok_lo = ecodes.BTN_JOYSTICK
    ok_hi = ecodes.BTN_TRIGGER_HAPPY1 + 63  # BTN_TRIGGER_HAPPY block is 64 wide
    strays = {i: c for i, c in mapping.items()
              if i <= 80 and not (ok_lo <= c <= ok_hi)}
    assert not strays, f"buttons mapped outside the gamepad key range: {strays}"


def test_buttons_beyond_80(button_sweep, connected_dut):
    """The maxbtn profile (128 buttons) walks off the end of the
    BTN_TRIGGER_HAPPY block. Pin whatever Linux actually does with buttons
    81..128 as a known result rather than a surprise -- if they get distinct
    codes this passes; if the kernel drops them it xfails."""
    if connected_dut.config()["buttons"] <= 80:
        pytest.skip("profile has <=80 buttons")
    mapping, problems = button_sweep
    high = {i: c for i, c in mapping.items() if i > 80}
    missing = [i for i in range(81, connected_dut.config()["buttons"] + 1)
               if i not in high or high[i] == 0]
    if missing:
        pytest.xfail(f"Linux gave no usable key code to buttons {missing[:8]}"
                     f"{'...' if len(missing) > 8 else ''} "
                     f"(HID Button usages past the BTN_TRIGGER_HAPPY block)")
    assert len(set(high.values())) == len(high), "high buttons collide on codes"


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
