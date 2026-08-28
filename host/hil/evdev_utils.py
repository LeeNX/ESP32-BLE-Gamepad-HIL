"""Locate the DUT's evdev node and capture input events from it."""

import select
import time

import evdev
from evdev import ecodes

# ABS_HAT0X..ABS_HAT3Y -- the 8 hat axis codes, kept separate from stick axes.
HAT_ABS_CODES = {
    ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y,
    ecodes.ABS_HAT1X, ecodes.ABS_HAT1Y,
    ecodes.ABS_HAT2X, ecodes.ABS_HAT2Y,
    ecodes.ABS_HAT3X, ecodes.ABS_HAT3Y,
}


def _open(path):
    try:
        return evdev.InputDevice(path)
    except OSError:
        return None


def find_gamepad(name_contains, timeout=25.0):
    """The DUT can expose several event nodes (gamepad, consumer control, ...).
    Return the one that carries both keys and stick axes."""
    deadline = time.time() + timeout
    last_seen = []
    while time.time() < deadline:
        last_seen = []
        for path in evdev.list_devices():
            dev = _open(path)
            if dev is None:
                continue
            if name_contains in dev.name:
                last_seen.append(dev.name)
                caps = dev.capabilities()
                abs_codes = {c for c, _ in caps.get(ecodes.EV_ABS, [])}
                stick_axes = abs_codes - HAT_ABS_CODES
                if ecodes.EV_KEY in caps and stick_axes:
                    return dev
            dev.close()
        time.sleep(0.5)
    raise TimeoutError(
        f"no gamepad evdev node matching {name_contains!r} within {timeout}s "
        f"(saw: {last_seen or 'nothing'})"
    )


def find_all_nodes(name_contains):
    out = []
    for path in evdev.list_devices():
        dev = _open(path)
        if dev is None:
            continue
        if name_contains in dev.name:
            out.append(dev)
        else:
            dev.close()
    return out


class Capture:
    """Thin event collector around one InputDevice."""

    def __init__(self, dev):
        self.dev = dev

    def drain(self):
        try:
            while self.dev.read_one() is not None:
                pass
        except BlockingIOError:
            pass

    def collect(self, settle=0.35, hard_timeout=2.0):
        """Read events until the stream goes quiet for `settle` seconds."""
        events = []
        start = time.time()
        last = time.time()
        while True:
            budget = min(settle, hard_timeout - (time.time() - start))
            if budget <= 0:
                break
            r, _, _ = select.select([self.dev.fd], [], [], budget)
            if not r:
                break
            got = False
            for e in self.dev.read():
                if e.type in (ecodes.EV_KEY, ecodes.EV_ABS):
                    events.append(e)
                    got = True
            if got:
                last = time.time()
            if time.time() - last >= settle:
                break
        return events

    @staticmethod
    def key_changes(events):
        """code -> last value seen (1 down, 0 up)."""
        return {e.code: e.value for e in events if e.type == ecodes.EV_KEY}

    @staticmethod
    def abs_changes(events):
        return {e.code: e.value for e in events if e.type == ecodes.EV_ABS}
