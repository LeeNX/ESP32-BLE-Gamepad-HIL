"""Axes as a game sees them: `AXIS <name> <v>` on the serial channel -> SDL's
joystick view of the DUT moves the matching axis, monotonically, with the
endpoints where they should be.

`-m sdl`. SDL normalises whatever HID logical range the firmware uses to
[-1.0, +1.0], so the checks are in SDL units, not raw counts. Needs the board
bonded to this host (`pair-assist.py`).
"""

import pytest
import sdlgamepad

pytestmark = pytest.mark.sdl

TOL = 0.06  # SDL quantisation + a fuzz margin


@pytest.fixture(autouse=True)
def _reset(dut, sdlpad):
    dut.reset()
    sdlgamepad.settle()
    yield


def _wait_axis(sdlpad, i, want):
    """Poll until SDL axis i is within TOL of want; return its value."""
    sdlgamepad.wait_until(lambda: sdlgamepad.axes(sdlpad)[i], lambda v: abs(v - want) < TOL)
    return sdlgamepad.axes(sdlpad)[i]


def test_sdl_axis_count(dut, sdlpad):
    assert sdlpad.get_numaxes() == len(dut.config()["axes"])


def test_each_axis_endpoints_and_centre(dut, sdlpad):
    """min -> ~-1.0, centre -> ~0.0, max -> ~+1.0, on the matching SDL axis and
    no other."""
    cfg = dut.config()
    lo, hi = cfg["axesMin"], cfg["axesMax"]
    mid = (lo + hi) // 2
    for i, name in enumerate(cfg["axes"]):
        for value, want in ((lo, -1.0), (mid, 0.0), (hi, 1.0)):
            dut.axis(name, value)
            got = _wait_axis(sdlpad, i, want)
            assert abs(got - want) < TOL, (
                f"axis {name} (SDL #{i}) = {value} -> {got:+.3f}, expected {want:+.1f}"
            )
        dut.axis(name, lo)
        sdlgamepad.settle()


def test_axis_is_monotonic(dut, sdlpad):
    """Sweeping one axis low->high never makes SDL's reading go backwards."""
    cfg = dut.config()
    if not cfg["axes"]:
        pytest.skip("profile has no axes")
    lo, hi = cfg["axesMin"], cfg["axesMax"]
    name = cfg["axes"][0]
    last = -2.0
    for step in range(9):
        target = -1.0 + 2.0 * step / 8
        dut.axis(name, lo + (hi - lo) * step // 8)
        cur = _wait_axis(sdlpad, 0, target)
        assert cur >= last - TOL, f"axis {name}: {cur:+.3f} < previous {last:+.3f}"
        last = cur


def test_axes_are_independent(dut, sdlpad):
    """Moving one axis doesn't move another SDL axis."""
    cfg = dut.config()
    if len(cfg["axes"]) < 2:
        pytest.skip("need >= 2 axes")
    hi = cfg["axesMax"]
    dut.axis(cfg["axes"][0], hi)
    _wait_axis(sdlpad, 0, 1.0)
    baseline = sdlgamepad.axes(sdlpad)
    dut.axis(cfg["axes"][1], hi)
    _wait_axis(sdlpad, 1, 1.0)
    after = sdlgamepad.axes(sdlpad)
    assert abs(after[0] - baseline[0]) < TOL, (
        f"axis 0 drifted {baseline[0]:+.3f} -> {after[0]:+.3f}"
    )
    assert after[1] > baseline[1] + 0.5, "axis 1 didn't move"
