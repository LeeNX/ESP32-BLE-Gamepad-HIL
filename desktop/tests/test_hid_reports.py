"""Raw HID input reports, end to end over BLE: PRESS <n> on the serial channel
-> the DUT's HID report on this host shows exactly button <n>'s bit set.

hidapi reads the report bytes straight off the device, so this pins the
**firmware + HID descriptor + BLE transport** -- independent of how the OS or
SDL interpret the device (that's `-m sdl` / test_buttons.py). Needs the board
bonded to this host: run `pair-assist.py` first.

Isolation: hidapi does **not** seize the device -- the OS still delivers the
gamepad's input to the foreground app. Harmless on a dedicated tester
(Finder/Terminal ignore game-controller input). A hard lock needs
`IOHIDDeviceOpen(kIOHIDOptionsTypeSeizeDevice)` (macOS: as root); not done here.
"""

import pytest
from hidgamepad import drain, pressed_buttons, read_after

pytestmark = pytest.mark.hid


@pytest.fixture(autouse=True)
def _reset(dut, hidgamepad):
    dut.reset()
    drain(hidgamepad)
    yield


def _final_buttons(reports, n):
    """Buttons still down in the last report (empty list -> nothing seen)."""
    return pressed_buttons(reports[-1], n) if reports else None


def test_each_button_is_seen_alone(dut, hidgamepad):
    """Every configured button: press -> host sees {b} and nothing else;
    release -> host sees {}. One report each, one-to-one (distinct bit)."""
    n = dut.config()["buttons"]
    for b in range(1, n + 1):
        dut.press(b)
        down = _final_buttons(read_after(hidgamepad), n)
        assert down is not None, f"button {b}: no HID report reached the host"
        assert down == {b}, f"button {b}: host saw {down or 'nothing'}"

        dut.release(b)
        up = _final_buttons(read_after(hidgamepad), n)
        assert up == set(), f"button {b} release: host still sees {up}"


def test_two_buttons_combine(dut, hidgamepad):
    """Chords: 1+last are reported together, then clear independently."""
    n = dut.config()["buttons"]
    a, z = 1, n
    dut.press(a)
    dut.press(z)
    down = _final_buttons(read_after(hidgamepad), n)
    assert down == {a, z}, f"chord {a}+{z}: host saw {down}"

    dut.release(a)
    assert _final_buttons(read_after(hidgamepad), n) == {z}
    dut.release(z)
    assert _final_buttons(read_after(hidgamepad), n) == set()


def test_out_of_range_button_changes_nothing(dut, hidgamepad):
    """PRESS past the count is rejected on serial and never reaches the host."""
    from hil.serialdev import SerialError

    n = dut.config()["buttons"]
    with pytest.raises(SerialError):
        dut.press(n + 1)
    assert _final_buttons(read_after(hidgamepad), n) in (None, set())
