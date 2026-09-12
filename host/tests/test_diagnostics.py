"""On-die temperature (`TEMP?`) and the activity/status LED (`LED ON|OFF`).
See README "Serial protocol" and "Rig hardware TODO".

`TEMP?` works on all 3 boards -- Arduino's temperatureRead() covers the
classic esp32 too (an undocumented ROM function), not just esp32-c3/-s3's
official sensor driver. The classic esp32's reading is uncalibrated and runs
well above ambient (known Arduino-core behaviour, not a rig fault), hence the
wide sanity range below -- it's a trend indicator, not a precise reading.

`LED` is opt-in on the firmware side: only works once HIL_LED_PIN is wired +
set in platformio.ini for that board. Not done on any board yet, so that test
skips rather than fails until then.
"""

import pytest


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
