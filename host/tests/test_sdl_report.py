"""SDL's view of the gamepad -- a different, arguably more consumer-relevant
layer than raw evdev: most game engines and SDL-based apps talk to
SDL_GameController, not evdev directly. See host/hil/sdlreport.py.

Mostly informational, not a hard functional gate: exact button/axis/hat
counts genuinely vary per profile, and there's no "should look like X" spec
yet -- this is prep work for the SInput profile (docs/TODO.md), establishing
what today's default HID gamepad descriptor actually looks like through SDL
before there's anything to compare it against. If SDL can't see the device
at all, that's a skip (flagged in the report), not a failure.
"""

import pytest

from hil import sdlreport


@pytest.fixture(scope="session")
def sdl():
    with sdlreport.SDL():
        yield


@pytest.fixture(scope="module")
def sdl_report(sdl, gamepad):
    """SDL's view of the DUT, fetched once and shared by the tests below.

    These tests are informational (see module docstring), not a functional
    gate -- a missing joystick means SDL just hasn't seen it yet (udev/BlueZ
    timing, a headless session quirk), not a firmware regression. Skip with
    a clear reason rather than failing/erroring, so it shows up as a flagged
    gap in the report instead of a false red."""
    dev, _ = gamepad
    try:
        return sdlreport.report(dev.name)
    except LookupError as e:
        print(f"\n[sdl] warning: {e}")
        pytest.skip(f"SDL can't see this device -- {e}")


def test_sdl_sees_a_game_controller(sdl_report, gamepad):
    dev, _ = gamepad
    print(f"\n[sdl] {sdl_report}")
    assert sdl_report["is_game_controller"], (
        f"SDL only sees {dev.name!r} as a raw joystick, not a GameController "
        "(no mapping) -- most SDL-based apps/engines won't recognize it"
    )


def test_sdl_coverage_vs_firmware(sdl_report, dut):
    cov = sdlreport.coverage(sdl_report, dut.config())
    print(f"\n[sdl coverage] (sdl-visible, firmware-declared): {cov}")
    # Informational only -- Linux's hid-generic evdev mapping table caps this
    # well below the HID report's real count; no assertion until SInput gives
    # us a target to hit.


def test_sdl_rumble_unsupported(sdl_report):
    """Documents the actual limit, rather than testing a requirement: this
    HID gamepad's report descriptor has no force-feedback usage, so neither
    the kernel evdev FF ioctl nor SDL's rumble (routed through that same
    ioctl for an unrecognized VID:PID) can drive it. If this ever starts
    passing, the descriptor gained real FF support -- update this test
    rather than deleting it."""
    print(
        f"\n[sdl rumble] has_rumble={sdl_report['has_rumble']} "
        f"call_ok={sdl_report['rumble_call_ok']} error={sdl_report['rumble_error']}"
    )
    assert not sdl_report["rumble_call_ok"], (
        "rumble now works -- HID descriptor must have gained FF support"
    )
