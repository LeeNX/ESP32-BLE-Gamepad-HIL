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
see hil_config.toml `[board.*].led_pin` / `.conn_led_pin`, or an env var (the
latter wins, no config edit needed — handy from a CI runner: a GitHub Actions
repo Variable or Secret, forwarded into `builder/build.sh`'s environment by
`.github/workflows/hil.yml`'s `build` job — add a line there per board/LED as
they get wired). Neither is set on any board today.

## Env var naming

`builder/build.sh`'s `board_gpio()` builds the name as:

```text
HIL_<LED_PIN|CONN_LED_PIN>_<BOARD, uppercased>
```

`<BOARD>` is whatever key you used under `[board.<name>]` in
`hil_config.toml` — `esp32dev` / `esp32c3` / `esp32s3` today. All 6
combinations:

| Board | Activity LED | Connection LED |
|---|---|---|
| `esp32dev` | `HIL_LED_PIN_ESP32DEV` | `HIL_CONN_LED_PIN_ESP32DEV` |
| `esp32c3`  | `HIL_LED_PIN_ESP32C3`  | `HIL_CONN_LED_PIN_ESP32C3`  |
| `esp32s3`  | `HIL_LED_PIN_ESP32S3`  | `HIL_CONN_LED_PIN_ESP32S3`  |

This same env var, wherever it's set, is only read by whatever process runs
`builder/build.sh` (CI's `build` job, or a local `builder/build.sh` invocation)
— it's a *build-time* flag baked into the compiled firmware, so setting it on
the tester/rig itself does nothing: the tester only flashes bundles it's
already handed, it never builds (`tester/test.sh` / `tester/test-all.sh`).

## Shortcut: reuse the onboard LED (esp32dev)

Most `esp32dev`-style boards (DOIT DevKit V1, NodeMCU-32S-style clones) ship
with an onboard LED already wired to **GPIO2**, resistor included — check
your specific board (silkscreen or schematic; it varies by manufacturer).
If yours has one, skip the whole "Parts"/wiring section below for that
board: just set `led_pin = 2` and you get the activity LED for free, no
soldering. This is also why GPIO2 is safe to reuse for this purpose despite
being a boot-strapping pin — manufacturers use it this way at scale. The
caution under "Picking a GPIO per board" is about wiring a *new* external LED
to a strapping pin yourself in an unverified orientation, not about reusing
one that's already there.

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
  GPIO0/5/12/15 (boot-strapping — an LED load on these can affect boot mode
  detection). GPIO2 is also a strapping pin, but see "reuse the onboard LED"
  above — the exception, not a recommendation to wire a *new* LED there.
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
