# Desktop tester (macOS / Windows) — serial-only subset

The Linux rig (repo root) asserts against `evdev` / BlueZ / `hidraw`, which is
Linux-only. This directory is the **portable slice**: the checks that ride the
`hil_runner` USB-serial command channel and need no BLE bond and no host HID
stack. It runs on macOS and Windows with nothing but Python + `pyserial`.

It is **not** a fork — it reuses the rig's `hil.serialdev` / `hil.hidraw`
(`../host`) and the checked-in golden descriptors (`../firmware/golden`)
directly. One repo, one source of truth for the protocol and the goldens.

## Status — step 1

| Layer | State |
|---|---|
| `esptool` flashing (`../tester/flash.py`) | ✅ reused as-is, cross-platform |
| Serial protocol client (`hil.serialdev.SerialDev`) | ✅ reused as-is |
| Serial-only assertions (`tests/test_serial_only.py`, `-m serial_only`) | ✅ this step |
| One-time BLE pairing (manual, in OS settings) | 🟢 documented below, not scripted |
| GATT reads (Device Info / PnP / Battery over CoreBluetooth / WinRT) | 🔴 later step |
| Native input read backend (IOKit HID / Windows Raw Input) + behavioural tests | 🔴 later step |
| Remote drive (SSH / CI runner) | 🔴 later — for now, run it by hand (below) |

### What the serial-only subset catches

Firmware regressions in HID **descriptor generation** (`RMAP?` vs
`firmware/golden/<profile>.hiddesc`), **report sizing** (`RSIZE?` vs the 150-byte
buffer ceiling), the **DIS / PnP** config the firmware reports, the **advertised
BLE name** (`NAME?` — ≤ 18 chars, `local` not in the rig namespace), and the
serial protocol round-trips (`PRESS` / `AXIS` / `HAT` / range errors). A laptop
smoke test between full Linux HIL runs.

### What it can't

Anything that needs the host's HID interpretation — button → key code, axis →
ABS code, the descriptor as the **OS** parsed it. That's the behavioural layer;
it needs a per-platform native read backend (a later step).

## Setup

Python 3.10+ and a USB-serial driver for your board's bridge (CP210x / CH34x —
usually built into macOS; vendor VCP on Windows).

```bash
cd desktop
python3 -m venv .venv
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

# two developers sharing BLE space? give yours a name (<= 18 chars):
builder/build.sh --profiles local --name "HILdev clt"
```

`../tester/flash.py` flashes the bundle with `esptool` (the `flashed` fixture
calls it). Or flash by hand / with the rig and pass `--no-flash`. The board
reports its own advertised name over serial — `NAME?`, or
`python -m serial.tools.miniterm <port> 115200`.

## Running

Find the port — macOS `ls /dev/cu.usbserial-*`, Windows `Get-PnpDevice -Class Ports`.

```bash
# flash the local bundle, then run the serial-only subset
.venv/bin/pytest -m serial_only \
    --bundle=../bundles/esp32dev-local-<sha> \
    --profile=local \
    --port=/dev/cu.usbserial-110

# board already has the firmware:
.venv/bin/pytest -m serial_only --no-flash --profile=local --port=/dev/cu.usbserial-110
```

esptool output streams live during the flash (~10s) — it's not a hang.

Write path-valued options with `=` (`--bundle=../x`, not `--bundle ../x`) — a
bare path arg pointing outside `desktop/` makes pytest walk up loading
`conftest.py` files and trip over the Linux-only `../conftest.py`. Or sidestep
it with env vars: `HIL_PORT` / `HIL_PROFILE` / `HIL_BUNDLE` / `HIL_FLASH_PORT`
(all four options fall back to these).

### One-time BLE pairing (only for the `PEERINFO?` check, and later steps)

macOS and Windows hand a bonded BLE-HID device to the OS HID stack — an app
can't cleanly initiate the bond. So pair **once, by hand**:

- Flash a bundle so the board advertises (as `HILdev esp32dev` for `local`).
- **macOS**: System Settings ▸ Bluetooth ▸ *Connect*.
  **Windows**: Settings ▸ Bluetooth & devices ▸ Add device.
- Re-pair when you change profile — the OS caches the HID descriptor against the
  bond.

The suite watches the link from the **firmware** side (`CONN?` over serial), so
no host BLE API is needed for that. `test_peer_info_when_connected` skips when
the board isn't bonded.

## Remote runs

Deferred. For now a desktop tester is driven **by hand** on the box itself (or
over plain `ssh` if you enable Remote Login / OpenSSH Server — it's just
`pytest` in a venv). A self-hosted CI runner or a tailnet SSH target like the
Pi rig is a later step, once the behavioural layer exists.
