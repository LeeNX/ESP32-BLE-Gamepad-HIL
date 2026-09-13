# Desktop tester (macOS / Windows) — serial-only subset

The Linux rig (repo root) asserts against `evdev` / BlueZ / `hidraw`, which is
Linux-only. This directory is the **portable slice**: the checks that ride the
`hil_runner` USB-serial command channel and need no BLE bond and no host HID
stack. It runs on macOS and Windows with nothing but Python + `pyserial`.

It is **not** a fork — it reuses the rig's `hil.serialdev` / `hil.hidraw`
(`../host`) and the checked-in golden descriptors (`../firmware/golden`)
directly. One repo, one source of truth for the protocol and the goldens.

## Status

| Layer | State |
|---|---|
| `esptool` flashing (`../tester/flash.py`) | ✅ reused as-is, cross-platform |
| Serial protocol client (`hil.serialdev.SerialDev`) | ✅ reused as-is |
| Serial-only assertions (`tests/test_serial_only.py`, `-m serial_only`) | ✅ done |
| One-time BLE pairing | ✅ `pair-assist.py` — names the device to click, `--clear-bonds`, `--reconnect` (macOS blueutil), waits on `CONN?` |
| Behavioural — buttons / axes / hats (`-m sdl`, SDL joystick, + `-m hid` raw for buttons) | ✅ `test_buttons.py` / `test_axes.py` / `test_hats.py` / `test_hid_reports.py`; board bonded here |
| Press-to-host latency (`-m latency`) | ✅ `test_latency.py`; hidapi timestamps (rig-comparable only loosely — see below) |
| Force-feedback / rumble (`-m sdl`) | ✅ `test_rumble.py` — documents unsupported, see below |
| GATT reads (Device Info / PnP / Battery over CoreBluetooth / WinRT) | 🔴 later step |
| Remote drive (SSH / CI runner) | 🔴 later — for now, run it by hand (below) |

### Three levels of test

- **`-m serial_only`** — the serial command channel only. Firmware regressions
  in HID **descriptor generation** (`RMAP?` vs the golden), **report sizing**,
  **DIS / PnP**, the **advertised BLE name** (`NAME?`), protocol round-trips. No
  BLE bond — a laptop smoke test.
- **`-m sdl`** — the DUT as an **SDL joystick** (pygame). SDL's per-OS driver
  (IOKit / RawInput / evdev) does the HID parsing and control numbering, so this
  is **"what a game sees"** — the behavioural layer: buttons one-to-one, axes
  monotonic with correct −1/0/+1 endpoints, all 8 hat directions. Headless, no
  window, no macOS permission for a game controller.
- **`-m hid`** — the **raw HID input reports** off the device (hidapi), decoded
  against the report layout. Pins **firmware + descriptor + transport**,
  independent of SDL / the OS. The lower-level cross-check.
- **`-m latency`** *(opt-in — a bare `pytest` run skips it)* — `TPRESS` on the
  serial channel, then time the blocking hidapi read of the resulting report.
  p50 / p90 / p99 for BLE-air (`ble`) and end-to-end (`e2e`), plus a dropped
  count and the serial `ping` baseline. `--latency-n` (default 100),
  `--latency-json` writes `results/latency-*.json`. **Not directly comparable to
  the rig's numbers** — the rig times off the kernel evdev timestamp; here it's
  a userspace hidapi read, so a few ms high and jittier. Good for regression /
  same-box comparison.

`-m sdl` / `-m hid` / `-m latency` need the board **bonded to this host**
(`pair-assist.py`).

### What macOS/SDL does differently from the Linux rig

- **All hats work** — SDL surfaces every hat (the rig's evdev only ever makes
  `ABS_HAT0`). But the library still emits the hat fields **reversed**, so
  firmware hat `h` is SDL hat `n_hats − h`. `test_hats.py` pins that.
- **8 axis slots** — SDL reports `get_numaxes() == 8` on `default` and each
  responds to its firmware axis (Linux collapses the second bare
  `Usage(Slider)`, `s2`, a strict xfail on the rig).
- **Buttons**: SDL exposes all 64 on `default`, one-to-one — no ~79-code
  ceiling like the Linux gamepad keycode block.
- SDL normalises any HID logical range to `[−1.0, +1.0]`.

So SDL is a *cleaner* view than evdev here. A raw `pyobjc-IOKit` backend would
only be needed to observe something SDL's remapping hides — none found yet.

**This is macOS-specific, not "SDL in general" — SDL on the Linux rig itself
inherits evdev's limits.** SDL's Linux joystick backend is built *on* evdev
(unlike macOS's IOKit backend, which talks to the HID transport more
directly), so running SDL on the rig doesn't recover what evdev already
dropped. Confirmed via `host/hil/sdlreport.py` / `host/tests/test_sdl_report.py`
against the real rig (`default` profile, 64 buttons / 8 axes / 4 hats
declared): SDL there sees **22 buttons, 2 axes, 0 hats** — same ceiling as
raw evdev, not the rig's real HID report. It does still resolve
`SDL_IsGameController() == true` with an auto-generated mapping, so a
Linux/SDL app at least gets *a* working GameController, just a smaller one
than the actual descriptor. This gap (and the rumble finding below) is the
concrete case for the SInput profile (`docs/TODO.md`) — a report format
chosen so a generic OS/SDL consumer doesn't have to guess-map around it.

**Rumble / force-feedback: unsupported on both platforms**, confirmed via
`sdlgamepad.rumble()` here (pygame `Joystick.rumble()` → `False`) and
`SDL_JoystickRumble()` on the rig (`rc=-1`, `"That operation is not
supported"`). Root cause on both: this HID report descriptor has no
force-feedback usage, and neither platform's SDL backend has a hardcoded
driver for this custom VID:PID to fall back on. `test_rumble.py` /
`test_sdl_rumble_unsupported` pin this as a documented limit — they start
failing (usefully) if the descriptor ever gains real FF support.

### Latency — first run (macOS 26, esp32dev, `minimal`, n=100)

```text
link: interval 30 ms, MTU 255      ping RTT (serial) p50 3.6 ms
ble  p50 23  p90 78  p99 173  max 229   (ms)     dropped 0
```

p50 ≈ half the connection interval + stack, in line with the rig's ~19 ms
median (the rig's interval is 48.75 ms, this one 30). The **tail** is the story
— p99 ~170 ms vs the rig's ~20–68: macOS isn't a real-time BLE-HID host, and
the userspace hidapi read adds its own jitter. 0 dropped over 100 presses.

## Setup

**Python 3.10–3.13** (pygame has no 3.14 wheel yet) and a USB-serial driver for
your board's bridge (CP210x / CH34x — usually built into macOS; vendor VCP on
Windows).

```bash
cd desktop
python3.13 -m venv .venv        # a 3.10-3.13 interpreter; `uv venv` needs --seed for pip
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
```

## Firmware — use the `local` profile

The tester never compiles (same rule as the rig) — it flashes a **bundle**.
Build one for the **`local`** profile: it's a small classic-gamepad layout
(4 btn / 1 hat / X-Y) that is **not in the CI matrix**, and it advertises as
**`HILdev <board>`** instead of `HILpad <board>` — so your board doesn't clash
with the reference rig's gamepads in the Bluetooth list.

```bash
# from the repo root -- needs PlatformIO + the library checkout (rig setup)
HIL_BOARDS=esp32dev HIL_PROFILES=local builder/build.sh
# -> bundles/esp32dev-local-<sha>/

# sharing BLE space with a rig / another dev? name yours (<= 18 chars) --
# $HIL_DEVICE_NAME in the env, or --name (--name wins):
HIL_DEVICE_NAME="HILdev clt" HIL_PROFILES=local builder/build.sh
builder/build.sh --profiles local --name "HILdev clt"
```

The board reports whatever it ended up with over serial — `NAME?` (or `pair-assist.py`).

`../tester/flash.py` flashes the bundle with `esptool` (the `flashed` fixture
calls it). Or flash by hand / with the rig and pass `--no-flash`. The board
reports its own advertised name over serial — `NAME?`, or
`python -m serial.tools.miniterm <port> 115200`.

## Running

Find the port — macOS `ls /dev/cu.usbserial-*`, Windows `Get-PnpDevice -Class Ports`.

```bash
# serial-only: flash the local bundle first, no bond needed
.venv/bin/pytest -m serial_only \
    --bundle=../bundles/esp32dev-local-<sha> --profile=local --port=/dev/cu.usbserial-110

# board already flashed:
.venv/bin/pytest -m serial_only --no-flash --profile=local --port=/dev/cu.usbserial-110

# + the behavioural button tests (board must be bonded here -- pair-assist.py first):
.venv/bin/pytest --no-flash --profile=local --port=/dev/cu.usbserial-110       # all markers except latency
.venv/bin/pytest -m sdl --no-flash --profile=local --port=/dev/cu.usbserial-110

# latency sweep (opt-in, ~15-30s, board bonded):
.venv/bin/pytest -m latency --no-flash --profile=local --port=/dev/cu.usbserial-110 -s --latency-json
```

esptool output streams live during the flash (~10s) — it's not a hang.

**Does the test spew input into my desktop?** `-m sdl` / `-m hid` press buttons
and move axes on a bonded gamepad. Neither SDL nor hidapi **seizes** the device,
so the OS still routes that input to the foreground app — but a gamepad's
buttons/axes do nothing in Finder / Terminal / an editor, so on a dedicated
tester box it's a non-issue. A hard lock (nothing else sees the device) needs
`IOHIDDeviceOpen(kIOHIDOptionsTypeSeizeDevice)`, which on macOS requires running
as **root**; not done here. The Linux rig doesn't seize either.

`-m sdl` runs headless (a game controller needs no macOS permission). If `-m
hid` skips with *"no input reports arrived"*, grant **Input Monitoring** to the
venv Python (System Settings ▸ Privacy & Security ▸ Input Monitoring ▸ **+** ▸
`…/desktop/.venv/bin/python3`).

Write path-valued options with `=` (`--bundle=../x`, not `--bundle ../x`) — a
bare path arg pointing outside `desktop/` makes pytest walk up loading
`conftest.py` files and trip over the Linux-only `../conftest.py`. Or sidestep
it with env vars: `HIL_PORT` / `HIL_PROFILE` / `HIL_BUNDLE` / `HIL_FLASH_PORT`
(all four options fall back to these).

### One-time BLE pairing (only for the `PEERINFO?` check, and later steps)

macOS and Windows hand a bonded BLE-HID device to the OS HID stack — an app
can't cleanly initiate the bond. So pair **once, by hand**. `pair-assist.py`
reads the board over serial and tells you exactly which entry to click, then
waits for the firmware to see the link (`CONN?` — no host BLE API):

```bash
.venv/bin/python pair-assist.py --port=/dev/cu.usbserial-110
.venv/bin/python pair-assist.py --port=/dev/cu.usbserial-110 --clear-bonds  # if it won't pair clean
.venv/bin/python pair-assist.py --port=/dev/cu.usbserial-110 --reconnect    # macOS: after a re-flash
.venv/bin/python pair-assist.py --port=/dev/cu.usbserial-110 --check        # just show state
```

- **First pair** — **macOS**: System Settings ▸ Bluetooth ▸ *Connect* next to
  the name `pair-assist.py` prints. **Windows**: Settings ▸ Bluetooth & devices
  ▸ Add device. (`blueutil` can't do the *first* BLE pair — it can't scan.)
- **After a re-flash that changes the profile** the OS clings to the old bond +
  cached HID descriptor (SDL shows the wrong button/hat count, no input flows).
  On macOS, `--reconnect` fixes it without the GUI: `brew install blueutil`,
  then `pair-assist.py --reconnect` power-cycles Bluetooth and reconnects the
  paired entry — the board's Just Works agent re-bonds with the fresh
  descriptor. (`blueutil --connect` prints `Failed to connect` even when it
  works; `--reconnect` polls `CONN?` for the truth.)

**"I don't see my board, only `ESP32 BLE Gamepad` / `HILpad …`"** —

- `HILpad esp32dev / esp32c3 / esp32s3` is the **reference rig** (a separate Pi
  with its own boards). Not yours.
- `ESP32 BLE Gamepad` is the **library's default name** — a board running stock
  `ESP32-BLE-Gamepad` firmware, *not* `hil_runner`. If that's your board, it
  isn't flashed: `pair-assist.py --check` will say `is not running hil_runner`,
  or `ID?` won't start with `ID hil_runner`.
- A **stale bond** (your board was paired earlier, as a different name/profile)
  makes the host show the old entry and skip a fresh pair. `pair-assist.py
  --clear-bonds` wipes it on the board (`CLEARBONDS`); then **Forget This
  Device** host-side too, and pair again.
- Confirm what your board actually advertises: `NAME?` over serial, or
  `python -m serial.tools.miniterm <port> 115200`.

`test_peer_info_when_connected` skips when the board isn't bonded.

## Remote runs

Deferred. For now a desktop tester is driven **by hand** on the box itself (or
over plain `ssh` if you enable Remote Login / OpenSSH Server — it's just
`pytest` in a venv). A self-hosted CI runner or a tailnet SSH target like the
Pi rig is a later step, once the behavioural layer exists.
