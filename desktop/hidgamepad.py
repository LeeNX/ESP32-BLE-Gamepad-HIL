"""Read the DUT's HID input reports straight off the bonded device (hidapi).

This is the raw-report path: it decodes the bytes the firmware sends over BLE
against the known per-profile report layout. It proves the firmware + HID
descriptor + BLE transport, end to end -- it does NOT test how the host OS's
input stack interprets the device (button -> key code, axis -> control). That
needs a native backend (IOKit HID / Windows Raw Input) and is a later step.

Report layout (from firmware/include/hil_profile.h + the golden descriptors):

    byte 0        report ID (RSIZE? reportId, = 3 for every current profile)
    byte 1..      buttons, one bit each, LSB = button 1, little-endian
    then          hat nibble (if HIL_HAT_COUNT), then each axis as int16 LE

Only the button field is decoded here for now; axes/hats join when their tests
land.
"""

import time

BUTTON_BYTE = 1  # first byte after the report ID


def button_bytes(n_buttons):
    return (n_buttons + 7) // 8


def pressed_buttons(report, n_buttons):
    """Set of 1-based button numbers currently down in one input report."""
    if len(report) < BUTTON_BYTE + button_bytes(n_buttons):
        return set()
    field = int.from_bytes(report[BUTTON_BYTE : BUTTON_BYTE + button_bytes(n_buttons)], "little")
    return {i + 1 for i in range(n_buttons) if field & (1 << i)}


def drain(dev):
    """Discard any buffered reports."""
    while dev.read(64):
        pass


def read_after(dev, settle=0.15, window=0.25):
    """Wait `settle` for the report to arrive, then collect what's queued over
    `window`. Returns the list of reports (bytes), newest last."""
    time.sleep(settle)
    out = []
    end = time.time() + window
    while time.time() < end:
        r = dev.read(64)
        if r:
            out.append(bytes(r))
        else:
            time.sleep(0.005)
    return out
