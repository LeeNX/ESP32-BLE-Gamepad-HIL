"""Hat switches.

Ground truth on Linux for the `default` profile (4 hats configured):
  - The kernel's hid-input creates ONE hat (ABS_HAT0X/ABS_HAT0Y). Additional
    HID Usage(Hat Switch) fields in this library's descriptor get no ABS code.
  - The library emits hat report fields in reverse of the hat index
    (BleGamepad.cpp: report field 0 = _hat4 when 4 hats are configured), so the
    single working hat is driven by firmware `HAT 4`, and `HAT 1..3` do nothing
    visible on Linux.

So: `HAT <hatcount>` is the functional test; `test_extra_hats_map` pins the
"only one hat surfaces" limitation as a strict xfail (flips to a failure if it
ever starts working), and `test_working_hat_is_the_last_index` pins the
reversal.
"""

import pytest
from evdev import ecodes

# direction code (BleGamepadConfiguration.h DPAD_*) -> (x, y)
DIR_VECTORS = {
    0: (0, 0), 1: (0, -1), 2: (1, -1), 3: (1, 0), 4: (1, 1),
    5: (0, 1), 6: (-1, 1), 7: (-1, 0), 8: (-1, -1),
}
HAT0 = (ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y)
ALL_HAT_CODES = set(range(ecodes.ABS_HAT0X, ecodes.ABS_HAT3Y + 1))


@pytest.fixture(scope="module")
def hat_sweep(connected_dut, gamepad):
    dev, cap = gamepad
    nhats = connected_dut.config()["hats"]
    result = {}
    for hat in range(1, nhats + 1):
        dirs = {}
        for d in range(0, 9):
            connected_dut.hat(hat, 0)
            cap.collect(settle=0.15)
            cap.drain()
            connected_dut.hat(hat, d)
            dirs[d] = {c: v for c, v in cap.abs_changes(cap.collect()).items()
                       if c in ALL_HAT_CODES}
        connected_dut.hat(hat, 0)
        result[hat] = dirs
    return result


def _last_hat(connected_dut):
    return connected_dut.config()["hats"]


def test_working_hat_is_the_last_index(hat_sweep, connected_dut):
    """The one hat that surfaces is driven by the highest firmware hat index
    (the reversed-emission quirk), and it uses ABS_HAT0."""
    last = _last_hat(connected_dut)
    codes = {c for ch in hat_sweep[last].values() for c in ch}
    assert codes and codes <= set(HAT0), (
        f"HAT {last} expected to drive ABS_HAT0, got {codes or 'nothing'}")
    for lower in range(1, last):
        moved = {c for ch in hat_sweep[lower].values() for c in ch}
        assert not moved, f"HAT {lower} unexpectedly moved {moved} (reversal quirk changed?)"


def test_working_hat_direction_vectors(hat_sweep, connected_dut):
    last = _last_hat(connected_dut)
    cx, cy = HAT0
    problems = []
    for d, changes in hat_sweep[last].items():
        if d == 0:
            continue
        got = (changes.get(cx, 0), changes.get(cy, 0))
        want = DIR_VECTORS[d]
        if got != want:
            problems.append(f"dir {d}: got {got} want {want}")
    assert not problems, "HAT {}: ".format(last) + "; ".join(problems)


@pytest.mark.xfail(reason="Linux hid-input only creates ABS_HAT0 for the first "
                          "HID Usage(Hat Switch); this library's extra hat "
                          "fields get no ABS code", strict=True)
def test_extra_hats_map(hat_sweep, connected_dut):
    last = _last_hat(connected_dut)
    if last < 2:
        pytest.skip("single-hat profile")
    # firmware HAT (last-1) should drive ABS_HAT1, etc. -- assert at least one
    # of the lower hats produces any hat-axis motion.
    moved = {c for lower in range(1, last)
             for ch in hat_sweep[lower].values() for c in ch}
    assert moved, "no extra hat surfaced"
