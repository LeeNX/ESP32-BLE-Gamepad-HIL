# ESP32-BLE-Gamepad — hardware-in-the-loop test rig

An ESP32 runs the `hil_runner` firmware; this harness drives it over USB serial
and asserts the resulting BLE HID events on `/dev/input/eventN`. It answers what
the compile-only CI can't: does `press(5)` actually produce one distinct key
event on a host, do axes/hats/special buttons map correctly, does a config
change survive a re-pair.

The rig is split into two roles so the BLE host can be a small board (a
Raspberry Pi 3B+) that can't build firmware in reasonable time:

```
 BUILDER (cylon / a CI runner)              TESTER (Raspberry Pi 3B+ / cylon)
 ┌────────────────────────────┐   bundle   ┌──────────────────────────────────┐
 │ builder/build.sh:          │  (rsync/   │ tester/test.sh:                  │
 │  pio run  (lib under test) │   CI       │  tester/flash.py  (esptool only) │
 │  -> bundles/<b>-<p>-<sha>/ │  artifact) │  pytest  (pyserial+evdev+bluez)  │
 │     *.bin + manifest.json  │──────────►│   USB─► ESP32 ─BLE─► /dev/input/  │
 └────────────────────────────┘            │  -> results/junit-*.xml summary  │
                                           └──────────────────────────────────┘
```

- **builder** needs PlatformIO. `builder/build.sh` compiles `hil_runner`
  against the library-under-test and writes a **bundle**: the flashable
  `.bin` parts + `manifest.json` (chip, flash offsets, sha256s, lib git sha).
- **tester** needs only `esptool` + the pytest deps (all pure-Python /
  lightweight — fine on a Pi). `tester/test.sh` flashes a bundle and runs the
  suite.
- Command injection is over USB serial, **not** BLE — an independent channel,
  so the harness never depends on the thing under test being up.

One box can be both (`./run.sh` does builder then tester locally).

## Layout

| Path | What |
|---|---|
| `firmware/` | PlatformIO project — `hil_runner` serial-command firmware, `hil_profile.h` layout profiles (`default`, `signed-axes`, `specials`) |
| `builder/build.sh` `builder/make_bundle.py` | compile → firmware bundle(s) → optional `--push` rsync to the tester |
| `tester/bootstrap.sh` | one-time, idempotent tester provisioning (apt deps, venv, groups, udev) |
| `tester/flash.py` `tester/test.sh` | flash a bundle with esptool, run the suite, write `results/` |
| `host/conftest.py` `host/hil/` `host/tests/` | the pytest suite: fixtures, serial/evdev/bluetooth helpers, the tests |
| `hil_config.toml` (+ gitignored `hil_config.local.toml`) | per-machine ports, ssh host, builder board/profile matrix |
| `run.sh` | one-box: build all bundles then flash+test each |
| `.gitea/workflows/hil.yml` | Gitea CI: build job → SSH-to-Pi test job |

## Setup

### Builder (has PlatformIO — e.g. cylon)

```bash
git clone <gitea>/leet/esp32-ble-gamepad-hil ~/src/esp32-ble-gamepad-hil
git clone git@github.com:LeeNX/ESP32-BLE-Gamepad ~/src/ESP32-BLE-Gamepad
python3 -m venv ~/.venvs/pio && ~/.venvs/pio/bin/pip install platformio
ln -sf ~/.venvs/pio/bin/pio ~/.local/bin/pio
```

`hil_config.local.toml` on the builder only needs `[rig] lib_dir` / `pio` if
they differ from the template, plus `[tester] ssh_host` for `--push`.

### Tester (has the ESP32 + a BLE adapter — e.g. the Pi)

```bash
git clone <gitea>/leet/esp32-ble-gamepad-hil ~/esp32-ble-gamepad-hil
~/esp32-ble-gamepad-hil/tester/bootstrap.sh          # idempotent; re-run after updates
```

`tester/bootstrap.sh` does the apt deps (`bluez` + `rfkill` + build tools), the
`~/.venvs/hil` venv from `tester/requirements.txt`, the `dialout` / `input` /
`plugdev` group adds, the udev rule, and a `hil_config.local.toml` stub. It
prints the remaining manual steps. For a managed fleet, mirror it as an Ansible
role — the step list is in that script's header. What it deliberately leaves
manual:

- **Power**: a Pi 3B+ can't reliably power an ESP32 doing BLE off its own USB —
  brownouts show up as a reset loop that never advertises (seen on cylon too).
  Use a **powered USB hub** for the ESP32.
- BLE: the Pi 3B+ built-in adapter (BT 4.1, shares the WiFi antenna) works but a
  USB BT dongle is steadier for a test rig.
- `hil_config.local.toml` on the tester sets the real `[board.<b>].port`
  (`ls -l /dev/serial/by-id/`).

First run pairs the device automatically (`NoInputNoOutput` agent, Just Works),
or pair by hand: `bluetoothctl` → `scan on`, wait for `HILpad <board>`, then
`pair <mac>` / `trust <mac>` / `connect <mac>` (the persistent agent the suite
runs is only needed for unattended re-pairs).

### Local (one box)

Do **both** the Builder and Tester setup above on one machine, plug the ESP32
into it (still via a powered hub), and `./run.sh` builds, flashes and tests in
one go — no SSH, no `--push`. This is the fastest edit/test loop.

**This one box must be Linux.** The suite asserts on `/dev/input/event*` via
`evdev` and pairs through BlueZ `bluetoothctl`; both are Linux-only, as are the
`evdev` / `pyudev` deps.

**macOS can be the Builder only** — PlatformIO builds fine there, so
`builder/build.sh --push` from a Mac to a Linux tester (a Pi, or a Linux
desktop) works. Running the pytest suite on macOS does not — see below for
what that would take.

### macOS as a tester (unsupported — gap list)

Flashing and the serial command channel already work on macOS: `esptool` and
`pyserial` are cross-platform, just point `[board.<b>].port` at a
`/dev/cu.usbserial-*` path. The two things the suite needs from the OS —
initiating the BLE bond and reading the resulting HID events as ground truth —
have no macOS implementation. What's missing:

1. **A CoreBluetooth pairing backend** to replace BlueZ `bluetoothctl`
   (`host/hil/bluetooth.py` — `BtCtl`, `ensure_paired`, and
   scan/pair/trust/connect/remove-bond). *Blocker:* macOS hands a BLE-HID
   device's GATT service to the system HID stack, so a CoreBluetooth app can't
   touch it to trigger pairing, and `blueutil` / `IOBluetoothDevicePair` are
   Classic-BT oriented and won't pair a BLE-only peripheral. Pairing would stay
   a **one-time manual step in System Settings ▸ Bluetooth**, with the suite run
   as `--no-pair`. The `--repair` / automatic re-pair-on-profile-change path
   (`conftest.py` `bt_mac`) would then need a manual "Forget This Device" first.

2. **An IOHIDManager read backend** to replace `host/hil/evdev_utils.py`
   (`find_gamepad`, `find_all_nodes`, `Capture.collect` / `key_changes` /
   `abs_changes`): enumerate `IOHIDDevice`s by product name (`HILpad <board>`),
   open, subscribe to input-value callbacks. Needs `pyobjc-framework-IOKit` (or
   a ctypes IOKit shim) in place of `evdev` / `pyudev`. *Blocker:* reading HID
   input from a device the process doesn't own requires the **Input Monitoring**
   TCC permission, granted by hand in System Settings (or via an MDM PPPC
   profile) to the python running pytest — not scriptable from a bootstrap.

3. **macOS ground-truth mapping tables.** Every `host/tests/test_*.py` asserts
   against Linux `hid-input` codes (`BTN_SOUTH…`, `ABS_THROTTLE` for `s1`,
   `ABS_HAT0*`), and the two strict xfails (`s2`, hats 2-4) pin *Linux kernel*
   behaviour. IOKit parses the report descriptor itself and exposes raw HID
   usage-page/usage, so the expected values — and which quirks even exist — must
   be re-characterised once on macOS and kept as a parallel table chosen by
   platform.

4. **Backend selection + a macOS bootstrap.** `conftest.py` fixtures (`gamepad`,
   `all_nodes`, `_reset`) and `tester/requirements.txt` are hardwired to evdev;
   they'd dispatch on `sys.platform`. `tester/bootstrap.sh` is apt/systemd/udev
   — a `bootstrap-macos.sh` would do Homebrew python + the pyobjc deps and print
   the manual TCC / pairing steps.

5. **CI**: a headless Mac runner needs a logged-in GUI session for BLE plus the
   TCC grants pre-provisioned (MDM PPPC or a seeded TCC.db). More friction than
   the Linux/Pi path — a Linux tester stays the recommended CI node.

## Running

```bash
# one box (build + flash + test here)
./run.sh                                    # config defaults, current lib checkout
LIB_REF=some-branch ./run.sh --profiles default
./run.sh --boards esp32dev --profiles "default specials"
./run.sh --profiles default -- -k buttons   # args after -- go to pytest

# split: on the builder
PUSH=1 LIB_REF=some-branch builder/build.sh
# then on the tester
tester/test.sh ~/hil-bundles/esp32dev-default-<sha>/

# pytest directly against a hand-flashed board (no builder needed)
~/.venvs/hil/bin/pytest --board esp32dev --no-flash --port /dev/ttyUSB0
```

Useful pytest options: `--bundle <dir>` (flash a bundle via esptool),
`--no-flash`, `--no-pair`, `--repair` (drop bond + pair fresh),
`--profile specials`.

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

`--board esp32c3`: flashes and pairs, but the command channel needs a wiring
change — see **ESP32-C3 serial bridge** below. Not in the CI matrix
(`.gitea/workflows/hil.yml` runs `esp32dev` only) until that's done.

## ESP32-C3 serial bridge

`hil_runner` writes the command protocol to `Serial`, and on the C3 with this
rig's build (`ARDUINO_USB_CDC_ON_BOOT` unset → `0`) `Serial` is **UART0**
(`GPIO21` TX / `GPIO20` RX), *not* the USB-C port. The USB-C connector on a C3 is
the native USB-Serial/JTAG peripheral — great for flashing, but it carries no
`hil_runner` I/O in this build, and even with CDC-on-boot it re-enumerates on
every chip reset and `serial.Serial()` can wedge on the half-open handle
(`SerialDev._open` guards that with a threaded timeout so it fails fast, but a
run can still lose the port mid-test).

So the C3 wants **two interfaces**: flash over USB-C, talk over an external
3.3 V USB-UART bridge on UART0. The harness supports this with a `flash_port`
distinct from `port`:

```toml
[board.esp32c3]
port       = "/dev/serial/by-id/usb-<CP2102-or-CH340-bridge>-if00-port0"   # UART0
flash_port = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_…-if00"  # USB-C
```

(`flash_port` defaults to `port`; `--flash-port` / `HIL_FLASH_PORT` override it.)

### Wiring — ESP32-C3 SuperMini

The SuperMini has **no onboard USB-UART chip** (unlike the DevKitC/DevKitM, which
expose a CP2102 on a second connector), so an external adapter is mandatory,
not just recommended. Any FTDI / CP2102 / CH340 dongle set to **3.3 V logic**
(the C3 is **not** 5 V tolerant):

| USB-UART adapter | C3 SuperMini | |
|---|---|---|
| `GND` | `GND` | common ground is required |
| `TX` (adapter → C3) | `GPIO20` (U0RXD) | pin nearest the USB-C shell, one side |
| `RX` (adapter ← C3) | `GPIO21` (U0TXD) | pin nearest the USB-C shell, other side |
| `VCC` / `5V` / `3V3` | **leave unconnected** | board is powered + flashed via USB-C |

Both cables plug into the powered hub. Don't wire the adapter's VCC *and* USB-C —
pick one power source (USB-C is simplest, and it's needed for flashing anyway).

### Approaches, trade-offs

| | Flash | Command channel | Verdict |
|---|---|---|---|
| **Native USB-C only** (`ARDUINO_USB_CDC_ON_BOOT=0`, today) | USB-C ✓ (slow, retries) | none — `Serial` is UART0, not exposed | broken for the suite |
| **Native USB-C only**, rebuild with `-D ARDUINO_USB_CDC_ON_BOOT=1` | USB-C ✓ | USB-C CDC — re-enumerates on every reset, races NimBLE for USB IRQ budget, "port vanished" mid-run | fragile; not for CI |
| **USB-C + external UART bridge** (recommended) | USB-C ✓ | FTDI/CP210x on UART0 — never resets when the C3 does, fully isolated from the flash path, identical to the stable esp32dev setup, works with the default build | **use this** |
| **External bridge for flash too** | UART0 — needs holding `BOOT` (GPIO9) + tapping `RST` by hand (SuperMini has no auto-reset on UART0) | UART0 ✓ | no good for unattended CI |

**Pros of the bridge approach:** rock-solid command channel (a real UART, not
the C3's shared USB peripheral); flashing resets never disturb it; no firmware
rebuild; same code path and reliability as esp32dev. **Cons:** a $2 adapter and
three jumper wires per C3; two USB devices per board on the hub; you must set
both `port` and `flash_port`.

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

Hand-test: `python3 -m serial.tools.miniterm <port> 115200` (or
`pio device monitor` on the builder), type `PING`, `CONFIG?`, `CONN?`,
`PRESS 5`, `AXIS x 16000`, `HAT 4 3`.

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

## Gitea CI

`.gitea/workflows/hil.yml` — a **build** job on any Gitea runner (installs
PlatformIO fresh, runs `builder/build.sh`, uploads the bundles as an artifact),
then a **hil-test** job on a normal runner that downloads the bundles, `rsync`s
them to the Pi and `ssh`es in to run `tester/test.sh`, then publishes the JUnit
report. The Pi is a plain **SSH target**, not a runner — nothing untrusted
executes on it directly.

Prerequisites:

- Push this harness repo and the library to your Gitea. Enable Actions on the
  repo; make sure the runners can fetch `actions/checkout` etc. (Gitea
  `DEFAULT_ACTIONS_URL`).
- The Pi has this repo at `~/esp32-ble-gamepad-hil`, `tester/bootstrap.sh` run,
  `hil_config.local.toml` port set (or the committed template already matches),
  ESP32 + BLE attached. The bootstrap health check must show a powered BT
  controller and ≥1 readable input node — the two things a fresh Pi image gets
  wrong are BT left **rfkill soft-blocked** and the CI user missing from the
  **`input`** group (`evdev.list_devices()` then returns `[]` and every test
  times out).
- Repo secrets: `HIL_PI_HOST`, `HIL_PI_USER`, `HIL_PI_SSH_KEY` (a passphrase-less
  key authorised on the Pi).

Triggers: push to `main` / `hil-*`, manual dispatch (with `lib_repo` /
`lib_ref` inputs), or `repository_dispatch` type `hil` from the library repo.
A `concurrency: hil-pi` group serialises runs — there's one physical rig.

**Untrusted code**: the build job compiles whatever library ref it's handed and
the test job flashes it to hardware on your LAN. Keep the triggers to
same-repo pushes + manual dispatch; don't wire it to run automatically on PRs
from forks.
