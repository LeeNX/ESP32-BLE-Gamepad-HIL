# Rig hardware: status LED wiring

Two opt-in status LEDs per board, both dormant by default on every board and
every fork/clone of this repo — nothing physically or electrically changes
until you wire one up and set its GPIO. See `firmware/src/hil_runner.cpp`
(search `HIL_LED_PIN`) and README "Serial protocol" / "Rig hardware TODO".

| LED | Firmware macro | Behaviour |
|---|---|---|
| Activity | `HIL_LED_PIN` | Brief pulse (~30ms) on every serial command received. Explicit `LED ON`/`LED OFF` override — lets you check the wiring with `miniterm` alone, no BLE pairing needed. With an `RGB<gpio>` value it drives an RGB LED with state colours instead — see "RGB status LED" below. |
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

## RGB status LED (esp32s3, and any board with a WS2812)

Some boards have no plain LED, only an onboard addressable RGB LED (WS2812 /
"NeoPixel"). The ESP32-S3 DevKitC-1 is one: GPIO48 on v1.0 boards, GPIO38 on
v1.1 (the version is on the silkscreen). `digitalWrite()` on that GPIO does
nothing useful, so give the same activity-LED setting an `RGB` prefix:

```toml
[board.esp32s3]
led_pin = "RGB48"          # or "RGB38" on a v1.1 board; "rgb:48" works too
```

or, from CI, the existing variable: `HIL_LED_PIN_ESP32S3=RGB48`. There is no
extra variable; `builder/build.sh` (and `conftest.py`) see the prefix and add
an internal `-DHIL_LED_RGB` build flag. A plain number still means a
single-colour LED, and any other value fails the build.

The one LED then shows both state and activity:

| Colour | Meaning |
|---|---|
| Dim blue, steady | Booted and advertising, not connected |
| Green, steady | BLE-connected (mirrors `CONN?`) |
| White flash (~30ms) | A serial command was handled |
| Red flash (~200ms) | A command replied `ERR …` (not cut short by the next command's white flash) |
| Amber | `BOOT <n>` slot switch accepted, about to restart |

Colours are kept dim on purpose. Each LED write blocks the CPU for roughly
30–50µs on the RMT peripheral, so the firmware only records the colour while
it handles a command. It writes in `loop()` afterwards, and only when the
colour changes, so the `--bench` timing path never waits on the LED.

Serial checks, with no pairing needed:

- `LED?` → `LED rgb 48` confirms the build picked up the prefix.
- `LED 32 0 0`, `LED 0 32 0`, `LED 0 0 32` should show red, green and blue.
  If the colours come out swapped, the LED isn't GRB-ordered; the core's
  `rgbLedWrite()` assumes GRB.
- `LED ON` / `LED OFF` work as on a single-colour LED (white / off).

An override lasts until the next non-LED command, then the automatic colours
take over again. `HIL_CONN_LED_PIN` still works alongside, if you want a
separate connection LED as well.

### Fallback: `97`, white only, no RGB code

The Arduino core already maps the S3's onboard RGB LED to a virtual pin,
`RGB_BUILTIN` = `SOC_GPIO_PIN_COUNT` (49) + 48 = **97**. `pinMode()` /
`digitalWrite()` on 97 drive the WS2812 as plain white on/off. So
`HIL_LED_PIN_ESP32S3=97` (plain number, no prefix) gives the normal
single-colour activity pulse and `LED ON|OFF`, with none of the colour logic.
It only works for an LED on GPIO48, the one the core's variant file
(`variants/esp32s3/pins_arduino.h`) names. A v1.1 board (GPIO38) needs
`RGB38`.

## Observer firmware (Bluepad32)

When a board runs the Bluepad32 observer (the observer matrix,
`firmware/observer`), the same settings drive its status LEDs. There's nothing
extra to set: `builder/build-observers.sh` reads `led_pin` / `conn_led_pin`
(or `$HIL_LED_PIN_<BOARD>` / `$HIL_CONN_LED_PIN_<BOARD>`) through the same
`builder/led-pin.sh` as `builder/build.sh`. The idle colours differ from
hil_runner's, so you can see a board's current role at a glance:

| Colour | hil_runner (gamepad) | Observer |
|---|---|---|
| Dim blue | Advertising | — |
| Dim purple | — | Scanning, no gamepad |
| Cyan | — | Gamepad connected, not ready yet |
| Green | BLE-connected | Gamepad ready |
| White flash | Serial command handled | Reports arriving (at most one flash per ~100ms, so a steady stream flickers) |
| Red flash | `ERR …` reply | `HIL err …` reply |
| Amber | `BOOT <n>` slot switch | `BOOT <n>` slot switch |

On a single-colour LED the observer pulses on reports and errors only, and
`conn_led_pin` stays on while a gamepad is ready.

The observer's own console commands for its LED are `statusled?` →
`HIL statusled rgb <gpio>` / `mono <gpio>` / `none`, and
`statusled on|off|<r> <g> <b>`. Don't confuse these with `led <n>` / `rgb <r> <g> <b>`,
which drive the connected *gamepad's* LEDs over Bluetooth. A `97` setting
works there too: the observer reads any pin past the chip's last GPIO as
"RGB LED on pin − GPIO count", like Arduino's `RGB_BUILTIN`. So with `97`,
an S3 gets full colours as an observer but white only as a gamepad.

The observer drives the WS2812 with ESP-IDF's RMT TX driver directly, with no
extra component to download. A low-priority task does every LED write, so the
Bluetooth thread only sets flags.

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
