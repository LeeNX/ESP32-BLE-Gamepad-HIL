/*
 * hil_runner -- serial-command-driven ESP32-BLE-Gamepad firmware for the
 * hardware-in-the-loop test harness.
 *
 * The host (the Raspberry Pi tester) drives this over USB serial, one command
 * per line, and watches the resulting BLE HID events on /dev/input/eventN.
 * Unlike the TestAll example this does nothing on its own: it waits for BEGIN,
 * then applies exactly the input the host asks for, with autoReport off and an
 * explicit sendReport() per command so every host-side event batch is
 * attributable to one command.
 *
 * Protocol: 115200 8N1, '\n'-terminated ASCII. Every command produces exactly
 * one reply line: OK, ERR <reason>, or a value line (PONG / CONN 0|1 / DIS ...
 * / PNP ... / RSIZE ... / PEER ... / BURST ... / T ... / STATS ...). See README.md
 * "Serial protocol".
 *
 * Requires ESP32-BLE-Gamepad with getHidReportSize() /
 * getHidReportDescriptorSize() (used by RSIZE?).
 */

#include <Arduino.h>
#include <BleGamepad.h>
#include <NimBLEDevice.h>
#include <soc/soc_caps.h>
#include "hil_profile.h"
#include "hil_slots.h"
#include "hil_stats.h"

// HIL_DEVICE_NAME (hil_profile.h): "HILpad <board>" for CI/release, "HILdev
// <board>" for the `local` profile, or a -D override. Keep it <= 18 chars --
// it has to fit the 31-byte legacy BLE advertising packet alongside flags +
// appearance + the HID service UUID or NimBLE drops the UUID then the name.
// hil_runner reports the live value over serial via `NAME?`.
static BleGamepad bleGamepad(HIL_DEVICE_NAME, HIL_DIS_MANUFACTURER);
static BleGamepadConfiguration bleGamepadConfig;

// Upper bound on BURST iterations -- a tight sendReport() loop past this risks
// overrunning the NimBLE notification queue / tripping the task watchdog.
static const int HIL_BURST_MAX = 2000;

static String line;

// Defined with the LED code below; reply() calls it so every ERR reply flashes red on an RGB status LED.
static void ledError();

static void reply(const char *s)
{
    Serial.println(s);
    if (s[0] == 'E' && s[1] == 'R' && s[2] == 'R') ledError();
}

// Input reports sent since boot -- STATS? reports it as a "how busy was BLE" counter. Every sendReport() goes
// through here.
static uint32_t reportsSent = 0;
static void sendRpt()
{
    bleGamepad.sendReport();
    reportsSent++;
}

// MCU health: STATS?, TASKS? and TEMP? (firmware/include/hil_stats.h, shared with the Bluepad32 observer).
// The Bluetooth stack's tasks, summed into bt=: NimBLE's host and the controller.
static const char *const HIL_BT_TASKS[] = {"nimble_host", "btController", nullptr};
static hil_stats_state_t statsState;
static hil_task_window_t tasksWindow;

// STATS <shared fields> conn=<0|1> stack_loop=<b> stack_ble=<b|na> reports=<n> took_us=<us>
// The loop task polls serial without sleeping, so its core reads ~100% busy whatever BLE is doing: bt= is the number
// that tracks BLE load. Takes ~5 ms on the MCU (it walks the task list), so the host samples between tests.
static void printStats()
{
    int64_t t0 = esp_timer_get_time();
    char buf[320];
    int n = snprintf(buf, sizeof buf, "STATS ");
    n += hil_stats_body(&statsState, buf + n, sizeof buf - n, HIL_BT_TASKS);
    TaskHandle_t ble = xTaskGetHandle("nimble_host");
    char bleStack[16] = "na";
    if (ble) snprintf(bleStack, sizeof bleStack, "%u", (unsigned)uxTaskGetStackHighWaterMark(ble));
    n += snprintf(buf + n, sizeof buf - n, " conn=%d stack_loop=%u stack_ble=%s reports=%lu",
                  bleGamepad.isConnected() ? 1 : 0, (unsigned)uxTaskGetStackHighWaterMark(nullptr), bleStack,
                  (unsigned long)reportsSent);
    if (n < (int)sizeof buf)
        snprintf(buf + n, sizeof buf - n, " took_us=%lu", (unsigned long)(esp_timer_get_time() - t0));
    Serial.println(buf);
}

// TASKS span_ms=<n> <task>:<cpu %>:<least free stack> ... -- a one-line "top" since the previous TASKS?.
static void printTasks()
{
    static char buf[HIL_STATS_TASKS_BUF];
    if (hil_stats_tasks(&tasksWindow, buf, sizeof buf) < 0)
    {
        reply("ERR tasks");
        return;
    }
    Serial.printf("TASKS %s\n", buf);
}

// Two opt-in status LEDs -- neither wired on any board by default, both
// compile out to no-ops when their pin macro is undefined. Wiring guide:
// docs/rig-hardware.md. Pin comes in via -D HIL_LED_PIN / -D
// HIL_CONN_LED_PIN, set per board from hil_config.toml or a
// $HIL_LED_PIN_<BOARD> / $HIL_CONN_LED_PIN_<BOARD> env var (README "Rig
// hardware TODO") -- never hardcoded here or in platformio.ini.
//
// Activity LED (HIL_LED_PIN): one brief, non-blocking pulse per command
// handled, plus an explicit LED ON/OFF override for a wiring check with no
// BLE pairing needed. Must stay non-blocking -- this firmware also runs the
// --bench latency/throughput sweep, and a delay() here would skew exactly
// the timing numbers that measures.
//
// RGB variant (-D HIL_LED_RGB, set by builder/build.sh when the led_pin
// value is `RGB<gpio>`): HIL_LED_PIN is a WS2812-style addressable LED
// (the S3 DevKitC-1's onboard one is GPIO48 on v1.0 boards, GPIO38 on
// v1.1), and the one LED shows state *and* activity -- see RGB_* below and
// docs/rig-hardware.md "RGB status LED". rgbLedWrite() blocks for ~30-50us
// on the RMT peripheral, so ledPulse()/ledError() only record what to show;
// the write happens in ledService() from loop(), after the command (and
// its sendReport()) is done, and only when the colour actually changes.
#if defined(HIL_LED_PIN) && defined(HIL_LED_RGB)
struct HilRgb
{
    uint8_t r, g, b;
    bool operator==(const HilRgb &o) const { return r == o.r && g == o.g && b == o.b; }
};
// Kept dim on purpose: a bare WS2812 at full drive is glaring on a desk.
static const HilRgb RGB_OFF = {0, 0, 0};
static const HilRgb RGB_ADVERTISING = {0, 0, 12};  // dim blue: booted, not connected
static const HilRgb RGB_CONNECTED = {0, 20, 0};    // green: BLE-connected (mirrors CONN?)
static const HilRgb RGB_ACTIVITY = {24, 24, 24};   // white flash: a command was handled
static const HilRgb RGB_ERROR = {40, 0, 0};        // red flash: a command replied ERR
static const HilRgb RGB_BOOTSLOT = {32, 20, 0};    // yellow/amber: BOOT <n> slot switch, about to restart
static const uint32_t RGB_ACTIVITY_MS = 30;
static const uint32_t RGB_ERROR_MS = 200;

static HilRgb rgbShown = RGB_OFF;
static bool rgbShownValid = false;
static HilRgb rgbFlash = RGB_OFF;
// A flash is requested while a command runs, but its time only starts when
// ledService() can show it: a long command (BURST with a large gap) would
// otherwise use up the whole 30 ms before the LED is ever written.
static uint32_t rgbFlashPendingMs = 0;  // 0 = nothing requested
static uint32_t rgbFlashUntilMs = 0;    // 0 = no flash showing
static bool rgbFlashIsError = false;
static bool rgbOverride = false;
static HilRgb rgbOverrideColour = RGB_OFF;

static void rgbShow(const HilRgb &c)
{
    if (rgbShownValid && c == rgbShown) return;
    rgbLedWrite(HIL_LED_PIN, c.r, c.g, c.b);
    rgbShown = c;
    rgbShownValid = true;
}

static void rgbRequestFlash(const HilRgb &c, uint32_t ms, bool isError)
{
    rgbFlash = c;
    rgbFlashPendingMs = ms;
    rgbFlashIsError = isError;
}

static void ledSetup() { rgbShow(RGB_OFF); }

// Any command ends a manual LED override (LED ON/OFF/<r g b>): the override
// is for a wiring check from miniterm, not something a test run can leave
// stuck on. The LED command itself re-applies its override after this.
static void ledPulse()
{
    rgbOverride = false;
    // Don't let the next command's white flash cut an error flash short.
    if (rgbFlashIsError && (rgbFlashPendingMs || rgbFlashUntilMs)) return;
    rgbRequestFlash(RGB_ACTIVITY, RGB_ACTIVITY_MS, false);
}

static void ledError() { rgbRequestFlash(RGB_ERROR, RGB_ERROR_MS, true); }

static void ledService()
{
    uint32_t now = millis();
    if (rgbFlashPendingMs)
    {
        rgbFlashUntilMs = (now + rgbFlashPendingMs) | 1;  // | 1: never 0, which means "no flash"
        rgbFlashPendingMs = 0;
    }
    else if (rgbFlashUntilMs && (int32_t)(now - rgbFlashUntilMs) >= 0)
    {
        rgbFlashUntilMs = 0;
        rgbFlashIsError = false;
    }
    if (rgbOverride) rgbShow(rgbOverrideColour);
    else if (rgbFlashUntilMs) rgbShow(rgbFlash);
    else rgbShow(bleGamepad.isConnected() ? RGB_CONNECTED : RGB_ADVERTISING);
}

static void ledSet(uint8_t r, uint8_t g, uint8_t b)
{
    rgbOverride = true;
    rgbOverrideColour = {r, g, b};
    rgbFlashPendingMs = 0;
    rgbFlashUntilMs = 0;
    rgbFlashIsError = false;
}

// Shown synchronously: BOOT restarts straight after, loop() never runs again.
static void ledBootSlot() { rgbShow(RGB_BOOTSLOT); }
#elif defined(HIL_LED_PIN)
static uint32_t ledOffAtMs = 0;

static void ledSetup() { pinMode(HIL_LED_PIN, OUTPUT); digitalWrite(HIL_LED_PIN, LOW); }

static void ledPulse()
{
    digitalWrite(HIL_LED_PIN, HIGH);
    ledOffAtMs = millis() + 30;
}

static void ledService()
{
    if (ledOffAtMs && (int32_t)(millis() - ledOffAtMs) >= 0)
    {
        digitalWrite(HIL_LED_PIN, LOW);
        ledOffAtMs = 0;
    }
}

static void ledError() {}

// Single-colour LED: any non-zero colour is "on".
static void ledSet(uint8_t r, uint8_t g, uint8_t b)
{
    digitalWrite(HIL_LED_PIN, (r | g | b) ? HIGH : LOW);
    ledOffAtMs = 0;
}

static void ledBootSlot() {}
#else
static void ledSetup() {}
static void ledPulse() {}
static void ledService() {}
static void ledError() {}
static void ledBootSlot() {}
#endif

// Connection LED (HIL_CONN_LED_PIN): steady on while bonded+connected over
// BLE, off otherwise -- mirrors what CONN? reports. State-driven only, no
// serial override: unlike the activity LED there's nothing useful to fake,
// so verify wiring by actually pairing rather than a manual command.
#if defined(HIL_CONN_LED_PIN)
static bool connLedOn = false;

static void connLedSetup() { pinMode(HIL_CONN_LED_PIN, OUTPUT); digitalWrite(HIL_CONN_LED_PIN, LOW); }

static void connLedService()
{
    bool connected = bleGamepad.isConnected();
    if (connected == connLedOn) return;
    digitalWrite(HIL_CONN_LED_PIN, connected ? HIGH : LOW);
    connLedOn = connected;
}
#else
static void connLedSetup() {}
static void connLedService() {}
#endif

static int axisIndex(const String &name)
{
    for (int i = 0; i < 8; i++)
        if (name == HIL_AXIS_TOKENS[i]) return i;
    return -1;
}

static void applyAxis(int idx, int16_t v)
{
    switch (idx)
    {
    case 0: bleGamepad.setX(v); break;
    case 1: bleGamepad.setY(v); break;
    case 2: bleGamepad.setZ(v); break;
    case 3: bleGamepad.setRX(v); break;
    case 4: bleGamepad.setRY(v); break;
    case 5: bleGamepad.setRZ(v); break;
    case 6: bleGamepad.setSlider1(v); break;
    case 7: bleGamepad.setSlider2(v); break;
    }
}

static void applyHat(int idx, signed char dir)
{
    switch (idx)
    {
    case 1: bleGamepad.setHat1(dir); break;
    case 2: bleGamepad.setHat2(dir); break;
    case 3: bleGamepad.setHat3(dir); break;
    case 4: bleGamepad.setHat4(dir); break;
    }
}

// Split "PART0 PART1 .. PARTn" -> up to HIL_MAX_TOKENS tokens. Returns count.
static const int HIL_MAX_TOKENS = 8;  // MOTION takes 7
static int tokenize(const String &s, String out[HIL_MAX_TOKENS])
{
    int n = 0, start = 0;
    while (n < HIL_MAX_TOKENS && start < (int)s.length())
    {
        int sp = s.indexOf(' ', start);
        if (sp < 0) sp = s.length();
        out[n++] = s.substring(start, sp);
        start = sp + 1;
    }
    return n;
}

// Parse up to `max` bytes of hex ("AABBCC") into out. Returns count, -1 on bad.
static int hexToBytes(const String &s, uint8_t *out, int max)
{
    int len = s.length();
    if (len % 2) return -1;
    int n = len / 2;
    if (n > max) return -1;
    for (int i = 0; i < n; i++)
    {
        char hi = s[2 * i], lo = s[2 * i + 1];
        int v = 0;
        for (char c : {hi, lo})
        {
            v <<= 4;
            if (c >= '0' && c <= '9') v |= c - '0';
            else if (c >= 'A' && c <= 'F') v |= c - 'A' + 10;
            else if (c >= 'a' && c <= 'f') v |= c - 'a' + 10;
            else return -1;
        }
        out[i] = (uint8_t)v;
    }
    return n;
}

static void printBytesHex(const char *tag, bool recv, const uint8_t *d, int n)
{
    Serial.printf("%s recv=%d ", tag, recv ? 1 : 0);
    for (int i = 0; i < n; i++) Serial.printf("%02X", d[i]);
    Serial.println();
}

// "name,name" of the enabled flags, or "none".
static void namesCsv(char *buf, size_t buflen, const char *const names[], const bool enabled[], int n)
{
    buf[0] = '\0';
    size_t used = 0;
    for (int i = 0; i < n; i++)
    {
        if (!enabled[i]) continue;
        int w = snprintf(buf + used, buflen - used, "%s%s", used ? "," : "", names[i]);
        if (w > 0) used += w;
    }
    if (!used) snprintf(buf, buflen, "none");
}

static const char *const HIL_SPECIAL_TOKENS[8] = {"start", "select", "menu", "home", "back", "volinc", "voldec", "volmute"};
static const bool HIL_SPECIAL_ON[8] = {HIL_SP_START, HIL_SP_SELECT, HIL_SP_MENU, HIL_SP_HOME,
                                       HIL_SP_BACK, HIL_SP_VOLINC, HIL_SP_VOLDEC, HIL_SP_VOLMUTE};
static const char *const HIL_SIM_TOKENS[5] = {"rudder", "throttle", "accelerator", "brake", "steering"};
static const bool HIL_SIM_ON[5] = {HIL_SIM_RUDDER, HIL_SIM_THROTTLE, HIL_SIM_ACCELERATOR, HIL_SIM_BRAKE, HIL_SIM_STEERING};

static void applySim(int idx, int16_t v)
{
    switch (idx)
    {
    case 0: bleGamepad.setRudder(v); break;
    case 1: bleGamepad.setThrottle(v); break;
    case 2: bleGamepad.setAccelerator(v); break;
    case 3: bleGamepad.setBrake(v); break;
    case 4: bleGamepad.setSteering(v); break;
    }
}

static void printAxesCsv(char *buf, size_t buflen)
{
    buf[0] = '\0';
    size_t used = 0;
    for (int i = 0; i < 8; i++)
    {
        if (!hilAxisEnabled(i)) continue;
        int w = snprintf(buf + used, buflen - used, "%s%s",
                         used ? "," : "", HIL_AXIS_TOKENS[i]);
        if (w > 0) used += w;
    }
}

static void handle(const String &cmd)
{
    String t[HIL_MAX_TOKENS];
    int n = tokenize(cmd, t);
    if (n == 0) return;
    const String &c = t[0];
    ledPulse();

    if (c == "PING") { reply("PONG"); return; }

    if (c == "ID?")
    {
        Serial.printf("ID hil_runner profile=%s board=%s built=%s %s\n",
                      HIL_PROFILE_NAME, HIL_BOARD_NAME, __DATE__, __TIME__);
        return;
    }

    if (c == "NAME?")
    {
        // The advertised BLE name (getDeviceName()) -- so the host knows exactly
        // which device to pair without guessing from board/profile.
        Serial.printf("NAME %s\n", bleGamepad.getDeviceName().c_str());
        return;
    }

    if (c == "ADDR?")
    {
        // This board's own BLE address, so an observer that filters by address (Bluepad32's
        // `allow <addr>`) can be pointed at whichever board plays the gamepad in a rotation.
        Serial.printf("ADDR %s\n", NimBLEDevice::getAddress().toString().c_str());
        return;
    }

    if (c == "SLOTS?")
    {
        // OTA app slots (multi-slot layout, docs/multislot-ota.md): which one runs, which boots next, and each
        // slot's image digest, so the host can check them against the multislot bundle's manifest.
        static char buf[HIL_SLOTS_LINE];
        hil_slots_describe(buf, sizeof(buf));
        Serial.printf("SLOTS %s\n", buf);
        return;
    }

    if (c == "BOOT")
    {
        // BOOT <n> [WIPE]: boot OTA slot n next (WIPE: erase NVS -- bonds -- too), reply OK, restart.
        if (n < 2 || n > 3 || (n > 2 && t[2] != "WIPE") || t[1].length() != 1 || !isDigit(t[1][0]))
        {
            reply("ERR args");
            return;
        }
        const char *err = hil_slots_set_boot(t[1].toInt(), n > 2);
        if (err) { Serial.printf("ERR %s\n", err); ledError(); return; }
        ledBootSlot();
        reply("OK");
        Serial.flush();
        delay(20);
        hil_slots_restart();
        return;
    }

    if (c == "CONFIG?")
    {
        char axes[40], specials[64], sims[48];
        printAxesCsv(axes, sizeof(axes));
        namesCsv(specials, sizeof(specials), HIL_SPECIAL_TOKENS, HIL_SPECIAL_ON, 8);
        namesCsv(sims, sizeof(sims), HIL_SIM_TOKENS, HIL_SIM_ON, 5);
        Serial.printf("CONFIG buttons=%d hats=%d axes=%s special=%s sim=%s "
                      "axesMin=%d axesMax=%d vid=%04X pid=%04X ver=%04X "
                      "reportId=%d feat=%d out=%d profile=%s libsha=%s\n",
                      HIL_BUTTON_COUNT, HIL_HAT_COUNT, axes,
                      HIL_SINPUT ? "start,select,home" : specials, sims,
                      (int)bleGamepadConfig.getAxesMin(), (int)bleGamepadConfig.getAxesMax(),
                      bleGamepadConfig.getVid(), bleGamepadConfig.getPid(), bleGamepadConfig.getGuidVersion(),
                      bleGamepadConfig.getHidReportId(),
                      HIL_FEATURE_REPORT_LEN, HIL_OUTPUT_REPORT_LEN, HIL_PROFILE_NAME, HIL_LIB_SHA);
        return;
    }

    if (c == "DIS?")
    {
        Serial.printf("DIS model=%s serial=%s fw=%s hw=%s sw=%s mfr=%s\n",
                      bleGamepadConfig.getModelNumber(),
                      bleGamepadConfig.getSerialNumber(),
                      bleGamepadConfig.getFirmwareRevision(),
                      bleGamepadConfig.getHardwareRevision(),
                      bleGamepadConfig.getSoftwareRevision(),
                      bleGamepad.getDeviceManufacturer().c_str());
        return;
    }

    if (c == "PNP?")
    {
        Serial.printf("PNP vidsrc=1 vid=%04X pid=%04X ver=%04X\n",
                      bleGamepadConfig.getVid(), bleGamepadConfig.getPid(),
                      bleGamepadConfig.getGuidVersion());
        return;
    }

    if (c == "RSIZE?")
    {
        Serial.printf("RSIZE report=%d descriptor=%d\n",
                      bleGamepad.getHidReportSize(),
                      bleGamepad.getHidReportDescriptorSize());
        return;
    }

    if (c == "RMAP?")
    {
        // The HID report descriptor the library generated, as one hex string.
        // Host compares it against RSIZE?, the kernel's copy
        // (/sys/class/hidraw/.../report_descriptor) and a golden file.
        int len = bleGamepad.getHidReportDescriptorSize();
        const uint8_t *d = bleGamepad.getHidReportDescriptor();
        Serial.printf("RMAP %d ", len);
        for (int i = 0; i < len; i++) Serial.printf("%02X", d[i]);
        Serial.println();
        return;
    }

    if (c == "TEMP?")
    {
        // On-die temp sensor, hil_stats_temp_c() (hil_stats.h: 20-100 C range on
        // S3/C3, an uncalibrated ROM reading on the classic ESP32).
        float t = hil_stats_temp_c();
        if (!HIL_STATS_HAS_TEMP || isnan(t)) reply("ERR unsupported");
        else Serial.printf("TEMP %.1f\n", t);
        return;
    }

    if (c == "STATS?")
    {
        printStats();
        return;
    }

    if (c == "TASKS?")
    {
        printTasks();
        return;
    }

    if (c == "LED?")
    {
        // LED? -> LED rgb <gpio> | LED mono <gpio> | LED none: which kind of
        // activity LED this build has, so a test knows whether LED <r> <g> <b> applies.
#if defined(HIL_LED_PIN) && defined(HIL_LED_RGB)
        Serial.printf("LED rgb %d\n", (int)HIL_LED_PIN);
#elif defined(HIL_LED_PIN)
        Serial.printf("LED mono %d\n", (int)HIL_LED_PIN);
#else
        reply("LED none");
#endif
        return;
    }

    if (c == "LED")
    {
        // LED ON|OFF on any LED; LED <r> <g> <b> (0..255 each) on an RGB one.
        // Held until the next non-LED command (see ledPulse()).
#if defined(HIL_LED_PIN)
        if (n == 2 && t[1] == "ON") { ledSet(64, 64, 64); reply("OK"); return; }
        if (n == 2 && t[1] == "OFF") { ledSet(0, 0, 0); reply("OK"); return; }
        if (n == 4)
        {
#if defined(HIL_LED_RGB)
            int v[3];
            for (int i = 0; i < 3; i++)
            {
                // Every character a digit (String::toInt() would take "12junk" as 12), at most 3 of them.
                const String &tok = t[i + 1];
                bool digits = tok.length() >= 1 && tok.length() <= 3;
                for (unsigned k = 0; digits && k < tok.length(); k++) digits = isDigit(tok[k]);
                if (!digits) { reply("ERR args"); return; }
                v[i] = t[i + 1].toInt();
                if (v[i] < 0 || v[i] > 255) { reply("ERR range"); return; }
            }
            ledSet(v[0], v[1], v[2]);
            reply("OK");
#else
            reply("ERR unsupported");
#endif
            return;
        }
        reply("ERR args");
#else
        reply("ERR unsupported");
#endif
        return;
    }

    // begin() runs in setup() (see there for why); BEGIN is now just a
    // readiness handshake the harness can rely on.
    if (c == "BEGIN")
    {
        reply("OK");
        return;
    }

    if (c == "CONN?")
    {
        reply(bleGamepad.isConnected() ? "CONN 1" : "CONN 0");
        return;
    }

    if (c == "BONDS?")
    {
        // Peers this board has a stored bond for. A leftover bond (e.g. from
        // stock "ESP32 BLE Gamepad" firmware, or a previous profile) makes the
        // host show a stale device and can auto-reconnect the wrong way --
        // CLEARBONDS drops them.
        int nb = NimBLEDevice::getNumBonds();
        Serial.printf("BONDS %d", nb);
        for (int i = 0; i < nb; i++)
            Serial.printf(" %s", NimBLEDevice::getBondedAddress(i).toString().c_str());
        Serial.println();
        return;
    }

    if (c == "CLEARBONDS")
    {
        // ble_store_clear() wipes the persistent security store (bonds + CCCDs)
        // in one shot -- more reliable here than iterating deleteBond(), which
        // can no-op if an address doesn't match the stored identity.
        int before = NimBLEDevice::getNumBonds();
        int rc = ble_store_clear();
        int after = NimBLEDevice::getNumBonds();
        Serial.printf("OK cleared=%d remaining=%d rc=%d\n", before - after, after, rc);
        return;
    }

    if (c == "PEERINFO?")
    {
        if (!bleGamepad.isConnected()) { reply("ERR notconnected"); return; }
        NimBLEConnInfo pi = bleGamepad.getPeerInfo();
        // interval in 1.25 ms units, timeout in 10 ms units -- host converts.
        Serial.printf("PEER interval=%u latency=%u timeout=%u mtu=%u\n",
                      (unsigned)pi.getConnInterval(), (unsigned)pi.getConnLatency(),
                      (unsigned)pi.getConnTimeout(), (unsigned)pi.getMTU());
        return;
    }

    if (c == "PRESS" || c == "RELEASE" || c == "TPRESS" || c == "TRELEASE")
    {
        if (n < 2) { reply("ERR args"); return; }
        int b = t[1].toInt();
        if (b < 1 || b > HIL_BUTTON_COUNT) { reply("ERR range"); return; }
        bool down = (c == "PRESS" || c == "TPRESS");
        bool timed = (c == "TPRESS" || c == "TRELEASE");
        if (down) bleGamepad.press(b); else bleGamepad.release(b);
        uint32_t ts = micros();
        sendRpt();
        if (timed) Serial.printf("T %lu\n", (unsigned long)ts);
        else reply("OK");
        return;
    }

    if (c == "BURST")
    {
        // BURST <button> <count> <gap_us> -- toggle <button> <count> times,
        // one sendReport() per toggle, busy-waiting <gap_us> between. gap_us=0
        // means "as fast as the loop runs". Host counts the resulting evdev
        // key transitions to derive an effective report rate + drop count.
        if (n < 4) { reply("ERR args"); return; }
        int b = t[1].toInt();
        long count = t[2].toInt();
        long gap = t[3].toInt();
        if (b < 1 || b > HIL_BUTTON_COUNT) { reply("ERR range"); return; }
        if (count < 1 || count > HIL_BURST_MAX) { reply("ERR count"); return; }
        if (gap < 0 || gap > 1000000) { reply("ERR gap"); return; }
        uint32_t start = micros();
        for (long i = 0; i < count; i++)
        {
            if (i & 1) bleGamepad.release(b); else bleGamepad.press(b);
            sendRpt();
            if (gap) delayMicroseconds(gap);
            if ((i & 0x3F) == 0) yield();
        }
        uint32_t elapsed = micros() - start;
        bleGamepad.release(b);
        sendRpt();
        Serial.printf("BURST OK %ld %lu\n", count, (unsigned long)elapsed);
        return;
    }

    if (c == "SPECIAL")
    {
#if !HIL_SPECIALS
        reply("ERR disabled");
        return;
#endif
        if (n < 3) { reply("ERR args"); return; }
        int b = t[2].toInt();
        if (b < 0 || b > 7) { reply("ERR range"); return; }
        if (t[1] == "PRESS") bleGamepad.pressSpecialButton(b);
        else if (t[1] == "RELEASE") bleGamepad.releaseSpecialButton(b);
        else { reply("ERR subcmd"); return; }
        sendRpt();
        reply("OK");
        return;
    }

    if (c == "AXIS")
    {
        if (n < 3) { reply("ERR args"); return; }
        int idx = axisIndex(t[1]);
        if (idx < 0) { reply("ERR axis"); return; }
        if (!hilAxisEnabled(idx)) { reply("ERR disabled"); return; }
        applyAxis(idx, (int16_t)t[2].toInt());
        sendRpt();
        reply("OK");
        return;
    }

    if (c == "HAT")
    {
        if (n < 3) { reply("ERR args"); return; }
        int idx = t[1].toInt();
        int dir = t[2].toInt();
        if (HIL_HAT_COUNT == 0) { reply("ERR disabled"); return; }
        if (idx < 1 || idx > HIL_HAT_COUNT) { reply("ERR range"); return; }
        if (dir < 0 || dir > 8) { reply("ERR dir"); return; }
        applyHat(idx, (signed char)dir);
        sendRpt();
        reply("OK");
        return;
    }

    if (c == "FEATURE?")
    {
#if !HIL_FEATURE_REPORT_LEN
        reply("ERR disabled");
        return;
#else
        printBytesHex("FEATURE", bleGamepad.isFeatureReceived(),
                      bleGamepad.getFeatureBuffer(), HIL_FEATURE_REPORT_LEN);
        return;
#endif
    }

    if (c == "FEATURE")   // FEATURE SET <hex>
    {
#if !HIL_FEATURE_REPORT_LEN
        reply("ERR disabled");
        return;
#else
        if (n < 3 || t[1] != "SET") { reply("ERR args"); return; }
        uint8_t buf[HIL_FEATURE_REPORT_LEN];
        int got = hexToBytes(t[2], buf, HIL_FEATURE_REPORT_LEN);
        if (got < 0) { reply("ERR hex"); return; }
        bleGamepad.setFeatureBuffer(buf, got);
        reply("OK");
        return;
#endif
    }

    if (c == "OUTPUT?")
    {
#if !HIL_OUTPUT_REPORT_LEN
        reply("ERR disabled");
        return;
#else
        printBytesHex("OUTPUT", bleGamepad.isOutputReceived(),
                      bleGamepad.getOutputBuffer(), HIL_OUTPUT_REPORT_LEN);
        return;
#endif
    }

    if (c == "BATTERY")
    {
        if (n < 2) { reply("ERR args"); return; }
        int lvl = t[1].toInt();
        if (lvl < 0 || lvl > 100) { reply("ERR range"); return; }
        bleGamepad.setBatteryLevel(lvl);
        reply("OK");
        return;
    }

    if (c == "POWERSTATE")
    {
        // POWERSTATE <info> <discharging> <charging> <level> -- raw bytes for
        // setPowerStateAll(); host reads them back from the 0x2A1A Battery
        // Power State characteristic. See BleGamepad.h::setPowerStateAll and
        // the BLE Battery Power State bitfield.
        if (n < 5) { reply("ERR args"); return; }
        long v[4];
        for (int i = 0; i < 4; i++)
        {
            v[i] = t[i + 1].toInt();
            if (v[i] < 0 || v[i] > 255) { reply("ERR range"); return; }
        }
        bleGamepad.setPowerStateAll((uint8_t)v[0], (uint8_t)v[1],
                                    (uint8_t)v[2], (uint8_t)v[3]);
        reply("OK");
        return;
    }

    // SInput-only commands. Report what the host sent (rumble, RGB, player LED)
    // and drive the report fields the generic profiles don't have (IMU, touch).
    if (c == "MOTION" || c == "TOUCH" || c == "RUMBLE?" || c == "RGB?" || c == "PLED?")
    {
#if !HIL_SINPUT
        reply("ERR disabled");
        return;
#else
        if (c == "MOTION")   // MOTION <gx> <gy> <gz> <ax> <ay> <az>, raw int16 counts
        {
            if (n < 7) { reply("ERR args"); return; }
            long v[6];
            for (int i = 0; i < 6; i++)
            {
                v[i] = t[i + 1].toInt();
                if (v[i] < -32768 || v[i] > 32767) { reply("ERR range"); return; }
            }
            bleGamepad.setMotionControls(v[0], v[1], v[2], v[3], v[4], v[5]);
            sendRpt();
            reply("OK");
            return;
        }
        if (c == "TOUCH")    // TOUCH <finger 0|1> <x> <y> <pressure>, pressure 0 = lifted
        {
            if (n < 5) { reply("ERR args"); return; }
            long f = t[1].toInt(), x = t[2].toInt(), y = t[3].toInt(), p = t[4].toInt();
            if (f < 0 || f > 1 || x < -32768 || x > 32767 || y < -32768 || y > 32767 || p < 0 || p > 65535)
            {
                reply("ERR range");
                return;
            }
            bleGamepad.setTouchpad((uint8_t)f, (int16_t)x, (int16_t)y, (uint16_t)p);
            sendRpt();
            reply("OK");
            return;
        }
        // The *Received() getters clear their flag, so recv=1 means "new since the last query".
        if (c == "RUMBLE?")
        {
            bool recv = bleGamepad.isRumbleReceived();
            Serial.printf("RUMBLE recv=%d left=%u right=%u\n", recv ? 1 : 0,
                          bleGamepad.getRumbleLeftAmplitude(), bleGamepad.getRumbleRightAmplitude());
            return;
        }
        if (c == "RGB?")
        {
            bool recv = bleGamepad.isRgbReceived();
            Serial.printf("RGB recv=%d r=%u g=%u b=%u\n", recv ? 1 : 0,
                          bleGamepad.getRgbRed(), bleGamepad.getRgbGreen(), bleGamepad.getRgbBlue());
            return;
        }
        // PLED?
        bool recv = bleGamepad.isPlayerLedReceived();
        Serial.printf("PLED recv=%d %u\n", recv ? 1 : 0, bleGamepad.getPlayerLedIndex());
        return;
#endif
    }

    if (c == "SIM")   // SIM <rudder|throttle|accelerator|brake|steering> <int16>
    {
        if (n < 3) { reply("ERR args"); return; }
        int idx = -1;
        for (int i = 0; i < 5; i++)
            if (t[1].equalsIgnoreCase(HIL_SIM_TOKENS[i])) idx = i;
        if (idx < 0) { reply("ERR control"); return; }
        if (!HIL_SIM_ON[idx]) { reply("ERR disabled"); return; }
        long v = t[2].toInt();
        if (v < -32768 || v > 32767) { reply("ERR range"); return; }
        applySim(idx, (int16_t)v);
        sendRpt();
        reply("OK");
        return;
    }

    if (c == "RESET")
    {
        bleGamepad.resetButtons();
#if HIL_SPECIALS
        for (int i = 0; i < 8; i++) bleGamepad.releaseSpecialButton(i);
#endif
        for (int i = 0; i < 8; i++) applyAxis(i, 0);
        for (int i = 1; i <= HIL_HAT_COUNT; i++) applyHat(i, 0);
        for (int i = 0; i < 5; i++)
            if (HIL_SIM_ON[i]) applySim(i, 0);
#if HIL_SINPUT
        bleGamepad.setMotionControls(0, 0, 0, 0, 0, 0);
        bleGamepad.setTouchpad(0, 0, 0, 0);
        bleGamepad.setTouchpad(1, 0, 0, 0);
#endif
        sendRpt();
        reply("OK");
        return;
    }

    reply("ERR unknown");
}

void setup()
{
    Serial.begin(115200);
    line.reserve(64);
    ledSetup();
    connLedSetup();
    hilApplyProfile(bleGamepadConfig);
    // begin() is called here, from setup(), exactly like the TestAll example.
    // Calling it later from loop() in response to a serial command reliably
    // wedged the NimBLE server task on the classic ESP32 (advertising never
    // started, board reset-looped) -- so the firmware always advertises once
    // booted, and BEGIN is just a handshake. Both boards carry distinct names
    // ("HILpad esp32dev" / "HILpad esp32c3") so the harness still bonds the
    // right one.
    bleGamepad.begin(&bleGamepadConfig);
    Serial.println("HIL hil_runner ready profile=" HIL_PROFILE_NAME " board=" HIL_BOARD_NAME);
}

void loop()
{
    while (Serial.available())
    {
        char ch = (char)Serial.read();
        if (ch == '\r') continue;
        if (ch == '\n')
        {
            line.trim();
            if (line.length()) handle(line);
            line = "";
        }
        else if (line.length() < 120)
        {
            line += ch;
        }
    }
    ledService();
    connLedService();
}
