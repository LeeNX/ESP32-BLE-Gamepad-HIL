/*
 * hil_runner -- serial-command-driven ESP32-BLE-Gamepad firmware for the
 * hardware-in-the-loop test harness.
 *
 * The host (cylon) drives this over USB serial, one command per line, and
 * watches the resulting BLE HID events on /dev/input/eventN. Unlike the
 * TestAll example this does nothing on its own: it waits for BEGIN, then
 * applies exactly the input the host asks for, with autoReport off and an
 * explicit sendReport() per command so every host-side event batch is
 * attributable to one command.
 *
 * Protocol: 115200 8N1, '\n'-terminated ASCII. Every command produces exactly
 * one reply line: OK, ERR <reason>, or a value line (PONG / CONN 0|1 / ...).
 * See README.md "Serial protocol".
 */

#include <Arduino.h>
#include <BleGamepad.h>
#include "hil_profile.h"

// delayAdvertising=false (default): begin() starts advertising straight away.
// begin() itself is the gate -- nothing runs before the host sends BEGIN.
static BleGamepad bleGamepad("ESP32 BLE Gamepad HIL " HIL_BOARD_NAME);
static BleGamepadConfiguration bleGamepadConfig;
static bool started = false;

static String line;

static void reply(const char *s) { Serial.println(s); }

static int axisIndex(const String &name)
{
    if (name == "x") return 0;
    if (name == "y") return 1;
    if (name == "z") return 2;
    if (name == "rx") return 3;
    if (name == "ry") return 4;
    if (name == "rz") return 5;
    if (name == "s1") return 6;
    if (name == "s2") return 7;
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

// Split "PART0 PART1 PART2" -> up to 3 tokens. Returns token count.
static int tokenize(const String &s, String out[3])
{
    int n = 0, start = 0;
    while (n < 3 && start < (int)s.length())
    {
        int sp = s.indexOf(' ', start);
        if (sp < 0) sp = s.length();
        out[n++] = s.substring(start, sp);
        start = sp + 1;
    }
    return n;
}

static void handle(const String &cmd)
{
    String t[3];
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
        Serial.printf("CONFIG buttons=%d hats=%d axes=x,y,z,rx,ry,rz,s1,s2 "
                      "special=start,select,menu,home,back,volinc,voldec,volmute "
                      "axesMin=%d axesMax=%d vid=%04X pid=%04X reportId=%d profile=%s\n",
                      HIL_BUTTON_COUNT, HIL_HAT_COUNT,
                      (int)bleGamepadConfig.getAxesMin(), (int)bleGamepadConfig.getAxesMax(),
                      HIL_VID, HIL_PID, bleGamepadConfig.getHidReportId(), HIL_PROFILE_NAME);
        return;
    }

    if (c == "BEGIN")
    {
        if (!started)
        {
            bleGamepad.begin(&bleGamepadConfig);
            started = true;
        }
        reply("OK");
        return;
    }

    if (c == "CONN?")
    {
        reply(started && bleGamepad.isConnected() ? "CONN 1" : "CONN 0");
        return;
    }

    // Everything past here needs BEGIN first.
    if (!started) { reply("ERR not-begun"); return; }

    if (c == "PRESS" || c == "RELEASE")
    {
        if (n < 2) { reply("ERR args"); return; }
        int b = t[1].toInt();
        if (b < 1 || b > HIL_BUTTON_COUNT) { reply("ERR range"); return; }
        if (c == "PRESS") bleGamepad.press(b); else bleGamepad.release(b);
        bleGamepad.sendReport();
        reply("OK");
        return;
    }

    if (c == "SPECIAL")
    {
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

    if (c == "RESET")
    {
        bleGamepad.resetButtons();
        for (int i = 0; i < 8; i++) bleGamepad.releaseSpecialButton(i);
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
    hilApplyProfile(bleGamepadConfig); // populate config now so CONFIG? is accurate pre-BEGIN
    // Announce readiness; host waits for this or just polls PING.
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
