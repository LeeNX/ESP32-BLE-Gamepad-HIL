"""On-die temperature (`TEMP?`), MCU health (`STATS?`, `TASKS?`) and the activity/status LED (`LED ON|OFF`).
See README "Serial protocol" and "Rig hardware TODO".

`TEMP?` works on all 3 boards: the IDF sensor driver on the esp32-c3/-s3 (20-100 C range), an undocumented
ROM function on the classic esp32. The classic esp32's reading is uncalibrated, runs well above ambient and
moves in 0.56 C steps, hence the wide sanity range below -- it's a trend indicator, not a precise reading.

`LED` is opt-in on the firmware side: only works once HIL_LED_PIN is wired +
set in platformio.ini for that board. Not done on any board yet, so that test
skips rather than fails until then.
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
        pytest.skip("HIL_LED_PIN not wired/configured for this board (see platformio.ini)")
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
