"""Hats as a game sees them: `HAT <idx> <0..8>` on the serial channel -> SDL's
joystick view moves the matching hat to the matching 8-way direction.

`-m sdl`. Needs a profile with hats (`default` = 4, `specials` = 1) and the
board bonded to this host (`pair-assist.py`).

macOS/SDL surfaces **all** the hats (unlike Linux evdev, which only ever makes
`ABS_HAT0`), but the library emits the hat fields **reversed**, so firmware hat
`h` shows up as SDL hat `n_hats - h`. That reversal is pinned here (it's the
desktop equivalent of the rig's reversed-hat xfail).
"""

import pytest
import sdlgamepad

pytestmark = pytest.mark.sdl

# firmware HAT <idx> <v>  ->  SDL get_hat() (x, y)   (v: 0=centre, 1=N, CW to 8=NW)
DIR_TO_XY = {
    0: (0, 0),
    1: (0, 1),
    2: (1, 1),
    3: (1, 0),
    4: (1, -1),
    5: (0, -1),
    6: (-1, -1),
    7: (-1, 0),
    8: (-1, 1),
}


@pytest.fixture(autouse=True)
def _hatcfg(dut, sdlpad):
    cfg = dut.config()
    if not cfg["hats"]:
        pytest.skip(f"profile {cfg['profile']!r} has no hats (use default / specials)")
    dut.reset()
    sdlgamepad.settle()
    yield cfg


def _sdl_index(n_hats, fw_hat):
    """The library reverses the hat fields."""
    return n_hats - fw_hat


def test_sdl_hat_count(_hatcfg, sdlpad):
    assert sdlpad.get_numhats() == _hatcfg["hats"]


def test_each_hat_all_directions(_hatcfg, dut, sdlpad):
    """Every firmware hat, every direction: the reversed SDL hat shows the right
    (x, y), and no other hat moves."""
    n = _hatcfg["hats"]
    for fw_hat in range(1, n + 1):
        want_idx = _sdl_index(n, fw_hat)
        for direction, xy in DIR_TO_XY.items():
            dut.hat(fw_hat, direction)
            sdlgamepad.wait_until(lambda: sdlgamepad.hats(sdlpad)[want_idx], xy)  # noqa: B023
            hats = sdlgamepad.hats(sdlpad)
            assert hats[want_idx] == xy, (
                f"fw hat {fw_hat} dir {direction} -> SDL hat {want_idx} = {hats[want_idx]}, want {xy}"
            )
            for other in range(n):
                if other != want_idx:
                    assert hats[other] == (0, 0), f"fw hat {fw_hat} also moved SDL hat {other}"
        dut.hat(fw_hat, 0)
        sdlgamepad.settle()


def test_hats_are_one_to_one(_hatcfg, dut, sdlpad):
    """Each firmware hat drives a distinct SDL hat."""
    n = _hatcfg["hats"]
    driven = {}
    for fw_hat in range(1, n + 1):
        dut.hat(fw_hat, 3)  # E
        sdlgamepad.wait_until(
            lambda: any(xy != (0, 0) for xy in sdlgamepad.hats(sdlpad)), lambda v: v
        )
        moved = {i for i, xy in enumerate(sdlgamepad.hats(sdlpad)) if xy != (0, 0)}
        dut.hat(fw_hat, 0)
        sdlgamepad.wait_until(
            lambda: all(xy == (0, 0) for xy in sdlgamepad.hats(sdlpad)), lambda v: v
        )
        assert len(moved) == 1, f"fw hat {fw_hat} moved SDL hats {moved}"
        idx = moved.pop()
        assert idx not in driven.values(), f"fw hats {driven} and {fw_hat} both drive SDL hat {idx}"
        driven[fw_hat] = idx
