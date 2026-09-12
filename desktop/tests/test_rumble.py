"""Force-feedback (rumble) via SDL -- documents the actual limit rather than
testing a requirement. This HID descriptor has no FF usage, so SDL's rumble
(which needs either that or a hardcoded per-VID/PID driver) can't drive it.
Confirmed False on both macOS (here) and the Linux rig (host/tests/
test_sdl_report.py) -- see README "What macOS/SDL does differently from the
Linux rig". Independent of the firmware profile's button/axis/hat shape, so
this doesn't need a fresh bond after a profile change like the other `-m sdl`
tests do.
"""

import pytest
import sdlgamepad

pytestmark = pytest.mark.sdl


def test_rumble_unsupported(sdlpad):
    ok = sdlgamepad.rumble(sdlpad)
    assert not ok, "rumble now works -- HID descriptor must have gained FF support"
