"""Each configured axis: commanding a value moves exactly one non-hat ABS axis,
monotonically, and the well-known axes land on their expected ABS_* code.

Ground truth on Linux for the `default` profile (x,y,z,rx,ry,rz,s1,s2):
  x->ABS_X  y->ABS_Y  z->ABS_Z  rx->ABS_RX  ry->ABS_RY  rz->ABS_RZ
  s1->ABS_THROTTLE   s2-> (nothing)
The kernel's hid-input maps the first HID Usage(Slider) to ABS_THROTTLE but a
second bare Usage(Slider) in the same collection gets no distinct evdev code --
a game using evdev/SDL sees the same. `test_slider2...` pins that as a known
limitation (strict xfail: it flips to a failure if the library/kernel ever
starts exposing it).
"""

import pytest
from evdev import ecodes

from hil.evdev_utils import HAT_ABS_CODES

KNOWN = {
    "x": ecodes.ABS_X, "y": ecodes.ABS_Y, "z": ecodes.ABS_Z,
    "rx": ecodes.ABS_RX, "ry": ecodes.ABS_RY, "rz": ecodes.ABS_RZ,
    "s1": ecodes.ABS_THROTTLE,
}
LINUX_UNMAPPED = {"s2"}


@pytest.fixture(scope="module")
def axis_sweep(connected_dut, gamepad):
    dev, cap = gamepad
    cfg = connected_dut.config()
    lo, hi = cfg["axesMin"], cfg["axesMax"]
    span = hi - lo
    probes = [lo + int(span * f) for f in (0.15, 0.5, 0.85)]
    # For a signed axis the 0.5 probe lands on 0 -- which is also the natural
    # baseline, so commanding it produces no event. Baseline from lo instead so
    # every probe is a real change.
    baseline = lo
    probes = [p for p in probes if p != baseline]

    result = {}
    for tok in cfg["axes"]:
        seen = []
        for v in probes:
            connected_dut.axis(tok, baseline)
            cap.collect(settle=0.15)
            cap.drain()
            connected_dut.axis(tok, v)
            changes = {c: val for c, val in cap.abs_changes(cap.collect()).items()
                       if c not in HAT_ABS_CODES}
            seen.append((v, changes))
        connected_dut.axis(tok, baseline)
        result[tok] = seen
    return result


def _mapped_tokens(axis_sweep):
    return [t for t in axis_sweep if t not in LINUX_UNMAPPED]


def test_each_mapped_axis_moves_exactly_one_abs(axis_sweep):
    problems = []
    for tok in _mapped_tokens(axis_sweep):
        codes = set()
        for v, changes in axis_sweep[tok]:
            codes |= set(changes)
            if len(changes) != 1:
                problems.append(f"{tok} @ {v}: moved {list(changes)} (want exactly 1)")
        if len(codes) > 1:
            problems.append(f"{tok}: moved multiple ABS codes across sweep {codes}")
    assert not problems, "\n".join(problems)


def test_axis_mapping_is_one_to_one(axis_sweep):
    per_tok = {}
    for tok in _mapped_tokens(axis_sweep):
        codes = {c for _, ch in axis_sweep[tok] for c in ch}
        if len(codes) == 1:
            per_tok[tok] = next(iter(codes))
    vals = list(per_tok.values())
    assert len(set(vals)) == len(vals), f"axes collide on ABS codes: {per_tok}"


def test_axis_tracks_monotonically(axis_sweep):
    problems = []
    for tok in _mapped_tokens(axis_sweep):
        seen = axis_sweep[tok]
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
    if tok not in axis_sweep:
        pytest.skip(f"{tok} not in this profile")
    codes = {c for _, ch in axis_sweep[tok] for c in ch}
    assert codes == {code}, f"{tok} -> {codes}, expected {{{code}}} ({ecodes.ABS[code]})"


@pytest.mark.xfail(reason="a 2nd bare HID Usage(Slider) gets no distinct evdev "
                          "ABS code on Linux", strict=True)
def test_slider2_maps_to_an_abs(axis_sweep):
    if "s2" not in axis_sweep:
        pytest.skip("profile has no s2 axis")
    codes = {c for _, ch in axis_sweep["s2"] for c in ch}
    assert len(codes) == 1, f"s2 moved {codes}"
