"""SDL's view of the gamepad -- a different, arguably more consumer-relevant
layer than raw evdev: most game engines and SDL-based apps talk to
SDL_GameController, not evdev directly. See host/hil/sdlreport.py.

Mostly informational, not a hard functional gate: exact button/axis/hat
counts genuinely vary per profile, and there's no "should look like X" spec
yet -- this is prep work for the SInput profile (docs/TODO.md), establishing
what today's default HID gamepad descriptor actually looks like through SDL
before there's anything to compare it against.
"""

import pytest

from hil import sdlreport


@pytest.fixture(scope="session")
def sdl():
    with sdlreport.SDL():
        yield


def test_sdl_sees_a_game_controller(sdl, gamepad):
    dev, _ = gamepad
    r = sdlreport.report(dev.name)
    print(f"\n[sdl] {r}")
    assert r["is_game_controller"], (
        f"SDL only sees {dev.name!r} as a raw joystick, not a GameController "
        "(no mapping) -- most SDL-based apps/engines won't recognize it"
    )


def test_sdl_coverage_vs_firmware(sdl, gamepad, dut):
    dev, _ = gamepad
    r = sdlreport.report(dev.name)
    cov = sdlreport.coverage(r, dut.config())
    print(f"\n[sdl coverage] (sdl-visible, firmware-declared): {cov}")
    # Informational only -- Linux's hid-generic evdev mapping table caps this
    # well below the HID report's real count; no assertion until SInput gives
    # us a target to hit.


def test_sdl_rumble_unsupported(sdl, gamepad):
    """Documents the actual limit, rather than testing a requirement: this
    HID gamepad's report descriptor has no force-feedback usage, so neither
    the kernel evdev FF ioctl nor SDL's rumble (routed through that same
    ioctl for an unrecognized VID:PID) can drive it. If this ever starts
    passing, the descriptor gained real FF support -- update this test
    rather than deleting it."""
    dev, _ = gamepad
    r = sdlreport.report(dev.name)
    print(
        f"\n[sdl rumble] has_rumble={r['has_rumble']} call_ok={r['rumble_call_ok']} error={r['rumble_error']}"
    )
    assert not r["rumble_call_ok"], "rumble now works -- HID descriptor must have gained FF support"
