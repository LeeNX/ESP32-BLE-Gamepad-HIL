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
 * / PNP ... / RSIZE ... / PEER ... / BURST ... / T ...). See README.md
 * "Serial protocol".
 *
 * Requires ESP32-BLE-Gamepad with getHidReportSize() /
 * getHidReportDescriptorSize() (used by RSIZE?).
 */

#include <Arduino.h>
#include <BleGamepad.h>
#include "hil_profile.h"

// Keep this short: it has to fit in the 31-byte legacy BLE advertising packet
// alongside flags + appearance + the HID service UUID, or NimBLE truncates it
// and the host sees a clipped name it can't match on.
static BleGamepad bleGamepad("HILpad " HIL_BOARD_NAME, HIL_DIS_MANUFACTURER);
static BleGamepadConfiguration bleGamepadConfig;

// Upper bound on BURST iterations -- a tight sendReport() loop past this risks
// overrunning the NimBLE notification queue / tripping the task watchdog.
static const int HIL_BURST_MAX = 2000;

static String line;

static void reply(const char *s) { Serial.println(s); }

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
static const int HIL_MAX_TOKENS = 6;
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

    if (c == "PING") { reply("PONG"); return; }

    if (c == "ID?")
    {
        Serial.printf("ID hil_runner profile=%s board=%s built=%s %s\n",
                      HIL_PROFILE_NAME, HIL_BOARD_NAME, __DATE__, __TIME__);
        return;
    }

    if (c == "CONFIG?")
    {
        char axes[40];
        printAxesCsv(axes, sizeof(axes));
        Serial.printf("CONFIG buttons=%d hats=%d axes=%s special=%s "
                      "axesMin=%d axesMax=%d vid=%04X pid=%04X ver=%04X "
                      "reportId=%d profile=%s\n",
                      HIL_BUTTON_COUNT, HIL_HAT_COUNT, axes,
                      HIL_SPECIALS ? "start,select,menu,home,back,volinc,voldec,volmute" : "none",
                      (int)bleGamepadConfig.getAxesMin(), (int)bleGamepadConfig.getAxesMax(),
                      HIL_VID, HIL_PID, HIL_GUID_VERSION,
                      bleGamepadConfig.getHidReportId(), HIL_PROFILE_NAME);
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
        bleGamepad.sendReport();
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
            bleGamepad.sendReport();
            if (gap) delayMicroseconds(gap);
            if ((i & 0x3F) == 0) yield();
        }
        uint32_t elapsed = micros() - start;
        bleGamepad.release(b);
        bleGamepad.sendReport();
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
        bleGamepad.sendReport();
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
        bleGamepad.sendReport();
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
        bleGamepad.sendReport();
        reply("OK");
        return;
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

    if (c == "RESET")
    {
        bleGamepad.resetButtons();
#if HIL_SPECIALS
        for (int i = 0; i < 8; i++) bleGamepad.releaseSpecialButton(i);
#endif
        for (int i = 0; i < 8; i++) applyAxis(i, 0);
        for (int i = 1; i <= HIL_HAT_COUNT; i++) applyHat(i, 0);
        bleGamepad.sendReport();
        reply("OK");
        return;
    }

    reply("ERR unknown");
}

void setup()
{
    Serial.begin(115200);
    line.reserve(64);
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
}
