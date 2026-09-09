"""Read the DUT the way a game does -- SDL's joystick subsystem, via pygame.

SDL is what real apps use. Its per-OS joystick driver (IOKit on macOS, RawInput
/ WGI on Windows, evdev on Linux) applies the same HID parsing and control
numbering a game sees -- so `-m sdl` tests answer "what does an app on this OS
make of the device?", which raw HID reports (`-m hid`) can't.

Headless: SDL_VIDEODRIVER=dummy, no window. A game controller needs no TCC
grant on macOS. Poll model -- call pump() then read get_button/get_axis/get_hat;
no event queue needed.
"""

import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
# read state even though our process isn't foreground
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")

import pygame  # noqa: E402  -- after the SDL_* env is set


def init():
    pygame.init()
    pygame.joystick.init()


def _guid_vid_pid(vid, pid):
    # SDL joystick GUID: bytes 4-5 = vendor LE, 8-9 = product LE (hex chars
    # 8..12 and 16..20). e.g. VID 0x1D34 PID 0x8010 -> "341d" / "1080".
    return f"{vid & 0xFF:02x}{vid >> 8:02x}", f"{pid & 0xFF:02x}{pid >> 8:02x}"


def find(vid, pid, timeout=10.0):
    """The SDL Joystick for VID/PID (init()ed), or None. Retries -- a fresh
    bond can take a moment to enumerate."""
    want_vid, want_pid = _guid_vid_pid(vid, pid)
    end = time.time() + timeout
    while time.time() < end:
        pygame.event.pump()
        for i in range(pygame.joystick.get_count()):
            j = pygame.joystick.Joystick(i)
            g = j.get_guid()
            if g[8:12] == want_vid and g[16:20] == want_pid:
                j.init()
                return j
        time.sleep(0.3)
    return None


def settle(seconds=0.2):
    """Let a state change propagate through SDL, then refresh."""
    time.sleep(seconds)
    pygame.event.pump()


def buttons(j):
    """1-based set of buttons SDL currently reports down."""
    pygame.event.pump()
    return {i + 1 for i in range(j.get_numbuttons()) if j.get_button(i)}


def axes(j):
    pygame.event.pump()
    return [j.get_axis(i) for i in range(j.get_numaxes())]


def hats(j):
    pygame.event.pump()
    return [j.get_hat(i) for i in range(j.get_numhats())]
