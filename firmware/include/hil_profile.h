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
 * own smaller-button-count profile. `RSIZE?` reports the descriptor size the
 * library computed (needs ESP32-BLE-Gamepad's getHidReportDescriptorSize());
 * test_connection.py::test_descriptor_within_buffer fails if it nears 150.
 *
 * The set of profiles is also deliberately spread across HID input-report
 * sizes (minimal ~2 bytes .. maxbtn 16 bytes of buttons) so the latency
 * benchmark (host/hil/bench.py) can plot latency / throughput vs report size.
 *
 * Four CI profiles. Three run on every push/PR (default/specials/maxbtn), the
 * full four plus --bench run on the weekly schedule + at release. Each carries
 * more than one concern so the flash/pair count per matrix run stays low:
 *   default  -- what most people run: 64 btn / 4 hat / 8 unsigned axes, mirrors
 *               TestAll.ino. The known-good baseline.
 *   specials -- the fragile, least-exercised surface, in one flash: 8 special
 *               (consumer/desktop) usages, signed axes (min -32767, so the
 *               negative-rail check rides here), AND the Output + Feature
 *               reports (separate HID report types -- host exercises them over
 *               hidraw). Trimmed to X/Y axes, no hat, to keep the descriptor
 *               clear of the 150-byte buffer (~122 B; it's the biggest one).
 *   maxbtn   -- the largest layout the HID transport currently supports: the
 *               library's 128-button ceiling. Also the bench high-end anchor.
 *   minimal  -- 2 btn / X-Y, smallest input report -- the bench low-end anchor.
 *               Nothing else depends on it, so it's the one profile left off
 *               push/PR (weekly + release only).
 * `local` is a fifth, ad-hoc developer profile -- never built by CI or a release.
 *
 * Select with a build flag: -D HIL_PROFILE=HIL_PROFILE_SPECIALS
 * (platformio.ini wires one env per profile x board).
 */

#define HIL_PROFILE_DEFAULT 1
#define HIL_PROFILE_SPECIALS 2
#define HIL_PROFILE_MINIMAL 3
#define HIL_PROFILE_MAXBTN 4
#define HIL_PROFILE_LOCAL 5

#ifndef HIL_PROFILE
#define HIL_PROFILE HIL_PROFILE_DEFAULT
#endif

#ifndef HIL_BOARD_NAME
#define HIL_BOARD_NAME "esp32"
#endif

// The advertised BLE name. hil_runner also reports it over serial (`NAME?`).
// Keep it <= 18 chars: it shares the 31-byte legacy advertising packet with
// flags + appearance + the HID service UUID, and NimBLE drops the service UUID
// (then the name itself) once it overruns -- see README "How pairing works".
//
//  - CI / release builds:  "HILpad <board>"  (the reference rig)
//  - `local` profile:       "HILdev <board>"  -- distinct, so a developer's
//                           board doesn't clash with the rig in a shared BLE
//                           space. Override with -D HIL_DEVICE_NAME=... (the
//                           builder's --name does this) to disambiguate two
//                           developers.
#ifndef HIL_DEVICE_NAME
#if HIL_PROFILE == HIL_PROFILE_LOCAL
#define HIL_DEVICE_NAME "HILdev " HIL_BOARD_NAME
#else
#define HIL_DEVICE_NAME "HILpad " HIL_BOARD_NAME
#endif
#endif

// Stable, deliberately not the library default (0xE502/0xBBAB) nor the SInput
// pair (0x2E8A/0x10C6) -- keeps the HIL device distinct in bond lists and udev.
#define HIL_VID 0x1D34
#define HIL_PID 0x8010
#define HIL_GUID_VERSION 0x0110
#define HIL_CONTROLLER_TYPE CONTROLLER_TYPE_GAMEPAD

// Deterministic Device Information Service values so the host can assert the
// GATT read matches what the firmware configured (test_device_info.py). The
// firmware also echoes these via `DIS?`.
#define HIL_DIS_MODEL "HIL-MODEL"
#define HIL_DIS_SERIAL "HIL-SN-0001"
#define HIL_DIS_HW "HIL-HW-1"
#define HIL_DIS_SW "HIL-SW-1"
#define HIL_DIS_MANUFACTURER "LeeNX-HIL"

// Per-profile axis selection. Defaults to all eight; a profile may override the
// individual HIL_AX_* flags before including the defaults below.
#if HIL_PROFILE == HIL_PROFILE_DEFAULT
#define HIL_PROFILE_NAME "default"
#define HIL_BUTTON_COUNT 64
#define HIL_HAT_COUNT 4
#define HIL_AXES_MIN 0x0000
#define HIL_AXES_MAX 0x7FFF
#define HIL_SPECIALS 0
#elif HIL_PROFILE == HIL_PROFILE_SPECIALS
// The fragile / least-exercised surface, folded into one flash: 8 special
// (consumer/desktop) usages, signed axes (min -32767 -- test_ranges' negative-
// rail check rides here), AND Output + Feature reports (separate HID report
// types the host drives over hidraw: test_feature_report / test_output_report).
// Trimmed to X/Y axes and no hat -- special buttons + O/F reports push the
// descriptor toward the fixed tempHidReportDescriptor[150] buffer, and the
// full 8-axis / 4-hat ground truth is already covered by `default`.
#define HIL_PROFILE_NAME "specials"
#define HIL_BUTTON_COUNT 16
#define HIL_HAT_COUNT 0
#define HIL_AXES_MIN ((int16_t)0x8001) // -32767
#define HIL_AXES_MAX 0x7FFF
#define HIL_SPECIALS 1
#define HIL_AX_X 1
#define HIL_AX_Y 1
#define HIL_AX_Z 0
#define HIL_AX_RX 0
#define HIL_AX_RY 0
#define HIL_AX_RZ 0
#define HIL_AX_S1 0
#define HIL_AX_S2 0
#define HIL_OUTPUT_REPORT_LEN 16
#define HIL_FEATURE_REPORT_LEN 16
#elif HIL_PROFILE == HIL_PROFILE_MINIMAL
// Smallest input report worth calling a gamepad: 2 buttons, X + Y, nothing
// else. Anchors the low end of the latency-vs-report-size curve. Nothing else
// depends on it, so it's the one CI profile left off push/PR (weekly + release
// only).
#define HIL_PROFILE_NAME "minimal"
#define HIL_BUTTON_COUNT 2
#define HIL_HAT_COUNT 0
#define HIL_AXES_MIN 0x0000
#define HIL_AXES_MAX 0x7FFF
#define HIL_SPECIALS 0
#define HIL_AX_X 1
#define HIL_AX_Y 1
#define HIL_AX_Z 0
#define HIL_AX_RX 0
#define HIL_AX_RY 0
#define HIL_AX_RZ 0
#define HIL_AX_S1 0
#define HIL_AX_S2 0
#elif HIL_PROFILE == HIL_PROFILE_MAXBTN
// The library's hard ceiling: 128 buttons (_buttons[16]). No hats/axes so the
// generated descriptor stays well inside tempHidReportDescriptor[150].
#define HIL_PROFILE_NAME "maxbtn"
#define HIL_BUTTON_COUNT 128
#define HIL_HAT_COUNT 0
#define HIL_AXES_MIN 0x0000
#define HIL_AXES_MAX 0x7FFF
#define HIL_SPECIALS 0
#define HIL_AX_X 0
#define HIL_AX_Y 0
#define HIL_AX_Z 0
#define HIL_AX_RX 0
#define HIL_AX_RY 0
#define HIL_AX_RZ 0
#define HIL_AX_S1 0
#define HIL_AX_S2 0
#elif HIL_PROFILE == HIL_PROFILE_LOCAL
// Ad-hoc profile for local developer smoke-testing -- deliberately NOT in the
// CI build matrix (hil_config.toml [builder] profiles) or any release. A small
// classic-gamepad layout: 4 buttons, 1 hat, left stick (X/Y). It advertises
// under a distinct BLE name (see HIL_DEVICE_NAME below -- "HILdev <board>" by
// default) so a developer's board never clashes with the reference rig's
// "HILpad <board>" gamepads in a shared BLE space.
#define HIL_PROFILE_NAME "local"
#define HIL_BUTTON_COUNT 4
#define HIL_HAT_COUNT 1
#define HIL_AXES_MIN 0x0000
#define HIL_AXES_MAX 0x7FFF
#define HIL_SPECIALS 0
#define HIL_AX_X 1
#define HIL_AX_Y 1
#define HIL_AX_Z 0
#define HIL_AX_RX 0
#define HIL_AX_RY 0
#define HIL_AX_RZ 0
#define HIL_AX_S1 0
#define HIL_AX_S2 0
#else
#error "unknown HIL_PROFILE"
#endif

#ifndef HIL_OUTPUT_REPORT_LEN
#define HIL_OUTPUT_REPORT_LEN 0
#endif
#ifndef HIL_FEATURE_REPORT_LEN
#define HIL_FEATURE_REPORT_LEN 0
#endif

#ifndef HIL_AX_X
#define HIL_AX_X 1
#endif
#ifndef HIL_AX_Y
#define HIL_AX_Y 1
#endif
#ifndef HIL_AX_Z
#define HIL_AX_Z 1
#endif
#ifndef HIL_AX_RX
#define HIL_AX_RX 1
#endif
#ifndef HIL_AX_RY
#define HIL_AX_RY 1
#endif
#ifndef HIL_AX_RZ
#define HIL_AX_RZ 1
#endif
#ifndef HIL_AX_S1
#define HIL_AX_S1 1
#endif
#ifndef HIL_AX_S2
#define HIL_AX_S2 1
#endif

// Axis tokens in the order applyAxis() indexes them, and which are enabled for
// this profile. The host reads the enabled set from CONFIG? `axes=...`, which
// hil_runner assembles at runtime from these.
static const char *const HIL_AXIS_TOKENS[8] = {
    "x", "y", "z", "rx", "ry", "rz", "s1", "s2"};

static inline bool hilAxisEnabled(int idx)
{
    static const bool en[8] = {
        HIL_AX_X, HIL_AX_Y, HIL_AX_Z, HIL_AX_RX,
        HIL_AX_RY, HIL_AX_RZ, HIL_AX_S1, HIL_AX_S2};
    return idx >= 0 && idx < 8 && en[idx];
}

static inline void hilApplyProfile(BleGamepadConfiguration &cfg)
{
    cfg.setAutoReport(false);
    cfg.setControllerType(HIL_CONTROLLER_TYPE);
    cfg.setButtonCount(HIL_BUTTON_COUNT);
    cfg.setHatSwitchCount(HIL_HAT_COUNT);
    cfg.setWhichAxes(HIL_AX_X, HIL_AX_Y, HIL_AX_Z, HIL_AX_RX,
                     HIL_AX_RY, HIL_AX_RZ, HIL_AX_S1, HIL_AX_S2);
#if HIL_SPECIALS
    cfg.setWhichSpecialButtons(true, true, true, true, true, true, true, true);
#else
    cfg.setWhichSpecialButtons(false, false, false, false, false, false, false, false);
#endif
    cfg.setWhichSimulationControls(false, false, false, false, false);
#if HIL_OUTPUT_REPORT_LEN
    cfg.setEnableOutputReport(true);
    cfg.setOutputReportLength(HIL_OUTPUT_REPORT_LEN);
#endif
#if HIL_FEATURE_REPORT_LEN
    cfg.setEnableFeatureReport(true);
    cfg.setFeatureReportLength(HIL_FEATURE_REPORT_LEN);
#endif
    cfg.setAxesMin(HIL_AXES_MIN);
    cfg.setAxesMax(HIL_AXES_MAX);
    cfg.setVid(HIL_VID);
    cfg.setPid(HIL_PID);
    cfg.setGuidVersion(HIL_GUID_VERSION);
    cfg.setModelNumber(HIL_DIS_MODEL);
    cfg.setSerialNumber(HIL_DIS_SERIAL);
    cfg.setHardwareRevision(HIL_DIS_HW);
    cfg.setSoftwareRevision(HIL_DIS_SW);
    // Firmware revision carries the profile so a stale flash is obvious in the
    // GATT read as well as in `ID?`.
    cfg.setFirmwareRevision("HIL-FW-" HIL_PROFILE_NAME);
}
