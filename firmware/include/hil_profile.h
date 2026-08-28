#pragma once
#include <BleGamepadConfiguration.h>

/*
 * Compile-time layout profile for the HIL runner firmware.
 *
 * Each profile produces a distinct HID report descriptor. A host caches the
 * descriptor at bond time, so switching profiles on the same board means the
 * host bond is stale -- the harness detects this (descriptor fingerprint
 * changes) and re-pairs. See README "Switching profiles".
 *
 * Keep every profile's descriptor within BleGamepad's fixed
 * tempHidReportDescriptor[150] buffer -- it has no bounds check and begin()
 * will corrupt the stack / crash if the descriptor overruns it. That's why
 * `default` mirrors TestAll.ino (known good) and special buttons live in their
 * own smaller-button-count profile.
 *
 * Select with a build flag: -D HIL_PROFILE=HIL_PROFILE_SPECIALS
 * (platformio.ini wires one env per profile x board).
 */

#define HIL_PROFILE_DEFAULT 1
#define HIL_PROFILE_SIGNED_AXES 2
#define HIL_PROFILE_SPECIALS 3

#ifndef HIL_PROFILE
#define HIL_PROFILE HIL_PROFILE_DEFAULT
#endif

#ifndef HIL_BOARD_NAME
#define HIL_BOARD_NAME "esp32"
#endif

// Stable, deliberately not the library default (0xE502/0xBBAB) nor the SInput
// pair (0x2E8A/0x10C6) -- keeps the HIL device distinct in bond lists and udev.
#define HIL_VID 0x1D34
#define HIL_PID 0x8010
#define HIL_CONTROLLER_TYPE CONTROLLER_TYPE_GAMEPAD

#if HIL_PROFILE == HIL_PROFILE_DEFAULT
#define HIL_PROFILE_NAME "default"
#define HIL_BUTTON_COUNT 64
#define HIL_HAT_COUNT 4
#define HIL_AXES_MIN 0x0000
#define HIL_AXES_MAX 0x7FFF
#define HIL_SPECIALS 0
#elif HIL_PROFILE == HIL_PROFILE_SIGNED_AXES
#define HIL_PROFILE_NAME "signed-axes"
#define HIL_BUTTON_COUNT 64
#define HIL_HAT_COUNT 4
#define HIL_AXES_MIN ((int16_t)0x8001) // -32767
#define HIL_AXES_MAX 0x7FFF
#define HIL_SPECIALS 0
#elif HIL_PROFILE == HIL_PROFILE_SPECIALS
#define HIL_PROFILE_NAME "specials"
#define HIL_BUTTON_COUNT 16
#define HIL_HAT_COUNT 1
#define HIL_AXES_MIN 0x0000
#define HIL_AXES_MAX 0x7FFF
#define HIL_SPECIALS 1
#else
#error "unknown HIL_PROFILE"
#endif

static inline void hilApplyProfile(BleGamepadConfiguration &cfg)
{
    cfg.setAutoReport(false);
    cfg.setControllerType(HIL_CONTROLLER_TYPE);
    cfg.setButtonCount(HIL_BUTTON_COUNT);
    cfg.setHatSwitchCount(HIL_HAT_COUNT);
    cfg.setWhichAxes(true, true, true, true, true, true, true, true);
#if HIL_SPECIALS
    cfg.setWhichSpecialButtons(true, true, true, true, true, true, true, true);
#else
    cfg.setWhichSpecialButtons(false, false, false, false, false, false, false, false);
#endif
    cfg.setWhichSimulationControls(false, false, false, false, false);
    cfg.setAxesMin(HIL_AXES_MIN);
    cfg.setAxesMax(HIL_AXES_MAX);
    cfg.setVid(HIL_VID);
    cfg.setPid(HIL_PID);
}
