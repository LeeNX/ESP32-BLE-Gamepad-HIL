"""Full-range coverage that the per-feature suites don't pin down:

  * axis endpoints -- commanding exactly axesMin / centre / axesMax lands at
    the evdev absinfo min / centre / max (within the kernel's fuzz/flat), for
    both the unsigned `default` and the signed `signed-axes` profiles;
  * the `minimal` profile really is minimal (one button, one axis, no hats);
  * the `maxbtn` profile exercises the library's 128-button ceiling -- the
    per-button cleanliness is covered by test_buttons.py running against it,
    here we just assert the count surfaced.

Run the profile-specific tests by flashing that profile:
    scripts/hil.sh --profiles "default signed-axes minimal maxbtn"
"""

import pytest
from evdev import ecodes

from hil.evdev_utils import HAT_ABS_CODES

WELL_KNOWN = {
    "x": ecodes.ABS_X,
    "y": ecodes.ABS_Y,
    "z": ecodes.ABS_Z,
    "rx": ecodes.ABS_RX,
    "ry": ecodes.ABS_RY,
    "rz": ecodes.ABS_RZ,
    "s1": ecodes.ABS_THROTTLE,
}


def _abs_code_for(dev, tok):
    code = WELL_KNOWN.get(tok)
    caps = {c for c, _ in dev.capabilities().get(ecodes.EV_ABS, [])}
    return code if code in caps else None


@pytest.fixture(scope="module")
def endpoints(connected_dut, gamepad):
    """{tok: {"lo":(cmd,evdev), "mid":..., "hi":...}, absinfo}."""
    dev, cap = gamepad
    cfg = connected_dut.config()
    lo, hi = cfg["axesMin"], cfg["axesMax"]
    mid = (lo + hi) // 2
    out = {}
    for tok in cfg["axes"]:
        code = _abs_code_for(dev, tok)
        if code is None:
            continue
        ai = dev.absinfo(code)
        seen = {}
        for name, cmd in (("lo", lo), ("mid", mid), ("hi", hi)):
            # move from the *opposite* rail so every probe is a real change
            base = hi if cmd != hi else lo
            connected_dut.axis(tok, base)
            cap.collect(settle=0.15)
            cap.drain()
            connected_dut.axis(tok, cmd)
            ch = {c: v for c, v in cap.abs_changes(cap.collect()).items() if c not in HAT_ABS_CODES}
            seen[name] = (cmd, ch.get(code))
        out[tok] = {"absinfo": ai, "seen": seen}
    connected_dut.reset()
    return out


def _tol(ai):
    return max(ai.flat * 2, (ai.max - ai.min) * 0.03, 2)


def test_axis_endpoints_land_at_absinfo_extremes(endpoints):
    problems = []
    for tok, d in endpoints.items():
        ai, seen = d["absinfo"], d["seen"]
        tol = _tol(ai)
        targets = {"lo": ai.min, "mid": (ai.min + ai.max) // 2, "hi": ai.max}
        for name, (cmd, got) in seen.items():
            if got is None:
                problems.append(f"{tok} {name}: commanding {cmd} produced no ABS event")
            elif abs(got - targets[name]) > tol:
                problems.append(
                    f"{tok} {name}: commanded {cmd} -> evdev {got}, "
                    f"expected ~{targets[name]} (+/-{tol:.0f})"
                )
    assert not problems, "\n".join(problems)


def test_axis_endpoints_are_ordered(endpoints):
    for tok, d in endpoints.items():
        lo = d["seen"]["lo"][1]
        mid = d["seen"]["mid"][1]
        hi = d["seen"]["hi"][1]
        if None in (lo, mid, hi):
            continue
        assert lo < mid < hi, f"{tok}: evdev values not ordered lo<mid<hi: {lo},{mid},{hi}"


def test_signed_axis_reaches_negative_rail(connected_dut, endpoints, rigcfg):
    if connected_dut.config()["axesMin"] >= 0:
        pytest.skip("unsigned-axes profile")
    for tok, d in endpoints.items():
        lo_cmd, lo_evdev = d["seen"]["lo"]
        assert lo_cmd < 0
        if lo_evdev is not None:
            assert lo_evdev < 0, (
                f"{tok}: negative command {lo_cmd} -> non-negative evdev {lo_evdev}"
            )


def test_minimal_profile_is_minimal(connected_dut, gamepad):
    cfg = connected_dut.config()
    if cfg["profile"] != "minimal":
        pytest.skip("not the minimal profile")
    dev, _ = gamepad
    assert cfg["buttons"] == 1
    assert cfg["hats"] == 0
    assert cfg["axes"] == ["x"]
    abs_codes = {c for c, _ in dev.capabilities().get(ecodes.EV_ABS, [])}
    assert abs_codes - HAT_ABS_CODES == {ecodes.ABS_X}
    assert not abs_codes & HAT_ABS_CODES


def test_maxbtn_button_ceiling(connected_dut, gamepad):
    """The library advertises 128 buttons, but Linux's hid-input maps a
    gamepad-application Button usage to `BTN_GAMEPAD + n` and runs out of the
    named gamepad/joystick key block at 0x17e -- so a host sees ~79 buttons,
    not 128. Pin that as the known ceiling (a game using evdev/SDL sees the
    same); test_buttons::test_buttons_beyond_80 covers the dropped ones."""
    cfg = connected_dut.config()
    if cfg["profile"] != "maxbtn":
        pytest.skip("not the maxbtn profile")
    dev, _ = gamepad
    assert cfg["buttons"] == 128
    key_codes = sorted(dev.capabilities().get(ecodes.EV_KEY, []))
    print(f"maxbtn: {len(key_codes)} evdev key codes, 0x{key_codes[0]:x}..0x{key_codes[-1]:x}")
    assert 64 <= len(key_codes) <= 128
    assert key_codes[0] == ecodes.BTN_GAMEPAD  # 0x130 / 304
    assert len(key_codes) < 100  # the finding: nowhere near 128
