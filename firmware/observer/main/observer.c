// Bluepad32 observer: scan/auto-connect BLE HID gamepads and print one machine-readable line per event on the serial
// console, for tester/bp32_hil.py to parse. Also takes a few commands on the console. Derived from the Bluepad32 HIL
// rig's host firmware (leenx-foss/antBot-hil host/), minus its NuS and OTA, which the observer never used.
//
// Output:
//   HIL ready idx=<n> type=<type> vid=<hex> pid=<hex> name="<name>"
//   HIL state idx=<n> btn=<hex> misc=<hex> dpad=<hex> lx=<n> ly=<n> rx=<n> ry=<n> brake=<n> thr=<n> bat=<n>
//             ax=<n> ay=<n> az=<n> gx=<n> gy=<n> gz=<n>    (accel in mm/s^2, gyro in mrad/s, Bluepad32's frame)
//   HIL features proto=<n> caps0=<hex> caps1=<hex> poll=<us> accel=<g> gyro=<dps>   (or "HIL features none")
//   HIL discovered addr=<addr> name="<name>" | HIL connected idx=<n> | HIL disconnected idx=<n>
//   HIL init-complete | HIL version <version>
//   HIL ok <command> | HIL err <reason>
//
// Commands (one per line):
//   help                list the commands
//   version?            firmware version (bp32obs-<bluepad32 sha8>)
//   led <0..4>          player LED (0 = off) on the first ready device
//   rgb <r> <g> <b>     RGB LED, 0..255 each
//   rumble <weak> <strong> <ms>   dual rumble, magnitudes 0..255, for ms milliseconds (0 = stop)
//   features?           print the device's feature response
//   allow <addr>|any    only pair with this Bluetooth address (any = clear the filter)
//
// Pairing filter: with an allowed address set, only that device is accepted. Otherwise devices whose name starts
// with "HILpad" (the rig's gamepads), or that advertise no name at all (which a rig gamepad can do before its scan
// response arrives), are ignored, so the observer only takes the gamepad bp32_hil.py allows. A default address can be
// baked in with -DHIL_ALLOW_ADDR="AA:BB:..".
//
// Device names are printed with '"', '\' and non-printable characters replaced by '?', so a nearby device's name
// can't break a line or inject one. The observer is a BLE central only: it doesn't enable Bluepad32's BLE service.

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include <btstack_stdin.h>
#include <uni.h>

#include <esp_app_desc.h>

#include "sdkconfig.h"

#include "parser/uni_hid_parser_sinput.h"

#ifndef HIL_ALLOW_ADDR
#define HIL_ALLOW_ADDR ""
#endif
#define HIL_IGNORE_NAME_PREFIX "HILpad"
#define HIL_LINE_MAX 256

static char g_allow_addr[18] = HIL_ALLOW_ADDR;
static uni_hid_device_t* g_dev;  // first ready device, for console commands
static char g_line[HIL_LINE_MAX];
static size_t g_line_len;

// Last printed controller state per device slot, so a "HIL state" line is only printed on a change. Cleared on
// connect and disconnect: a reconnected device's first report always prints.
static uni_controller_t g_prev[CONFIG_BLUEPAD32_MAX_DEVICES];
static bool g_prev_valid[CONFIG_BLUEPAD32_MAX_DEVICES];

static void forget_state(uni_hid_device_t* d) {
    int idx = uni_hid_device_get_idx_for_instance(d);
    if (idx >= 0 && idx < CONFIG_BLUEPAD32_MAX_DEVICES)
        g_prev_valid[idx] = false;
}

// A device name, safe inside name="...": '"', '\\' and non-printable characters become '?'.
static const char* safe_name(const char* in, char* out, size_t n) {
    size_t i = 0;
    for (; in && in[i] && i < n - 1; i++) {
        unsigned char ch = (unsigned char)in[i];
        out[i] = (ch < 0x20 || ch > 0x7e || ch == '"' || ch == '\\') ? '?' : (char)ch;
    }
    out[i] = '\0';
    return out;
}

static void hil_init(int argc, const char** argv) {
    ARG_UNUSED(argc);
    ARG_UNUSED(argv);
    logi("HIL host: init\n");
}

static void print_features(uni_hid_device_t* d) {
    uint16_t proto, poll_us = 0, accel_g = 0, gyro_dps = 0;
    uint8_t caps0, caps1;
    if (d && uni_hid_parser_sinput_get_features(d, &proto, &caps0, &caps1)) {
        uni_hid_parser_sinput_get_imu_config(d, &poll_us, &accel_g, &gyro_dps);
        printf("HIL features proto=%u caps0=0x%02x caps1=0x%02x poll=%u accel=%u gyro=%u\n", proto, caps0, caps1,
               poll_us, accel_g, gyro_dps);
    } else
        printf("HIL features none\n");
}

static void print_help(void) {
    static const char* const lines[] = {
        "HIL help commands, one per line:",
        "HIL help   help                        this list",
        "HIL help   version?                    firmware version",
        "HIL help   features?                   the gamepad's SInput features",
        "HIL help   led <0..4>                  player LED (0 = off)",
        "HIL help   rgb <r> <g> <b>             RGB LED",
        "HIL help   rumble <weak> <strong> <ms> dual rumble, 0..255 each, ms 0 = stop",
        "HIL help   allow <addr>|any            only pair with this Bluetooth address",
    };
    for (size_t i = 0; i < sizeof(lines) / sizeof(lines[0]); i++)
        printf("%s\n", lines[i]);
}

static void handle_command(char* line) {
    unsigned a, b, c;
    char addr[32];

    if (strcmp(line, "help") == 0 || strcmp(line, "?") == 0) {
        print_help();
    } else if (strcmp(line, "version?") == 0) {
        printf("HIL version %s\n", esp_app_get_description()->version);
    } else if (strcmp(line, "features?") == 0) {
        print_features(g_dev);
    } else if (sscanf(line, "led %u", &a) == 1 && a <= 4) {
        if (!g_dev || !g_dev->report_parser.set_player_leds) {
            printf("HIL err no device\n");
            return;
        }
        // Bluepad32 player LEDs are a bitmask; player n is bit n-1.
        g_dev->report_parser.set_player_leds(g_dev, a ? (uint8_t)(1u << (a - 1)) : 0);
        printf("HIL ok led %u\n", a);
    } else if (sscanf(line, "rgb %u %u %u", &a, &b, &c) == 3 && a <= 255 && b <= 255 && c <= 255) {
        if (!g_dev || !g_dev->report_parser.set_lightbar_color) {
            printf("HIL err no device\n");
            return;
        }
        g_dev->report_parser.set_lightbar_color(g_dev, (uint8_t)a, (uint8_t)b, (uint8_t)c);
        printf("HIL ok rgb %u %u %u\n", a, b, c);
    } else if (sscanf(line, "rumble %u %u %u", &a, &b, &c) == 3 && a <= 255 && b <= 255 && c <= 65535) {
        if (!g_dev || !g_dev->report_parser.play_dual_rumble) {
            printf("HIL err no device\n");
            return;
        }
        g_dev->report_parser.play_dual_rumble(g_dev, 0, (uint16_t)c, (uint8_t)a, (uint8_t)b);
        printf("HIL ok rumble %u %u %u\n", a, b, c);
    } else if (sscanf(line, "allow %31s", addr) == 1) {
        if (strcasecmp(addr, "any") == 0) {
            g_allow_addr[0] = '\0';
        } else if (strlen(addr) == 17) {
            strcpy(g_allow_addr, addr);
        } else {
            printf("HIL err bad address\n");
            return;
        }
        printf("HIL ok allow %s\n", g_allow_addr[0] ? g_allow_addr : "any");
    } else {
        printf("HIL err unknown command\n");
    }
}

// Called on the BTstack thread for every console character.
static void hil_stdin(char ch) {
    if (ch == '\r' || ch == '\n') {
        if (g_line_len > 0) {
            g_line[g_line_len] = '\0';
            handle_command(g_line);
            g_line_len = 0;
        }
    } else if (g_line_len < HIL_LINE_MAX - 1) {
        g_line[g_line_len++] = ch;
    }
}

static void hil_on_init_complete(void) {
    // Runs on the BT thread, so the "unsafe" calls are fine.
    // Start every run with no stored keys, so pairing is deterministic.
    uni_bt_del_keys_unsafe();
    uni_bt_start_scanning_and_autoconnect_unsafe();
    uni_bt_allow_incoming_connections(true);
    btstack_stdin_setup(hil_stdin);
    printf("HIL init-complete\n");
}

static uni_error_t hil_on_device_discovered(bd_addr_t addr, const char* name, uint16_t cod, uint8_t rssi) {
    ARG_UNUSED(cod);
    ARG_UNUSED(rssi);

    if (g_allow_addr[0]) {
        if (strcasecmp(bd_addr_to_str(addr), g_allow_addr) != 0)
            return UNI_ERROR_IGNORE_DEVICE;
    } else if (!name || !name[0] || strncmp(name, HIL_IGNORE_NAME_PREFIX, strlen(HIL_IGNORE_NAME_PREFIX)) == 0) {
        return UNI_ERROR_IGNORE_DEVICE;
    }
    char safe[64];
    printf("HIL discovered addr=%s name=\"%s\"\n", bd_addr_to_str(addr), safe_name(name, safe, sizeof(safe)));
    return UNI_ERROR_SUCCESS;
}

static void hil_on_device_connected(uni_hid_device_t* d) {
    forget_state(d);
    printf("HIL connected idx=%d\n", uni_hid_device_get_idx_for_instance(d));
}

static void hil_on_device_disconnected(uni_hid_device_t* d) {
    if (d == g_dev)
        g_dev = NULL;
    forget_state(d);
    printf("HIL disconnected idx=%d\n", uni_hid_device_get_idx_for_instance(d));
}

static uni_error_t hil_on_device_ready(uni_hid_device_t* d) {
    if (!g_dev)
        g_dev = d;
    char safe[64];
    printf("HIL ready idx=%d type=%d vid=0x%04x pid=0x%04x name=\"%s\"\n", uni_hid_device_get_idx_for_instance(d),
           (int)d->controller_type, d->vendor_id, d->product_id, safe_name(d->name, safe, sizeof(safe)));
    print_features(d);
    return UNI_ERROR_SUCCESS;
}

static void hil_on_controller_data(uni_hid_device_t* d, uni_controller_t* ctl) {
    int idx = uni_hid_device_get_idx_for_instance(d);
    if (idx >= 0 && idx < CONFIG_BLUEPAD32_MAX_DEVICES) {
        if (g_prev_valid[idx] && memcmp(&g_prev[idx], ctl, sizeof(*ctl)) == 0)
            return;
        g_prev[idx] = *ctl;
        g_prev_valid[idx] = true;
    }

    if (ctl->klass != UNI_CONTROLLER_CLASS_GAMEPAD)
        return;
    const uni_gamepad_t* gp = &ctl->gamepad;
    printf("HIL state idx=%d btn=0x%04x misc=0x%02x dpad=0x%02x lx=%d ly=%d rx=%d ry=%d brake=%d thr=%d bat=%d "
           "ax=%d ay=%d az=%d gx=%d gy=%d gz=%d\n",
           uni_hid_device_get_idx_for_instance(d), gp->buttons, gp->misc_buttons, gp->dpad, (int)gp->axis_x,
           (int)gp->axis_y, (int)gp->axis_rx, (int)gp->axis_ry, (int)gp->brake, (int)gp->throttle, ctl->battery,
           (int)lroundf(gp->accel[0] * 1000.0f), (int)lroundf(gp->accel[1] * 1000.0f),
           (int)lroundf(gp->accel[2] * 1000.0f), (int)lroundf(gp->gyro[0] * 1000.0f),
           (int)lroundf(gp->gyro[1] * 1000.0f), (int)lroundf(gp->gyro[2] * 1000.0f));
}

static const uni_property_t* hil_get_property(uni_property_idx_t idx) {
    ARG_UNUSED(idx);
    return NULL;
}

static void hil_on_oob_event(uni_platform_oob_event_t event, void* data) {
    ARG_UNUSED(data);
    logd("HIL host: oob event 0x%04x\n", event);
}

struct uni_platform* get_my_platform(void) {
    static struct uni_platform plat = {
        .name = "hil-observer",
        .init = hil_init,
        .on_init_complete = hil_on_init_complete,
        .on_device_discovered = hil_on_device_discovered,
        .on_device_connected = hil_on_device_connected,
        .on_device_disconnected = hil_on_device_disconnected,
        .on_device_ready = hil_on_device_ready,
        .on_oob_event = hil_on_oob_event,
        .on_controller_data = hil_on_controller_data,
        .get_property = hil_get_property,
    };
    return &plat;
}
