// Observer status LEDs -- see hil_led.h. The colours match hil_runner's (firmware/src/hil_runner.cpp), except the
// idle ones, so a board's role is visible at a glance: hil_runner advertises in dim blue, the observer scans in dim
// purple and shows cyan while a gamepad is connected but not ready.
//
// The Bluepad32 callbacks (BTstack thread) only set the volatile state below; a low-priority task polls it every
// 10 ms and writes the LED when the colour changes. That keeps the RMT write (~30 us) and any GPIO work off the
// Bluetooth thread. The fields are written by one thread and read by the other; a torn read costs at most one 10 ms
// frame of the wrong colour, so there's no lock.
#include <stdbool.h>
#include <stdio.h>

#include <esp_log.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <soc/soc_caps.h>

#include "hil_led.h"

#if defined(HIL_LED_PIN) || defined(HIL_CONN_LED_PIN)
#include <driver/gpio.h>
#define HIL_ANY_LED 1
#endif

// Arduino's RGB_BUILTIN convention: a pin number past the last GPIO means "the RGB LED on GPIO pin - count" (97 =
// GPIO48 on an S3). Honoured here too, so a board set to the white-only `97` fallback for hil_runner still gets an
// RGB status LED on the observer instead of a failed gpio call.
#if defined(HIL_LED_PIN) && (defined(HIL_LED_RGB) || HIL_LED_PIN >= SOC_GPIO_PIN_COUNT)
#define HIL_LED_IS_RGB 1
#if HIL_LED_PIN >= SOC_GPIO_PIN_COUNT
#define HIL_LED_GPIO (HIL_LED_PIN - SOC_GPIO_PIN_COUNT)
#else
#define HIL_LED_GPIO HIL_LED_PIN
#endif
#include <driver/rmt_tx.h>
#elif defined(HIL_LED_PIN)
#define HIL_LED_GPIO HIL_LED_PIN
#endif

static const char* TAG = "hil_led";

typedef struct {
    uint8_t r, g, b;
} hil_rgb_t;

// Dim on purpose, like hil_runner's.
static const hil_rgb_t C_OFF = {0, 0, 0};
static const hil_rgb_t C_SCANNING = {10, 0, 12};   // dim purple
static const hil_rgb_t C_CONNECTED = {0, 14, 14};  // cyan
static const hil_rgb_t C_READY = {0, 20, 0};       // green
static const hil_rgb_t C_ACTIVITY = {24, 24, 24};  // white flash
static const hil_rgb_t C_ERROR = {40, 0, 0};       // red flash
static const hil_rgb_t C_BOOTSLOT = {32, 20, 0};   // amber

#define ACTIVITY_MS 30
#define ACTIVITY_MIN_GAP_MS 100  // reports arrive at 100+ Hz: flash on at most one per window, so it flickers
#define ERROR_MS 200
#define POLL_MS 10

typedef enum { FLASH_NONE, FLASH_ACTIVITY, FLASH_ERROR } flash_t;

static volatile hil_led_state_t g_state = HIL_LED_BOOTING;
static volatile flash_t g_flash = FLASH_NONE;
static volatile TickType_t g_flash_until;
static volatile TickType_t g_activity_at;
static volatile bool g_override;
static volatile hil_rgb_t g_override_rgb;
static volatile bool g_boot_slot;

#if HIL_LED_IS_RGB
static rmt_channel_handle_t g_chan;
static rmt_encoder_handle_t g_enc;

static bool rgb_init(void) {
    rmt_tx_channel_config_t ch = {
        .gpio_num = HIL_LED_GPIO,
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,  // 0.1 us ticks
        .mem_block_symbols = SOC_RMT_MEM_WORDS_PER_CHANNEL,
        .trans_queue_depth = 1,
    };
    // WS2812 bit timing: 0 = 0.4 us high + 0.8 us low, 1 = 0.8 us high + 0.4 us low; bytes go out MSB first.
    rmt_bytes_encoder_config_t enc = {
        .bit0 = {.level0 = 1, .duration0 = 4, .level1 = 0, .duration1 = 8},
        .bit1 = {.level0 = 1, .duration0 = 8, .level1 = 0, .duration1 = 4},
        .flags.msb_first = 1,
    };
    if (rmt_new_tx_channel(&ch, &g_chan) != ESP_OK || rmt_new_bytes_encoder(&enc, &g_enc) != ESP_OK ||
        rmt_enable(g_chan) != ESP_OK) {
        ESP_LOGE(TAG, "RGB LED on GPIO%d: RMT init failed, status LED disabled", HIL_LED_GPIO);
        return false;
    }
    return true;
}

static void rgb_write(hil_rgb_t c) {
    uint8_t grb[3] = {c.g, c.r, c.b};  // WS2812 wire order, like Arduino's rgbLedWrite() default
    rmt_transmit_config_t tx = {.loop_count = 0};
    if (rmt_transmit(g_chan, g_enc, grb, sizeof(grb), &tx) == ESP_OK)
        rmt_tx_wait_all_done(g_chan, pdMS_TO_TICKS(POLL_MS));
    // The >50 us low latch gap comes free: the next write is at least one POLL_MS later.
}
#endif

#if HIL_ANY_LED
static bool same(hil_rgb_t a, hil_rgb_t b) {
    return a.r == b.r && a.g == b.g && a.b == b.b;
}

static hil_rgb_t state_colour(hil_led_state_t s) {
    switch (s) {
        case HIL_LED_SCANNING:
            return C_SCANNING;
        case HIL_LED_CONNECTED:
            return C_CONNECTED;
        case HIL_LED_READY:
            return C_READY;
        default:
            return C_OFF;
    }
}

static void led_task(void* arg) {
    (void)arg;
#if HIL_LED_IS_RGB
    bool rgb_ok = rgb_init();
    hil_rgb_t shown = C_OFF;
    bool shown_valid = false;
#elif defined(HIL_LED_GPIO)
    int mono_shown = -1;
#endif
#if defined(HIL_CONN_LED_PIN)
    int conn_shown = -1;
#endif
    for (;;) {
        TickType_t now = xTaskGetTickCount();
        if (g_flash != FLASH_NONE && (int32_t)(now - g_flash_until) >= 0)
            g_flash = FLASH_NONE;
        flash_t flash = g_flash;

        // Activity LED.
        hil_rgb_t want;
        if (g_boot_slot)
            want = C_BOOTSLOT;
        else if (g_override)
            want = g_override_rgb;
        else if (flash == FLASH_ERROR)
            want = C_ERROR;
        else if (flash == FLASH_ACTIVITY)
            want = C_ACTIVITY;
        else
            want = state_colour(g_state);
#if HIL_LED_IS_RGB
        if (rgb_ok && (!shown_valid || !same(want, shown))) {
            rgb_write(want);
            shown = want;
            shown_valid = true;
        }
#elif defined(HIL_LED_GPIO)
        // A single-colour LED only pulses, like hil_runner's: on for a flash, a slot switch or an override, else off.
        int on = (g_boot_slot || g_override) ? !same(want, C_OFF) : flash != FLASH_NONE;
        if (on != mono_shown) {
            gpio_set_level(HIL_LED_GPIO, on);
            mono_shown = on;
        }
#else
        (void)want;
        (void)same;
#endif

        // Connection LED: steady on while a gamepad is ready.
#if defined(HIL_CONN_LED_PIN)
        int conn = g_state == HIL_LED_READY;
        if (conn != conn_shown) {
            gpio_set_level(HIL_CONN_LED_PIN, conn);
            conn_shown = conn;
        }
#endif
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
    }
}

static inline void gpio_out(int pin) {
    gpio_reset_pin(pin);
    gpio_set_direction(pin, GPIO_MODE_OUTPUT);
    gpio_set_level(pin, 0);
}
#endif

void hil_led_init(void) {
#if HIL_ANY_LED
#if defined(HIL_LED_GPIO) && !HIL_LED_IS_RGB
    gpio_out(HIL_LED_GPIO);
#endif
#if defined(HIL_CONN_LED_PIN)
    gpio_out(HIL_CONN_LED_PIN);
#endif
    // Lowest useful priority: below BTstack's task, so a busy Bluetooth stack always wins.
    xTaskCreate(led_task, "hil_led", 2560, NULL, tskIDLE_PRIORITY + 1, NULL);
#endif
}

void hil_led_set_state(hil_led_state_t state) {
    g_state = state;
}

static void start_flash(flash_t kind, uint32_t ms) {
    g_flash_until = xTaskGetTickCount() + pdMS_TO_TICKS(ms);
    g_flash = kind;
}

void hil_led_activity(void) {
    TickType_t now = xTaskGetTickCount();
    if (g_flash == FLASH_ERROR || (int32_t)(now - g_activity_at) < (int32_t)pdMS_TO_TICKS(ACTIVITY_MIN_GAP_MS))
        return;
    g_activity_at = now;
    start_flash(FLASH_ACTIVITY, ACTIVITY_MS);
}

void hil_led_error(void) {
    start_flash(FLASH_ERROR, ERROR_MS);
}

void hil_led_command(void) {
    g_override = false;
}

void hil_led_boot_slot(void) {
#if defined(HIL_LED_GPIO)
    g_boot_slot = true;
    vTaskDelay(pdMS_TO_TICKS(3 * POLL_MS));  // let led_task show it before the restart
#endif
}

void hil_led_describe(char* buf, size_t n) {
#if HIL_LED_IS_RGB
    snprintf(buf, n, "rgb %d", HIL_LED_GPIO);
#elif defined(HIL_LED_GPIO)
    snprintf(buf, n, "mono %d", HIL_LED_GPIO);
#else
    snprintf(buf, n, "none");
#endif
}

bool hil_led_override(uint8_t r, uint8_t g, uint8_t b, bool colour) {
#if HIL_LED_IS_RGB
    (void)colour;
#elif defined(HIL_LED_GPIO)
    if (colour)
        return false;
#else
    (void)r, (void)g, (void)b, (void)colour;
    return false;
#endif
#if defined(HIL_LED_GPIO)
    g_override_rgb = (hil_rgb_t){r, g, b};
    g_override = true;
    return true;
#endif
}
