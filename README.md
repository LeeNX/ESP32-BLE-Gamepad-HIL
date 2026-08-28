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

## Serial protocol (`firmware/src/hil_runner.cpp`)

115200 8N1, one `\n`-terminated command per line, one reply line each.

| Command | Reply |
|---|---|
| `PING` | `PONG` |
| `ID?` | `ID hil_runner profile=… board=… built=…` |
| `CONFIG?` | `CONFIG buttons=64 hats=4 axes=x,y,z,rx,ry,rz,s1,s2 special=… axesMin=… axesMax=… vid=1D34 pid=8010 reportId=3 profile=default` |
| `BEGIN` | `OK` — calls `bleGamepad.begin()` (nothing advertises before this) |
| `CONN?` | `CONN 0` / `CONN 1` |
| `PRESS <1..64>` / `RELEASE <1..64>` | `OK` / `ERR range` |
| `SPECIAL PRESS\|RELEASE <0..7>` | `OK` |
| `AXIS <x\|y\|z\|rx\|ry\|rz\|s1\|s2> <int16>` | `OK` |
| `HAT <1..4> <0..8>` | `OK` |
| `BATTERY <0..100>` | `OK` |
| `RESET` | `OK` — zero buttons, axes, hats |

Hand-test: `~/.local/bin/pio device monitor -b 115200 --port <port>`, type
`PING`, `CONFIG?`, `BEGIN`, `CONN?`.

## Switching profiles

A profile = a distinct HID report descriptor. Hosts cache the descriptor at
bond time, so after switching the profile on a board the old bond is stale.
The harness detects this (it records `{mac, profile}` per device in
`~/.cache/esp32-hil/state.json`) and re-pairs automatically. By hand:
`bluetoothctl remove <mac>` then re-pair.

## Known mapping quirks the tests pin down

- **Buttons**: kernel `hid-input` maps HID Button usages 1..16 to
  `BTN_TRIGGER, BTN_THUMB, …`, then 17+ to `BTN_TRIGGER_HAPPY1..40`. The tests
  don't hard-code per-button codes (kernel-version sensitive) — they assert the
  sweep is one-to-one and every code is in the gamepad key range.
- **Hats**: the library emits hat fields in reverse of the hat index
  (`BleGamepad.cpp` — report field 0 is `_hat4` when 4 hats are configured), so
  firmware `HAT n` drives `ABS_HAT{4-n}`. `test_hats` asserts exactly that.
- **Consumer specials** (home/back/volume) are Consumer-page usages; the kernel
  routes them to a separate consumer-control input node. `test_special_buttons`
  watches all of the DUT's event nodes at once.
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
