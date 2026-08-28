# ESP32-BLE-Gamepad — hardware-in-the-loop test rig

Runs on **cylon**. An ESP32 runs the `hil_runner` firmware; this harness drives
it over USB serial and asserts the resulting BLE HID events on
`/dev/input/eventN`. It answers what the compile-only CI can't: does `press(5)`
actually produce one distinct key event on a host, do axes/hats/special buttons
map correctly, does a config change survive a re-pair.

```
pytest (this repo) ──USB serial──► ESP32 hil_runner ──BLE HID──► BlueZ ──► /dev/input/eventN
       │  "PRESS 5\n" → "OK\n"                                                      │
       └──────────────────── evdev: assert EV_KEY BTN_… value 1 ◄──────────────────┘
```

Command injection is over USB serial, **not** BLE — an independent channel, so
the harness never depends on the thing under test being up.

## Layout

| Path | What |
|---|---|
| `firmware/` | PlatformIO project — `hil_runner` serial-command firmware, one env per board |
| `firmware/include/hil_profile.h` | compile-time layout profiles (`default`, `signed-axes`) |
| `host/hil/` | serial client, evdev capture, `bluetoothctl` pairing helpers |
| `host/tests/` | `test_connection` / `test_buttons` / `test_special_buttons` / `test_axes` / `test_hats` |
| `hil_config.toml` | per-board serial port + pio env + profile |
| `run.sh` | update library checkout → pytest per board → JUnit XML + `results/summary.md` |

## One-time setup on cylon

Already done once (kept here as the record):

```bash
# library checkout the firmware builds against (THE one hard-coded path)
git clone git@github.com:LeeNX/ESP32-BLE-Gamepad ~/src/ESP32-BLE-Gamepad

# PlatformIO
python3 -m venv ~/.venvs/pio && ~/.venvs/pio/bin/pip install platformio
ln -sf ~/.venvs/pio/bin/pio ~/.local/bin/pio

# host deps
sudo apt install -y python3-dev build-essential
python3 -m venv ~/.venvs/hil && ~/.venvs/hil/bin/pip install -r host/requirements.txt
```

Groups: `leet` is already in `dialout` (serial), `input` (`/dev/input/event*`),
`plugdev` (`/dev/hidraw*`). udev rule for the HIL VID/PID
(`0005:1D34:8010.*`) added to `/etc/udev/rules.d/99-esp32-gamepad.rules`
alongside the existing `E502:BBAB` line — see `LinuxHIDTesting.md` §4 for the
rule shape.

### Attaching a board

1. Plug the ESP32 into cylon over USB.
2. `ls -l /dev/serial/by-id/` → copy the stable path into `hil_config.toml`
   under `[board.<name>].port`.
3. First run pairs it automatically (`NoInputNoOutput` agent, Just Works). Or
   pair by hand per `LinuxHIDTesting.md` §3 — the device name is
   `ESP32 BLE Gamepad HIL <board>`.

## Running

```bash
cd ~/src/esp32-ble-gamepad-hil

./run.sh                                  # default board, current library checkout
LIB_REF=some-branch ./run.sh --board esp32c3
HIL_BOARDS="esp32dev esp32c3" ./run.sh    # both boards, sequentially

# or pytest directly:
~/.venvs/hil/bin/pytest --board esp32dev
~/.venvs/hil/bin/pytest --board esp32dev --no-flash -k buttons   # skip the ~3min build
```

Useful options: `--no-flash` (firmware already on the board), `--no-pair`
(already bonded+connected), `--repair` (drop the bond and pair fresh),
`--profile signed-axes`.

## Current status

`--board esp32dev` is green on both profiles:

| Profile | Result |
|---|---|
| `default` (64 btn, 4 hat, 8 axes) | 21 passed, 3 skipped (specials), 2 xfail |
| `specials` (16 btn, 1 hat, 8 axes, 8 special btns) | 24 passed, 1 skipped, 1 xfail |

The 2 xfails are **real Linux HID-mapping limitations the rig found** (strict
xfail -> they flip to failures if the library/kernel ever start exposing them):

- **`s2` (second slider)** gets no distinct evdev `ABS_*` code. The kernel maps
  the first HID `Usage(Slider)` to `ABS_THROTTLE`; a second bare `Usage(Slider)`
  in the same collection is dropped. A game using evdev/SDL sees the same.
- **Hats 2-4** get no `ABS_HAT*` code. Linux `hid-input` only creates
  `ABS_HAT0X/Y` for the first HID `Usage(Hat Switch)`; this library's extra hat
  fields don't surface. And because the library emits hat fields reversed
  (report field 0 = `_hat4`), the *one* working hat is driven by `HAT 4` --
  `bleGamepad.setHat1()` on a multi-hat config does nothing visible on Linux.

`--board esp32c3` is not wired up yet in the harness — the C3's native
USB-Serial/JTAG port needs reconnect handling in `SerialDev` (it disappears
when the chip resets). TODO.

## Serial protocol (`firmware/src/hil_runner.cpp`)

115200 8N1, one `\n`-terminated command per line, one reply line each.

| Command | Reply |
|---|---|
| `PING` | `PONG` |
| `ID?` | `ID hil_runner profile=… board=… built=…` |
| `CONFIG?` | `CONFIG buttons=64 hats=4 axes=x,y,z,rx,ry,rz,s1,s2 special=none axesMin=0 axesMax=32767 vid=1D34 pid=8010 reportId=3 profile=default` |
| `BEGIN` | `OK` — handshake only; `bleGamepad.begin()` already ran in `setup()` |
| `CONN?` | `CONN 0` / `CONN 1` |
| `PRESS <n>` / `RELEASE <n>` | `OK` / `ERR range` |
| `SPECIAL PRESS\|RELEASE <0..7>` | `OK` / `ERR disabled` |
| `AXIS <x\|y\|z\|rx\|ry\|rz\|s1\|s2> <int16>` | `OK` |
| `HAT <1..4> <0..8>` | `OK` |
| `BATTERY <0..100>` | `OK` |
| `RESET` | `OK` — zero buttons, axes, hats |

`begin()` runs in `setup()` (like the TestAll example): calling it lazily from
`loop()` on the BEGIN command wedged the NimBLE server task on the classic
ESP32. So the firmware always advertises once booted; the two boards carry
distinct names (`HILpad esp32dev` / `HILpad esp32c3`) so the harness bonds the
right one. The name is kept short on purpose — 30 chars didn't fit the legacy
BLE advertising packet and NimBLE silently truncated it.

Hand-test: `~/.local/bin/pio device monitor -b 115200 --port <port>`, type
`PING`, `CONFIG?`, `CONN?`.

## Switching profiles

A profile = a distinct HID report descriptor. Hosts cache the descriptor at
bond time, so after switching the profile on a board the old bond is stale.
The harness detects this (it records `{mac, profile}` per device in
`~/.cache/esp32-hil/state.json`) and re-pairs automatically. By hand:
`bluetoothctl remove <mac>` then re-pair.

## How pairing works here

BlueZ needs a registered agent to auto-confirm even a no-MITM "Just Works"
pairing (`bluetoothd: new_auth() No agent available for request type 2`
otherwise), and an agent only lives as long as the `bluetoothctl` that
registered it. So `host/hil/bluetooth.py` keeps **one long-lived `bluetoothctl`
session** (`BtCtl`) open for the whole pytest run, holding a `NoInputNoOutput`
agent, and drives pair/trust/connect through it. One-shot `bluetoothctl info`
calls are still used for read-only queries.

## Known mapping quirks the tests pin down

- **Buttons**: kernel `hid-input` maps HID Button usages 1..16 to
  `BTN_SOUTH, BTN_EAST, …`, then 17+ to `BTN_TRIGGER_HAPPY1..` (which runs to
  +63, past the named `BTN_TRIGGER_HAPPY40`). Tests don't hard-code per-button
  codes — they assert the sweep is one-to-one and every code is in the gamepad
  key space. All 64 buttons round-trip.
- **Axes**: `x y z rx ry rz` → `ABS_X..ABS_RZ`, `s1` → `ABS_THROTTLE`, each
  tracking monotonically. `s2` → nothing (see Current status).
- **Hats**: only `ABS_HAT0` exists; it's driven by the *highest* firmware hat
  index because the library emits hat fields reversed (`BleGamepad.cpp`, report
  field 0 = `_hat4`). See Current status.
- **Special buttons**: all 8 (start/select/menu/home/back/vol±/mute) produce
  exactly one distinct key event; `test_special_buttons` watches every event
  node the DUT exposes since Consumer-page usages can land on a separate node.
- **`setAxes()` arg order**: the firmware uses per-axis setters
  (`setX/setRX/…`) to avoid `setAxes()`'s positional quirk (its `rX` arg lands
  in the Rx field). See the library's `IndividualAxes` example header.

## Future: GitHub self-hosted runner

Not built yet. Plan:

- Install the Actions runner on cylon as a **systemd service**, labels
  `[self-hosted, linux, hil, cylon]`, registered to `LeeNX/ESP32-BLE-Gamepad`.
- `.github/workflows/hil.yml` in the library repo: `workflow_dispatch` +
  `pull_request`, `runs-on: [self-hosted, hil]`, wrapped in an
  `environment: hil` whose protection rule **requires a maintainer to approve
  each run** — fork-PR code never runs unattended on the LAN.
- Steps: check out the library at the PR ref into `~/src/ESP32-BLE-Gamepad`,
  run `~/src/esp32-ble-gamepad-hil/run.sh`, upload `results/*.xml` +
  `summary.md`, surface it as a PR check (`dorny/test-reporter`).
- This harness would need to be reachable by the runner (push to a private
  GitHub repo, or keep it at the `~/src` path the workflow assumes).
