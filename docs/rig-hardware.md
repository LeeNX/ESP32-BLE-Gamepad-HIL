# Rig hardware: status LED wiring

Two opt-in status LEDs per board, both dormant by default on every board and
every fork/clone of this repo — nothing physically or electrically changes
until you wire one up and set its GPIO. See `firmware/src/hil_runner.cpp`
(search `HIL_LED_PIN`) and README "Serial protocol" / "Rig hardware TODO".

| LED | Firmware macro | Behaviour |
|---|---|---|
| Activity | `HIL_LED_PIN` | Brief pulse (~30ms) on every serial command received. Explicit `LED ON`/`LED OFF` override — lets you check the wiring with `miniterm` alone, no BLE pairing needed. |
| Connection | `HIL_CONN_LED_PIN` | Steady on while bonded+connected over BLE, off otherwise (mirrors `CONN?`). No serial override — it's state-driven, so verify it by actually pairing. |

Setting the GPIO for either one is a config/env change, not a firmware edit —
see hil_config.toml `[board.*].led_pin` / `.conn_led_pin`, or `$HIL_LED_PIN_
<BOARD>` / `$HIL_CONN_LED_PIN_<BOARD>` env vars (the latter win, no config
edit needed — handy from a CI runner). Neither is set on any board today.

## Parts (per LED)

- One 5mm LED. Different colours for the two LEDs (e.g. yellow = activity,
  green = connection) make them readable at a glance.
- One resistor — **330Ω for everything**, see sizing below.
- Two short lengths of wire terminated in **female Dupont connectors** (or a
  female-to-female jumper wire cut in half and soldered on), *or* a pre-built
  "LED module" breakout board (LED + resistor already on a small PCB with a
  standard 3-pin header — a few dollars for a pack, zero soldering).

## Resistor sizing

GPIOs are 3.3V logic. Target 5–10mA — bright enough to read, well under the
ESP32's ~40mA absolute per-pin limit.

```text
R = (Vcc − Vf) / I
```

- Red/yellow/green (Vf ≈ 2.0V) @ 8mA: (3.3 − 2.0) / 0.008 ≈ 162Ω
- Blue/white (Vf ≈ 3.1V) @ 8mA: (3.3 − 3.1) / 0.008 ≈ 25Ω (self-limits lower;
  don't go below ~150Ω regardless, some margin against a marginal 3.3V rail
  is cheap insurance)

**330Ω** covers every colour safely (≈4mA on red/yellow/green, dimmer than
the calculation above but plenty visible for an indicator — this isn't a
flashlight) and means stocking one resistor value instead of matching it to
whatever colour LED you have on hand.

## Circuit

```text
GPIO ---[330Ω]--- LED anode (+, longer leg)
                   LED cathode (−, shorter leg / flat side) --- GND
```

## Easy plug-in — move a LED between boards with no rewiring

The point of building each LED as its own 2-wire unit (resistor soldered to
the LED, both leads terminated in female Dupont connectors) is that nothing
is permanent:

- Plug the two wires straight onto a GPIO pin + a GND pin on whichever
  board's heading in for a session — no breadboard required.
- Moving it to a different board, or a different GPIO on the same board, is
  just unplugging two jumper wires and plugging them in elsewhere.
- Build a couple of each colour so a full 2-LED kit can live with whichever
  board is actively being tested, and duplicating the setup for a second rig
  or a quick bench check is "buy/solder another two."

## Picking a GPIO per board

Both LEDs are equally fine on any spare GPIO; the only thing that matters is
avoiding pins something else already claims. Known conflicts:

- **esp32dev**: GPIO1/GPIO3 (UART0 — `hil_runner`'s `Serial` *and* flashing);
  GPIO0/2/5/12/15 (boot-strapping — an LED load on these can affect boot mode
  detection).
- **esp32c3**: GPIO20/GPIO21 (UART0 — `hil_runner`'s `Serial`, see README
  "ESP32-C3 serial bridge"); GPIO8/GPIO9 (boot-strapping).
- **esp32s3**: GPIO43/GPIO44 (UART0 on most DevKitC-1 boards — check yours);
  whatever the native USB-OTG peripheral uses (`flash_port`).

Anything else free is fine — a slow on/off LED has no frequency, ADC, or
timing requirement that would rule out an otherwise-ordinary GPIO.

## Why "default not wired up" matters

Both LEDs compile out to no-ops (`ledSetup()`/`ledPulse()`/`ledService()` and
their `connLed*` equivalents become empty functions) when their macro is
undefined, and `LED ON`/`OFF` falls back to `ERR unsupported`. So this code
is safe to carry on every board, every fork, every CI build — it costs
nothing until someone actually solders an LED and sets its GPIO.
