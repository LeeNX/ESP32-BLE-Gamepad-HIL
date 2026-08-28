#pragma once
#include <BleGamepadConfiguration.h>

/*
 * Compile-time layout profile for the HIL runner firmware.
 *
 * Each profile produces a distinct HID report descriptor. A host caches the
 * descriptor at bond time, so switching profiles on the same board means the
 * host bond is stale -- the harness detects this (descriptor fingerprint
 * changes) and re-pairs, but see README "Switching profiles" if doing it by
 * hand.
 *
 * Select with a build flag: -D HIL_PROFILE=HIL_PROFILE_SIGNED_AXES
 * (platformio.ini wires one env per profile x board).
 */

#define HIL_PROFILE_DEFAULT 1
#define HIL_PROFILE_SIGNED_AXES 2

#ifndef HIL_PROFILE
#define HIL_PROFILE HIL_PROFILE_DEFAULT
#endif

#ifndef HIL_BOARD_NAME
#define HIL_BOARD_NAME "esp32"
#endif

#if HIL_PROFILE == HIL_PROFILE_DEFAULT
#define HIL_PROFILE_NAME "default"
#define HIL_AXES_MIN 0x0000
#define HIL_AXES_MAX 0x7FFF
#elif HIL_PROFILE == HIL_PROFILE_SIGNED_AXES
#define HIL_PROFILE_NAME "signed-axes"
#define HIL_AXES_MIN ((int16_t)0x8001) // -32767
#define HIL_AXES_MAX 0x7FFF
#else
#error "unknown HIL_PROFILE"
#endif

// Shared across the current profiles. buttonCount is a multiple of 8 and the
// report carries axes + hats, so sendReport()'s fixed 16-byte button memcpy
// (BleGamepad.cpp) stays in bounds.
#define HIL_CONTROLLER_TYPE CONTROLLER_TYPE_GAMEPAD
#define HIL_BUTTON_COUNT 64
#define HIL_HAT_COUNT 4

// Stable, deliberately not the library default (0xE502/0xBBAB) nor the SInput
// pair (0x2E8A/0x10C6) -- keeps the HIL device distinct in bond lists and udev.
#define HIL_VID 0x1D34
#define HIL_PID 0x8010

static inline void hilApplyProfile(BleGamepadConfiguration &cfg)
{
    cfg.setAutoReport(false);
    cfg.setControllerType(HIL_CONTROLLER_TYPE);
    cfg.setButtonCount(HIL_BUTTON_COUNT);
    cfg.setHatSwitchCount(HIL_HAT_COUNT);
    cfg.setWhichAxes(true, true, true, true, true, true, true, true);
    cfg.setWhichSpecialButtons(true, true, true, true, true, true, true, true);
    cfg.setWhichSimulationControls(false, false, false, false, false);
    cfg.setAxesMin(HIL_AXES_MIN);
    cfg.setAxesMax(HIL_AXES_MAX);
    cfg.setVid(HIL_VID);
    cfg.setPid(HIL_PID);
}
