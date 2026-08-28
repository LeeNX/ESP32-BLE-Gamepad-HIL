"""Each configured axis: commanding a value moves exactly one non-hat ABS axis,
monotonically, and the well-known axes land on their expected ABS_* code."""

import pytest
from evdev import ecodes

from hil.evdev_utils import HAT_ABS_CODES

KNOWN = {
    "x": ecodes.ABS_X, "y": ecodes.ABS_Y, "z": ecodes.ABS_Z,
    "rx": ecodes.ABS_RX, "ry": ecodes.ABS_RY, "rz": ecodes.ABS_RZ,
}


@pytest.fixture(scope="module")
def axis_sweep(connected_dut, gamepad):
    dev, cap = gamepad
    cfg = connected_dut.config()
    lo, hi = cfg["axesMin"], cfg["axesMax"]
    span = hi - lo
    probes = [lo + int(span * f) for f in (0.15, 0.5, 0.85)]

    result = {}
    for tok in cfg["axes"]:
        seen = []  # (commanded, {code: value})
        for v in probes:
            connected_dut.axis(tok, 0 if lo <= 0 <= hi else lo)
            cap.collect(settle=0.15)
            cap.drain()
            connected_dut.axis(tok, v)
            changes = {c: val for c, val in cap.abs_changes(cap.collect()).items()
                       if c not in HAT_ABS_CODES}
            seen.append((v, changes))
        connected_dut.axis(tok, 0 if lo <= 0 <= hi else lo)
        result[tok] = seen
    return result


def test_each_axis_moves_exactly_one_abs(axis_sweep):
    problems = []
    for tok, seen in axis_sweep.items():
        codes = set()
        for v, changes in seen:
            codes |= set(changes)
            if len(changes) != 1:
                problems.append(f"{tok} @ {v}: moved {list(changes)} (want exactly 1)")
        if len(codes) > 1:
            problems.append(f"{tok}: moved multiple ABS codes across sweep {codes}")
    assert not problems, "\n".join(problems)


def test_axis_mapping_is_one_to_one(axis_sweep):
    per_tok = {}
    for tok, seen in axis_sweep.items():
        codes = {c for _, ch in seen for c in ch}
        if len(codes) == 1:
            per_tok[tok] = next(iter(codes))
    vals = list(per_tok.values())
    assert len(set(vals)) == len(vals), f"axes collide on ABS codes: {per_tok}"


def test_axis_tracks_monotonically(axis_sweep):
    problems = []
    for tok, seen in axis_sweep.items():
        vals = [next(iter(ch.values())) for _, ch in seen if len(ch) == 1]
        if len(vals) != len(seen):
            continue  # covered by the "exactly one" test
        increasing = all(a < b for a, b in zip(vals, vals[1:]))
        decreasing = all(a > b for a, b in zip(vals, vals[1:]))
        if not (increasing or decreasing):
            problems.append(f"{tok}: not monotonic across probes -> {vals}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("tok,code", list(KNOWN.items()))
def test_well_known_axis_codes(axis_sweep, tok, code):
    seen = axis_sweep[tok]
    codes = {c for _, ch in seen for c in ch}
    assert codes == {code}, f"{tok} -> {codes}, expected {{{code}}} ({ecodes.ABS[code]})"
