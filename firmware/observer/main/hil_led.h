// Status LEDs on the observer board itself (not the gamepad's LEDs -- those are the `led` / `rgb` console commands).
// Same settings as hil_runner: -DHIL_LED_PIN=<gpio> [-DHIL_LED_RGB] and -DHIL_CONN_LED_PIN=<gpio>, set per board by
// builder/build-observers.sh from $HIL_LED_PIN_<BOARD> / [board.*].led_pin (builder/led-pin.sh). With neither set
// every function here is a no-op. See docs/rig-hardware.md "RGB status LED".
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
    HIL_LED_BOOTING,    // off: Bluetooth not up yet
    HIL_LED_SCANNING,   // no gamepad connected
    HIL_LED_CONNECTED,  // a gamepad is connected, not ready yet
    HIL_LED_READY,      // a gamepad is ready (HIL ready printed)
} hil_led_state_t;

void hil_led_init(void);
void hil_led_set_state(hil_led_state_t state);
void hil_led_activity(void);   // a report came in: a white flash, at most one per ~100 ms
void hil_led_error(void);      // an "HIL err" reply: a red flash
void hil_led_command(void);    // a console command arrived: ends a manual override
void hil_led_boot_slot(void);  // BOOT <n> accepted: amber, shown before this returns (the caller restarts)

// `statusled?`: "rgb <gpio>", "mono <gpio>" or "none".
void hil_led_describe(char* buf, size_t n);
// `statusled on|off|<r> <g> <b>`: a manual colour until the next other command. Returns false when there's no LED, or
// a colour was asked of a single-colour one.
bool hil_led_override(uint8_t r, uint8_t g, uint8_t b, bool colour);
