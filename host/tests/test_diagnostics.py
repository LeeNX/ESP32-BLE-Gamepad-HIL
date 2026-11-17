"""On-die temperature (`TEMP?`), MCU health (`STATS?`, `TASKS?`) and the activity/status LED (`LED ON|OFF`).
See README "Serial protocol" and "Rig hardware TODO".

`TEMP?` works on all 3 boards: the IDF sensor driver on the esp32-c3/-s3 (20-100 C range), an undocumented
ROM function on the classic esp32. The classic esp32's reading is uncalibrated, runs well above ambient and
moves in 0.56 C steps, hence the wide sanity range below -- it's a trend indicator, not a precise reading.

`LED` is opt-in on the firmware side: only works once HIL_LED_PIN is set for
that board (hil_config.toml `led_pin` / $HIL_LED_PIN_<BOARD>, see
docs/rig-hardware.md), so those tests skip rather than fail until then.
`RGB<gpio>` makes it an RGB LED, which also takes `LED <r> <g> <b>`.
"""

import pytest

from hil.mcustats import RESET_REASONS, parse_stats, parse_tasks


def test_temp_reading(dut):
    val = dut.temp_c()
    assert val is not None, "TEMP? -> ERR; all 3 boards should support temperatureRead()"
    # Generous range: the classic esp32's uncalibrated ROM reading commonly
    # runs 20-30C above ambient. This just catches a garbage/failed read.
    assert -10 <= val <= 130, f"implausible on-die temp: {val}°C"


def test_led_on_off(dut):
    if not dut.led(True):
        pytest.skip("HIL_LED_PIN not configured for this board (see docs/rig-hardware.md)")
    assert dut.led(False)


def test_stats_reply(dut):
    """STATS? parses, and the always-available fields are sane. Temperature and CPU may read "na" only where
    the firmware genuinely can't measure them, which none of the 3 boards should hit."""
    s = parse_stats(dut.command("STATS?"))
    assert s, "STATS? -> no STATS line"
    assert s["up"] > 0
    assert s["reset"] in RESET_REASONS, s["reset"]
    assert s["conn"] in (0, 1)
    assert s["temp"] is not None and -10 <= s["temp"] <= 130, (
        f"implausible on-die temp: {s['temp']}"
    )
    assert s["cpu0"] is not None and 0 <= s["cpu0"] <= 100.5
    assert 0 < s["heap_min"] <= s["heap"], f"heap {s['heap']} / heap_min {s['heap_min']}"
    assert 0 < s["heap_big"] <= s["heap"]
    assert s["stack_loop"] > 256, f"loop task within {s['stack_loop']} bytes of its stack limit"
    assert s["reports"] >= 0


def test_tasks_reply(dut):
    # Prime the window: the first TASKS? measures from boot, and past ~71 minutes of uptime (the 32-bit counters)
    # it replies ERR tasks. The C3/S3 aren't reset by opening their port, so that's a healthy board's normal state.
    dut.command("TASKS?")
    span, tasks = parse_tasks(dut.command("TASKS?"))
    assert span is not None and span > 0
    names = {t[0] for t in tasks}
    assert "loopTask" in names, names
    assert any(n.startswith("IDLE") for n in names), names
    assert all(stack > 0 for _, _, stack in tasks)


def test_led_kind(dut):
    kind, pin = dut.led_kind()
    if kind is None:
        pytest.skip("HIL_LED_PIN not configured for this board (see docs/rig-hardware.md)")
    assert pin >= 0
    # A colour is only accepted by an RGB build; a single-colour LED says so.
    reply = dut.command("LED 0 0 0")
    assert reply == ("OK" if kind == "rgb" else "ERR unsupported")


def test_led_rgb_colours(dut):
    if dut.led_kind()[0] != "rgb":
        pytest.skip(
            "no RGB status LED on this board (led_pin = RGB<gpio>, see docs/rig-hardware.md)"
        )
    for rgb in ((32, 0, 0), (0, 32, 0), (0, 0, 32), (0, 0, 0)):
        assert dut.led_rgb(*rgb), f"LED {rgb} rejected"
    assert dut.command("LED 256 0 0") == "ERR range"
    assert dut.command("LED x 0 0") == "ERR args"
    assert dut.command("LED 12junk 0 0") == "ERR args"  # not 12: the whole token must be digits
    assert dut.command("LED 0 0 0016") == "ERR args"  # at most 3 digits
    # Hand the LED back to the automatic state colours.
    assert dut.command("PING") == "PONG"
