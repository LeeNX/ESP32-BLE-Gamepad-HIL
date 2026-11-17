/*
 * hil_stats.h -- MCU health for the host's trend log: on-die temperature, CPU and Bluetooth load, heap, stack, reset
 * reason. Shared by hil_runner (Arduino, C++; `STATS?`, `TASKS?`, `TEMP?`) and the Bluepad32 observer (ESP-IDF, C;
 * `stats?`, `tasks?`). Header-only, everything static. Parsed by host/hil/mcustats.py.
 *
 * hil_stats_body() fills the shared part of a STATS line:
 *
 *   up=<ms> reset=<reason> temp=<C|na> cpu0=<%|na> [cpu1=<%|na>] bt=<%|na> heap=<b> heap_min=<b> heap_big=<b>
 *
 * Each caller appends its own fields and finally took_us=. hil_stats_tasks() fills a TASKS line body:
 *
 *   span_ms=<n> <task>:<cpu %>:<least free stack ever, bytes> ...
 *
 * CPU figures are shares of one core over the window since the previous call of the same kind (since boot on the
 * first): per core, 100 - that core's idle task; bt=, the summed share of the tasks the caller names as its Bluetooth
 * stack. From FreeRTOS run-time stats, which need CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS and
 * CONFIG_FREERTOS_USE_TRACE_FACILITY (both on in the Arduino core's prebuilt sdkconfig; the observer sets them in
 * sdkconfig.defaults). The counters are 32-bit microseconds, so a window over ~71 minutes reads "na".
 *
 * Temperature:
 *   ESP32-C3/S3 (SOC_TEMP_SENSOR_SUPPORTED): the IDF driver with a 20-100 C range (calibrated, error < 2 C). Not
 *     Arduino's temperatureRead(): that installs the sensor for 10-50 C, and these chips idle at ~50-53 C, so it read
 *     pinned at the top of the range (a C3 read a flat 52.9 C). Only one handle can exist: nothing may call
 *     temperatureRead() on these chips once this is in use.
 *   Classic ESP32: the undocumented ROM/PHY function temprature_sens_read() [sic], what temperatureRead() wraps.
 *     Uncalibrated, well above ambient, whole degrees Fahrenheit (0.56 C steps; a D0WD-V3 read 53.3 C at boot and
 *     53.9-54.4 C a few minutes later). Reading the SENS registers directly gives the identical value. A trend, not
 *     an absolute reading.
 */
#pragma once

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <esp_heap_caps.h>
#include <esp_system.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <soc/soc_caps.h>

#include "sdkconfig.h"

#if !defined(CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS) || !defined(CONFIG_FREERTOS_USE_TRACE_FACILITY)
#error "hil_stats.h needs CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS and CONFIG_FREERTOS_USE_TRACE_FACILITY"
#endif

// --- temperature --------------------------------------------------------------------------------------------------

#if SOC_TEMP_SENSOR_SUPPORTED && !defined(CONFIG_IDF_TARGET_ESP32)
#include <driver/temperature_sensor.h>
#define HIL_STATS_HAS_TEMP 1
static inline float hil_stats_temp_c(void) {
    static temperature_sensor_handle_t sensor = NULL;
    static bool failed = false;
    if (!sensor && !failed) {
        temperature_sensor_config_t cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(20, 100);
        if (temperature_sensor_install(&cfg, &sensor) != ESP_OK) {
            sensor = NULL;
            failed = true;  // don't retry on every call
        } else if (temperature_sensor_enable(sensor) != ESP_OK) {
            temperature_sensor_uninstall(sensor);
            sensor = NULL;
            failed = true;
        }
    }
    float c = NAN;
    if (!sensor || temperature_sensor_get_celsius(sensor, &c) != ESP_OK) return NAN;
    return c;
}
#elif defined(CONFIG_IDF_TARGET_ESP32)
#define HIL_STATS_HAS_TEMP 1
#ifdef __cplusplus
extern "C" {
#endif
uint8_t temprature_sens_read(void);
#ifdef __cplusplus
}
#endif
static inline float hil_stats_temp_c(void) { return (temprature_sens_read() - 32) / 1.8f; }
#else
#define HIL_STATS_HAS_TEMP 0
static inline float hil_stats_temp_c(void) { return NAN; }
#endif

// --- reset reason -------------------------------------------------------------------------------------------------

static inline const char* hil_stats_reset_name(esp_reset_reason_t r) {
    switch (r) {
        case ESP_RST_POWERON: return "poweron";
        case ESP_RST_EXT: return "ext";
        case ESP_RST_SW: return "sw";
        case ESP_RST_PANIC: return "panic";
        case ESP_RST_INT_WDT: return "int_wdt";
        case ESP_RST_TASK_WDT: return "task_wdt";
        case ESP_RST_WDT: return "wdt";
        case ESP_RST_DEEPSLEEP: return "deepsleep";
        case ESP_RST_BROWNOUT: return "brownout";
        case ESP_RST_SDIO: return "sdio";
        case ESP_RST_USB: return "usb";
        case ESP_RST_JTAG: return "jtag";
        case ESP_RST_EFUSE: return "efuse";
        case ESP_RST_PWR_GLITCH: return "pwr_glitch";
        case ESP_RST_CPU_LOCKUP: return "cpu_lockup";
        default: return "unknown";
    }
}

// --- per-task run time ---------------------------------------------------------------------------------------------

#define HIL_STATS_MAX_TASKS 32

// The previous call's counters, so the next call can report run time since then.
typedef struct {
    UBaseType_t num[HIL_STATS_MAX_TASKS];
    uint32_t run[HIL_STATS_MAX_TASKS];
    int n;
    int64_t prev_us;
} hil_task_window_t;

typedef void (*hil_task_fn)(const TaskStatus_t* t, float pct, void* ctx);

// Calls fn(task, % of one core since the previous call) for every task. Returns the window in microseconds, or -1 if
// it couldn't be measured (more than HIL_STATS_MAX_TASKS tasks, no memory, or a window too long for the counters).
static inline int64_t hil_stats_task_window(hil_task_window_t* w, hil_task_fn fn, void* ctx) {
    UBaseType_t cap = uxTaskGetNumberOfTasks() + 4;
    if (cap > HIL_STATS_MAX_TASKS) cap = HIL_STATS_MAX_TASKS;
    TaskStatus_t* st = (TaskStatus_t*)malloc(cap * sizeof(TaskStatus_t));
    if (!st) return -1;
    configRUN_TIME_COUNTER_TYPE total = 0;
    UBaseType_t got = uxTaskGetSystemState(st, cap, &total);
    int64_t now = esp_timer_get_time();
    int64_t span = now - w->prev_us;
    bool ok = got > 0 && span > 0 && span < 0xFFFFFFFFLL;
    hil_task_window_t next;
    next.n = 0;
    next.prev_us = now;
    for (UBaseType_t i = 0; i < got; i++) {
        uint32_t prev = 0;
        for (int j = 0; j < w->n; j++) {
            if (w->num[j] == st[i].xTaskNumber) {
                prev = w->run[j];
                break;
            }
        }
        uint32_t run = (uint32_t)st[i].ulRunTimeCounter;
        if (ok && fn) fn(&st[i], 100.0f * (float)(run - prev) / (float)span, ctx);
        next.num[next.n] = st[i].xTaskNumber;
        next.run[next.n] = run;
        next.n++;
    }
    free(st);
    *w = next;
    return ok ? span : -1;
}

typedef struct {
    const char* const* names;  // NULL-terminated
    float sum;
    bool seen;
} hil_bt_sum_t;

static inline void hil_stats_bt_add(const TaskStatus_t* t, float pct, void* ctx) {
    hil_bt_sum_t* s = (hil_bt_sum_t*)ctx;
    for (const char* const* n = s->names; *n; n++) {
        if (strcmp(t->pcTaskName, *n) == 0) {
            s->sum += pct;
            s->seen = true;
            return;
        }
    }
}

// --- STATS body ------------------------------------------------------------------------------------------------------

typedef struct {
    hil_task_window_t tasks;
    uint32_t prev_idle[portNUM_PROCESSORS];
    int64_t prev_us;
} hil_stats_state_t;

#define HIL_STATS_APPEND(buf, size, n, ...)                                          \
    do {                                                                             \
        if ((n) >= 0 && (size_t)(n) < (size)) (n) += snprintf((buf) + (n), (size) - (n), __VA_ARGS__); \
    } while (0)

// Writes the shared STATS fields to buf; returns the length written. bt_tasks: NULL-terminated task names that make
// up the Bluetooth stack (summed into bt=).
static inline int hil_stats_body(hil_stats_state_t* s, char* buf, size_t size, const char* const* bt_tasks) {
    int n = 0;
    HIL_STATS_APPEND(buf, size, n, "up=%lu reset=%s", (unsigned long)(esp_timer_get_time() / 1000),
                     hil_stats_reset_name(esp_reset_reason()));
    float t = hil_stats_temp_c();
    if (isnan(t)) {
        HIL_STATS_APPEND(buf, size, n, " temp=na");
    } else {
        HIL_STATS_APPEND(buf, size, n, " temp=%.1f", t);
    }

    int64_t now = esp_timer_get_time();
    int64_t span = now - s->prev_us;
    for (int core = 0; core < portNUM_PROCESSORS; core++) {
        uint32_t idle = (uint32_t)ulTaskGetIdleRunTimeCounterForCore(core);
        if (span > 0 && span < 0xFFFFFFFFLL) {
            float busy = 100.0f - 100.0f * (float)(idle - s->prev_idle[core]) / (float)span;
            HIL_STATS_APPEND(buf, size, n, " cpu%d=%.1f", core, busy < 0 ? 0.0f : busy);
        } else {
            HIL_STATS_APPEND(buf, size, n, " cpu%d=na", core);
        }
        s->prev_idle[core] = idle;
    }
    s->prev_us = now;

    hil_bt_sum_t bt = {bt_tasks, 0.0f, false};
    if (hil_stats_task_window(&s->tasks, hil_stats_bt_add, &bt) >= 0 && bt.seen) {
        HIL_STATS_APPEND(buf, size, n, " bt=%.1f", bt.sum);
    } else {
        HIL_STATS_APPEND(buf, size, n, " bt=na");
    }

    HIL_STATS_APPEND(buf, size, n, " heap=%lu heap_min=%lu heap_big=%u", (unsigned long)esp_get_free_heap_size(),
                     (unsigned long)esp_get_minimum_free_heap_size(),
                     (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_8BIT));
    return n < (int)size ? n : (int)size - 1;
}

// --- TASKS body ------------------------------------------------------------------------------------------------------

typedef struct {
    char* buf;
    size_t size;
    int n;
} hil_tasks_out_t;

static inline void hil_stats_task_item(const TaskStatus_t* t, float pct, void* ctx) {
    hil_tasks_out_t* o = (hil_tasks_out_t*)ctx;
    int start = o->n + 1;  // after the space
    HIL_STATS_APPEND(o->buf, o->size, o->n, " %s:%.1f:%u", t->pcTaskName, pct, (unsigned)t->usStackHighWaterMark);
    // Spaces in a task name ("Tmr Svc") would split the item: make them '_'.
    for (int i = start; i < o->n && (size_t)i < o->size && o->buf[i] != ':'; i++) {
        if (o->buf[i] == ' ') o->buf[i] = '_';
    }
}

// Writes "span_ms=<n> <task>:<cpu>:<stack> ..." to buf. Returns the length, or -1 if the window couldn't be measured
// (buf then holds nothing useful).
static inline int hil_stats_tasks(hil_task_window_t* w, char* buf, size_t size) {
    // The items are written first (the callback runs during the walk), then the span is known: leave room for it.
    const int head = 24;  // "span_ms=" + up to 10 digits, padded below
    if (size <= (size_t)head + 1) return -1;
    hil_tasks_out_t o = {buf + head, size - head, 0};
    o.buf[0] = '\0';
    int64_t span = hil_stats_task_window(w, hil_stats_task_item, &o);
    if (span < 0) return -1;
    if (o.n >= (int)o.size) o.n = (int)o.size - 1;  // truncated: snprintf returned the length it wanted
    int h = snprintf(buf, head, "span_ms=%lu", (unsigned long)(span / 1000));
    memmove(buf + h, buf + head, (size_t)o.n + 1);
    return h + o.n;
}
