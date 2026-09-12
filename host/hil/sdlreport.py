"""SDL's view of the HIL gamepad -- a different consumer layer than raw evdev
(hil.evdev_utils) or the kernel HID descriptor (test_descriptor.py). Most
game engines and SDL-based apps talk to SDL2's GameController API, not evdev
directly, and Linux's generic hid-generic evdev mapping table caps how many
of the HID report's buttons/axes/hats ever become visible that way at all --
this quantifies the gap, and checks force-feedback (rumble) support, which
this HID descriptor has no usage for and neither the kernel evdev FF ioctl
nor SDL's rumble (routed through that same ioctl for an unrecognized
VID:PID) can drive.

Needs `pysdl2` + `pysdl2-dll` (tester/requirements.txt) -- the latter bundles
a prebuilt SDL2 .so, so no system package / sudo is needed. Joystick +
game-controller subsystems only, no video -- works headless with no
SDL_VIDEODRIVER dance.
"""

import ctypes

import sdl2


class SDL:
    """`with SDL(): ...` -- inits/quits the subsystems this module needs."""

    def __enter__(self):
        rc = sdl2.SDL_Init(sdl2.SDL_INIT_JOYSTICK | sdl2.SDL_INIT_GAMECONTROLLER)
        if rc != 0:
            raise RuntimeError(f"SDL_Init failed: {sdl2.SDL_GetError().decode()}")
        return self

    def __exit__(self, *exc):
        sdl2.SDL_Quit()


def _guid_str(guid):
    buf = ctypes.create_string_buffer(64)
    sdl2.SDL_JoystickGetGUIDString(guid, buf, 64)
    return buf.value.decode()


def find_index(name_substr):
    """SDL joystick device index whose name contains `name_substr`, or None."""
    for i in range(sdl2.SDL_NumJoysticks()):
        n = sdl2.SDL_JoystickNameForIndex(i)
        if n and name_substr in n.decode():
            return i
    return None


def report(name_substr):
    """SDL's view of the device matching `name_substr`. Caller must already
    hold an active `SDL()` context. Raises LookupError if SDL doesn't see it
    (e.g. not connected, or BlueZ hasn't handed it to the kernel yet)."""
    idx = find_index(name_substr)
    if idx is None:
        raise LookupError(f"no SDL joystick matching {name_substr!r}")

    name = sdl2.SDL_JoystickNameForIndex(idx)
    out = {
        "name": name.decode() if name else None,
        "vid": sdl2.SDL_JoystickGetDeviceVendor(idx),
        "pid": sdl2.SDL_JoystickGetDeviceProduct(idx),
        "guid": _guid_str(sdl2.SDL_JoystickGetDeviceGUID(idx)),
        "is_game_controller": bool(sdl2.SDL_IsGameController(idx)),
    }

    js = sdl2.SDL_JoystickOpen(idx)
    try:
        out["axes"] = sdl2.SDL_JoystickNumAxes(js)
        out["buttons"] = sdl2.SDL_JoystickNumButtons(js)
        out["hats"] = sdl2.SDL_JoystickNumHats(js)
        out["has_rumble"] = bool(sdl2.SDL_JoystickHasRumble(js))
        # A real attempt, not just the capability query -- SDL_JoystickHasRumble
        # can answer true optimistically for backends that only find out on use.
        rc = sdl2.SDL_JoystickRumble(js, 0xFFFF, 0xFFFF, 200)
        out["rumble_call_ok"] = rc == 0
        out["rumble_error"] = None if rc == 0 else sdl2.SDL_GetError().decode()

        out["mapping"] = None
        if out["is_game_controller"]:
            gc = sdl2.SDL_GameControllerOpen(idx)
            try:
                m = sdl2.SDL_GameControllerMapping(gc)
                out["mapping"] = m.decode() if m else None
            finally:
                sdl2.SDL_GameControllerClose(gc)
    finally:
        sdl2.SDL_JoystickClose(js)
    return out


def coverage(sdl_report, fw_config):
    """(sdl-visible, firmware-declared) pairs for buttons/axes/hats -- how
    much of the HID report descriptor's real surface (from SerialDev.config())
    Linux's hid-generic evdev mapping actually exposes to SDL/evdev
    consumers. See docs/TODO.md "SInput protocol" -- this is the gap that
    prompted looking at an alternate report format in the first place."""
    return {
        "buttons": (sdl_report["buttons"], fw_config["buttons"]),
        "axes": (sdl_report["axes"], len(fw_config["axes"])),
        "hats": (sdl_report["hats"], fw_config["hats"]),
    }


if __name__ == "__main__":
    import json
    import sys

    substr = sys.argv[1] if len(sys.argv) > 1 else "HILpad"
    with SDL():
        print(json.dumps(report(substr), indent=2))
