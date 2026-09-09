"""Buttons as a game sees them: PRESS <n> on the serial channel -> SDL's
joystick view of the DUT reports exactly button <n> down.

`-m sdl` -- SDL's per-OS joystick driver (IOKit / RawInput / evdev) does the
HID parsing and control numbering, the same as any game. This is the
behavioural check; raw report bytes are `-m hid` / test_hid_reports.py.

Needs the board bonded to this host: run `pair-assist.py` first.
"""

import pytest
import sdlgamepad

pytestmark = pytest.mark.sdl


@pytest.fixture(autouse=True)
def _reset(dut, sdlpad):
    dut.reset()
    sdlgamepad.settle()
    yield


def test_sdl_sees_the_profile_shape(dut, sdlpad):
    """SDL's button / axis / hat counts match the firmware profile."""
    cfg = dut.config()
    assert sdlpad.get_numbuttons() == cfg["buttons"]
    assert sdlpad.get_numaxes() == len(cfg["axes"])
    assert sdlpad.get_numhats() == cfg["hats"]


def test_each_button_is_seen_alone(dut, sdlpad):
    """Every configured button: press -> SDL sees {b} only; release -> {}."""
    n = dut.config()["buttons"]
    for b in range(1, n + 1):
        dut.press(b)
        sdlgamepad.settle()
        assert sdlgamepad.buttons(sdlpad) == {b}, (
            f"button {b}: SDL saw {sdlgamepad.buttons(sdlpad)}"
        )

        dut.release(b)
        sdlgamepad.settle()
        assert sdlgamepad.buttons(sdlpad) == set(), f"button {b} release: SDL still sees it"


def test_buttons_are_one_to_one(dut, sdlpad):
    """No two firmware buttons collapse onto the same SDL index."""
    n = dut.config()["buttons"]
    seen = {}
    for b in range(1, n + 1):
        dut.press(b)
        sdlgamepad.settle()
        down = sdlgamepad.buttons(sdlpad)
        dut.release(b)
        sdlgamepad.settle()
        assert len(down) == 1, f"button {b}: SDL saw {down}"
        idx = next(iter(down))
        assert idx not in seen.values(), f"buttons {seen}, {b} all map to SDL #{idx}"
        seen[b] = idx


def test_two_buttons_combine(dut, sdlpad):
    n = dut.config()["buttons"]
    a, z = 1, n
    dut.press(a)
    dut.press(z)
    sdlgamepad.settle()
    assert sdlgamepad.buttons(sdlpad) == {a, z}

    dut.release(a)
    sdlgamepad.settle()
    assert sdlgamepad.buttons(sdlpad) == {z}
    dut.release(z)
    sdlgamepad.settle()
    assert sdlgamepad.buttons(sdlpad) == set()
