"""Hat switches. Each of the 4 configured hats, driven through its 8 directions,
must land on a distinct ABS_HAT{k}X/Y pair with the right (x,y) vector.

The library emits hat report fields in reverse of the hat index
(BleGamepad.cpp: field 0 = _hat4 when 4 hats are configured), so firmware
HAT n is expected to drive ABS_HAT{4-n}.
"""

import pytest
from evdev import ecodes

# direction code (BleGamepadConfiguration.h DPAD_*) -> (x, y)
DIR_VECTORS = {
    0: (0, 0), 1: (0, -1), 2: (1, -1), 3: (1, 0), 4: (1, 1),
    5: (0, 1), 6: (-1, 1), 7: (-1, 0), 8: (-1, -1),
}
HAT_PAIRS = {
    0: (ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y),
    1: (ecodes.ABS_HAT1X, ecodes.ABS_HAT1Y),
    2: (ecodes.ABS_HAT2X, ecodes.ABS_HAT2Y),
    3: (ecodes.ABS_HAT3X, ecodes.ABS_HAT3Y),
}


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
            dirs[d] = cap.abs_changes(cap.collect())
        connected_dut.hat(hat, 0)
        result[hat] = dirs
    return result


def test_each_hat_uses_one_distinct_pair(hat_sweep):
    used = {}
    for hat, dirs in hat_sweep.items():
        codes = {c for ch in dirs.values() for c in ch}
        hat_axes = codes & set(sum(HAT_PAIRS.values(), ()))
        assert hat_axes, f"HAT {hat} moved no ABS_HAT* axis"
        pair = None
        for k, (cx, cy) in HAT_PAIRS.items():
            if hat_axes <= {cx, cy}:
                pair = k
        assert pair is not None, f"HAT {hat} spread across pairs: {hat_axes}"
        used[hat] = pair
    assert len(set(used.values())) == len(used), f"hats collide: {used}"


def test_hat_index_is_reversed(hat_sweep):
    for hat, dirs in hat_sweep.items():
        codes = {c for ch in dirs.values() for c in ch}
        expected_x, expected_y = HAT_PAIRS[4 - hat]
        assert codes <= {expected_x, expected_y}, (
            f"HAT {hat} expected ABS_HAT{4 - hat}, got {codes}")


def test_hat_direction_vectors(hat_sweep):
    problems = []
    for hat, dirs in hat_sweep.items():
        cx, cy = HAT_PAIRS[4 - hat]
        for d, changes in dirs.items():
            want_x, want_y = DIR_VECTORS[d]
            got_x = changes.get(cx, 0)
            got_y = changes.get(cy, 0)
            # normalise to sign (kernel reports -1/0/1 for hats)
            if (got_x, got_y) != (want_x, want_y) and d != 0:
                problems.append(f"HAT {hat} dir {d}: got ({got_x},{got_y}) want ({want_x},{want_y})")
    assert not problems, "\n".join(problems)
