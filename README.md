# ESP32-BLE-Gamepad — hardware-in-the-loop test rig

An ESP32 runs the `hil_runner` firmware; this harness drives it over USB serial
and asserts the resulting BLE HID + GATT behaviour on a Linux host. It answers
what the [library](https://github.com/LeeNX/ESP32-BLE-Gamepad)'s compile-only CI
can't: does `press(5)` actually produce one distinct key event on a host, do
axes / hats / special buttons map correctly, does the Device Information / PnP /
battery data reach a GATT client, does the generated HID report descriptor
arrive intact, and how fast do reports get through the air.

The rig is split into two roles so the BLE host can be a small board (a
Raspberry Pi) that can't build firmware in reasonable time:

```
 BUILDER (CI runner / dev machine)          TESTER (Raspberry Pi + ESP32 + BLE)
 ┌────────────────────────────┐   bundle   ┌──────────────────────────────────┐
 │ builder/build.sh:          │  (rsync/   │ tester/test.sh:                  │
 │  pio run  (lib under test) │   CI       │  tester/flash.py  (esptool only) │
 │  -> bundles/<b>-<p>-<sha>/ │  artifact) │  pytest  (pyserial+evdev+bluez   │
 │     *.bin + manifest.json  │──────────►│         +dbus-fast)               │
 └────────────────────────────┘            │   USB─► ESP32 ─BLE─► /dev/input/  │
                                           │  -> results/ junit + bench + svg │
                                           └──────────────────────────────────┘
```

- **builder** needs PlatformIO. `builder/build.sh` compiles `hil_runner`
  against the library under test and writes a **bundle**: the flashable `.bin`
  parts + `manifest.json` (chip, flash offsets, sha256s, lib git sha).
- **tester** needs only `esptool` + the pytest deps (all pure-Python /
  lightweight — fine on a Pi). `tester/test.sh` flashes a bundle and runs the
  suite.
- Command injection is over USB serial, **not** BLE — an independent channel,
  so the harness never depends on the thing under test being up.

The library repo drives this end to end with its `scripts/hil.sh` (build,
push to the tester, run, pull results). One box can be both roles
(`./run.sh` does builder then tester locally).

## Layout

| Path | What |
|---|---|
| `firmware/` | PlatformIO project — `hil_runner` serial-command firmware, `hil_profile.h` layout profiles (see below) |
| `builder/build.sh` `builder/make_bundle.py` | compile → firmware bundle(s) → optional `--push` rsync to the tester. Library path comes from `$HIL_LIB_DIR` (exported from `rig.lib_dir`) |
| `tester/bootstrap-host.sh` (root) `tester/bootstrap.sh` (user) | tester provisioning, split: privileged half (apt / bluetooth / groups / udev) vs unprivileged half (venv / config / health check) |
| `tester/flash.py` `tester/test.sh` | flash a bundle with esptool, run the suite + benchmark, write `results/` |
| `host/conftest.py` `host/hil/` `host/tests/` | the pytest suite. Helpers: `serialdev`, `evdev_utils`, `bluetooth`, `gatt` (DIS/PnP/battery over BlueZ D-Bus), `hidraw` (Feature/Output reports + descriptor), `latency`+`bench`, `sysinfo`, `charts`, `summarize` |
| `hil_config.toml` (+ gitignored `hil_config.local.toml`) | per-machine ports, ssh host, builder board/profile matrix |
| `run.sh` | one-box: build all bundles then flash+test each |
| `.github/workflows/hil.yml` | CI: build job → SSH-to-tester test job |

### Compile profiles (`firmware/include/hil_profile.h`)

| Profile | Layout | Purpose |
|---|---|---|
| `default` | 64 btn, 4 hat, 8 axis (0..32767) | mirrors `TestAll.ino` — known good |
| `signed-axes` | as default, axis min −32767 | signed-axis convention |
| `specials` | 16 btn, 1 hat, 8 axis, 8 special buttons | consumer/desktop special usages |
| `minimal` | 1 btn, 1 axis | smallest possible input report |
| `maxbtn` | 128 btn, no hats/axes | the library's button ceiling |
| `reports` | 16 btn, 2 axis, Output + Feature reports | `setEnableOutputReport` / `setEnableFeatureReport` |

Each profile is a distinct HID report descriptor; the host caches the descriptor
at bond time, so switching profiles on a board makes the old bond stale and the
harness re-pairs automatically (it records `{mac, profile}` per device in
`~/.cache/esp32-hil/state.json`).

## Setup

### Builder (has PlatformIO)

```bash
git clone https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL ~/src/ESP32-BLE-Gamepad-HIL
git clone https://github.com/LeeNX/ESP32-BLE-Gamepad     ~/src/ESP32-BLE-Gamepad
python3 -m venv ~/.venvs/pio && ~/.venvs/pio/bin/pip install platformio
```

`hil_config.local.toml` on the builder only needs `[rig] lib_dir` / `pio` if
they differ from the template, plus `[tester] ssh_host` / `ssh_user` for
`--push`.

### Tester (has the ESP32 + a BLE adapter)

Bootstrap is split so the CI / test user never needs root:

```bash
git clone https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL ~/ESP32-BLE-Gamepad-HIL

# once, by a host admin -- the only step that touches root:
sudo ~/ESP32-BLE-Gamepad-HIL/tester/bootstrap-host.sh --user <ci-user>

# then as that user, NO sudo -- re-run freely after a requirements.txt change:
tester/bootstrap.sh
```

- `tester/bootstrap-host.sh` (root): apt deps (`bluez` + `rfkill` + `upower` +
  build tools), system locale, the `bluetooth` service + rfkill unblock, adds
  `--user` to `dialout` / `input` / `plugdev`, installs the udev rule for the
  DUT's `/dev/hidraw*` node.
- `tester/bootstrap.sh` (unprivileged): the `~/.venvs/hil` venv from
  `tester/requirements.txt`, the `hil_config.local.toml` stub, a health check.
  It **preflights** the privileged bits and points at `bootstrap-host.sh` if
  they're missing. `--with-host` runs the root half via `sudo` first (handy on
  a dev box); `--skip-preflight` bypasses the check.

Still manual:

- **Power**: a Pi 3B+ can't reliably power an ESP32 doing BLE off its own USB —
  brownouts show up as a reset loop that never advertises. Use a **powered USB
  hub** for the ESP32.
- BLE: a built-in Pi adapter works; a USB BT dongle is steadier for a rig.
- `hil_config.local.toml` sets the real `[board.<b>].port`
  (`ls -l /dev/serial/by-id/`).

First run pairs the device automatically (`NoInputNoOutput` agent, Just Works).
To pair by hand: `bluetoothctl` → `scan on`, wait for `HILpad <board>`, then
`pair` / `trust` / `connect` and answer the agent prompt `yes`.

### Local (one box)

Do **both** the Builder and Tester setup on one Linux machine, plug in the
ESP32 (via a powered hub), and `./run.sh` builds, flashes and tests in one go.

**The tester box must be Linux** — the suite asserts on `/dev/input/event*` via
`evdev` and pairs through BlueZ `bluetoothctl`. **macOS can be the builder
only** (`builder/build.sh --push` to a Linux tester); see
[macOS as a tester](#macos-as-a-tester--unsupported--gap-list) for what a macOS
tester port would take.

## Running

```bash
# one box (build + flash + test here)
./run.sh                                       # config defaults, current lib checkout
LIB_REF=some-branch ./run.sh --profiles default
./run.sh --boards esp32dev --profiles "default specials"
./run.sh --profiles default -- -k buttons      # args after -- go to pytest

# split: build on the builder, push, test on the tester
PUSH=1 LIB_REF=some-branch builder/build.sh
tester/test.sh ~/hil-bundles/esp32dev-default-<sha>/ --bench

# pytest directly against a hand-flashed board (no builder needed)
~/.venvs/hil/bin/pytest --board esp32dev --no-flash --port /dev/ttyUSB0
```

Useful pytest options: `--bundle <dir>` (flash a bundle via esptool),
`--no-flash`, `--no-pair`, `--repair` (drop bond + pair fresh), `--profile`,
`--bench` (latency / throughput sweep), `--update-golden` (rewrite the HID
descriptor golden files).

## What it covers

| Area | Notes |
|---|---|
| Buttons | every configured button → one distinct evdev key, one-to-one, in the gamepad key range. `maxbtn` pins the finding that **Linux surfaces only ~79 of 128** buttons for a gamepad-application collection (`BTN_GAMEPAD + n` runs out at `0x17e`) |
| Axes | each axis → exactly one ABS code, monotonic, exact min/centre/max endpoints; `s1`→`ABS_THROTTLE`; `s2` gets no distinct code (strict xfail); negative rail via `signed-axes` |
| Hats | 8 directions + centre; Linux creates only `ABS_HAT0` and this library emits hat fields reversed so the working hat is the highest index — both pinned as strict xfails |
| Special buttons | start/select/menu/home/back/vol± → one event each, across every input node the DUT exposes |
| HID descriptor | the descriptor the library generated (`getHidReportDescriptor()`) == its reported size == the copy the **kernel received over GATT** == a checked-in golden per profile |
| Device Information | model / serial / fw / hw / sw revision + manufacturer, read over GATT, match the firmware config |
| PnP ID | `0x2A50` vendor / product / version match `setVid` / `setPid` / `setGuidVersion` |
| Battery | `setBatteryLevel()` via raw `0x2A19`, BlueZ `Battery1` D-Bus, and `upower` where installed; nothing in `/sys/class/power_supply` (BLE Battery Service, not a HID battery usage). `setPowerStateAll()` bitfield via `0x2A1A` |
| Feature / Output reports | `reports` profile — Feature Report both directions (`setFeatureBuffer` ↔ `HIDIOCGFEATURE`, `HIDIOCSFEATURE` ↔ `getFeatureBuffer`); Output Report host→device via `write(/dev/hidraw*)` → `getOutputBuffer` |
| Latency / throughput | `--bench`, see below |

## Benchmarking (`--bench`)

`test_latency.py` runs one sweep per flashed profile via `host/hil/bench.py`,
recording a JSON blob with an environment fingerprint (distro / kernel / arch /
BlueZ version, load average + CPU temp/freq sampled around the measurement):

- `ping_rtt` — serial PING/PONG baseline (USB-serial + parse overhead).
- `input_latency` — per-event latency for button / axis / hat, host-side, split
  into `t_evdev − t_serial_reply` (BLE + host stack) and `t_evdev − t_serial_write`
  (end to end). p50 / p90 / p99 / max, plus a dropped count.
- `clean_rate` — fastest paced rate at which **every** distinct state change
  still reaches the host (≥95% delivery).
- `burst` — `BURST` a few hundred toggles at decreasing gaps; shows where
  NimBLE's TX queue overflows and the ESP32 starts dropping before air.
- `PEERINFO?` connection interval + MTU, `RSIZE?` sizes.

`tester/test.sh` then runs `python -m hil.charts results/` → `bench-table.md`
plus three SVGs (latency vs report size, clean rate per profile, latency
distribution). The pytest gates are deliberately loose — the recorded JSON is
the deliverable.

### Findings (esp32dev, Raspberry Pi 3B+, kernel 6.18, BlueZ 5.82)

- Single button press → host in **~18.6 ms** median (p99 ~67), **0 dropped**
  across 200 paced presses per profile.
- **Latency is flat vs HID report size** (3–28 B).
- **Connection interval 48.75 ms** on every profile — the library doesn't
  request a fast one. It bounds *latency*, not paced *rate*: NimBLE sends
  several packets per connection event, so paced input delivers ~100% past
  80 Hz (here it's the serial channel, not BLE, that runs out first).
- **Unpaced `sendReport()` bursts overflow and drop silently** — at gap=0 only
  ~2% of a 500-report burst survives. Don't call `sendReport()` faster than you
  can transmit.
- **Feature Report off-by-one**: the last byte of `setFeatureReportLength()`
  doesn't round-trip (host reads back length−1 data + a trailing zero) — pinned
  as a strict xfail (`test_feature_full_length_roundtrips`).

## Serial protocol (`firmware/src/hil_runner.cpp`)

115200 8N1, one `\n`-terminated command per line, one reply line each. The boot
banner and any debug lines are skipped by the host.

| Command | Reply |
|---|---|
| `PING` | `PONG` |
| `ID?` | `ID hil_runner profile=… board=… built=…` |
| `CONFIG?` | `CONFIG buttons=… hats=… axes=… special=… axesMin=… axesMax=… vid=… pid=… ver=… reportId=… feat=… out=… profile=…` |
| `DIS?` | `DIS model=… serial=… fw=… hw=… sw=… mfr=…` — the DIS strings the firmware configured |
| `PNP?` | `PNP vidsrc=1 vid=… pid=… ver=…` |
| `RSIZE?` | `RSIZE report=<n> descriptor=<n>` |
| `RMAP?` | `RMAP <len> <hex>` — the generated HID report descriptor bytes |
| `PEERINFO?` | `PEER interval=<1.25ms units> latency=<n> timeout=<10ms units> mtu=<n>` / `ERR notconnected` |
| `BEGIN` | `OK` — handshake only; `bleGamepad.begin()` already ran in `setup()` |
| `CONN?` | `CONN 0` / `CONN 1` |
| `PRESS <n>` / `RELEASE <n>` | `OK` / `ERR range` |
| `TPRESS <n>` / `TRELEASE <n>` | `T <micros>` — like PRESS/RELEASE, replies `micros()` captured just before `sendReport()` |
| `BURST <btn> <count> <gap_us>` | `BURST OK <count> <elapsed_us>` — `count` ≤ 2000 |
| `SPECIAL PRESS\|RELEASE <0..7>` | `OK` / `ERR disabled` |
| `AXIS <x\|y\|z\|rx\|ry\|rz\|s1\|s2> <int16>` | `OK` / `ERR disabled` |
| `HAT <1..4> <0..8>` | `OK` / `ERR disabled` |
| `BATTERY <0..100>` | `OK` |
| `POWERSTATE <info> <discharging> <charging> <level>` | `OK` — 2-bit fields for `setPowerStateAll()` |
| `FEATURE?` | `FEATURE recv=0\|1 <hex>` — `isFeatureReceived()` + `getFeatureBuffer()` |
| `FEATURE SET <hex>` | `OK` — `setFeatureBuffer()` |
| `OUTPUT?` | `OUTPUT recv=0\|1 <hex>` — `isOutputReceived()` + `getOutputBuffer()` |
| `RESET` | `OK` — zero buttons, axes, hats |

`begin()` runs in `setup()` (like `TestAll.ino`): calling it lazily from
`loop()` on the BEGIN command wedged the NimBLE server task on the classic
ESP32. So the firmware always advertises once booted; the two boards carry
distinct names (`HILpad esp32dev` / `HILpad esp32c3`) so the harness bonds the
right one. The name is kept short — a longer one didn't fit the legacy BLE
advertising packet and NimBLE silently truncated it.

Hand-test: `python3 -m serial.tools.miniterm <port> 115200`, type `PING`,
`CONFIG?`, `CONN?`, `PRESS 5`, `AXIS x 16000`, `HAT 4 3`.

## How pairing works

BlueZ needs a registered agent to confirm even a no-MITM "Just Works" pairing,
and an agent only lives as long as the `bluetoothctl` that registered it. BlueZ
5.82 additionally drops discovered-but-unconnected devices the moment scanning
stops, and its `NoInputNoOutput` agent still prompts `[agent] Accept pairing
(yes/no)`. So `host/hil/bluetooth.py` keeps one long-lived `bluetoothctl`
session (`BtCtl`) open for the whole run: it holds the agent, keeps discovery
running, auto-answers any `(yes/no)` prompt with `yes`, and drops any bond it
has no state record for (unknown provenance → re-pair).

## ESP32-C3 serial bridge

`hil_runner` writes the command protocol to `Serial`, and on the C3 with this
rig's build (`ARDUINO_USB_CDC_ON_BOOT` unset → `0`) `Serial` is **UART0**
(`GPIO21` TX / `GPIO20` RX), *not* the USB-C port. The USB-C connector on a C3 is
the native USB-Serial/JTAG peripheral — great for flashing, but it carries no
`hil_runner` I/O in this build, and even with CDC-on-boot it re-enumerates on
every chip reset and `serial.Serial()` can wedge on the half-open handle.

So the C3 wants **two interfaces**: flash over USB-C, talk over an external
3.3 V USB-UART bridge on UART0. The harness supports a `flash_port` distinct
from `port`:

```toml
[board.esp32c3]
port       = "/dev/serial/by-id/usb-<CP2102-or-CH340-bridge>-if00-port0"   # UART0
flash_port = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_…-if00"  # USB-C
```

(`flash_port` defaults to `port`; `--flash-port` / `HIL_FLASH_PORT` override.)

### Wiring — ESP32-C3 SuperMini

The SuperMini has **no onboard USB-UART chip**, so an external adapter set to
**3.3 V logic** (the C3 is not 5 V tolerant) is mandatory:

| USB-UART adapter | C3 SuperMini | |
|---|---|---|
| `GND` | `GND` | common ground is required |
| `TX` (adapter → C3) | `GPIO20` (U0RXD) | pin nearest the USB-C shell, one side |
| `RX` (adapter ← C3) | `GPIO21` (U0TXD) | pin nearest the USB-C shell, other side |
| `VCC` | **leave unconnected** | board is powered + flashed via USB-C |

## Known mapping quirks the tests pin down

- **Buttons**: kernel `hid-input` maps a gamepad-application Button usage to
  `BTN_GAMEPAD + n`, running out of the named key block at `0x17e` (~79 codes).
  Tests don't hard-code per-button codes; they assert the sweep is one-to-one
  and in range. Buttons past ~79 (`maxbtn`) get no usable code — pinned.
- **Axes**: `x y z rx ry rz` → `ABS_X..ABS_RZ`, `s1` → `ABS_THROTTLE`. A second
  bare `Usage(Slider)` (`s2`) gets no distinct code — strict xfail.
- **Hats**: only `ABS_HAT0` exists, driven by the *highest* firmware hat index
  because the library emits hat fields reversed — strict xfail.
- **`setAxes()` arg order**: the firmware uses per-axis setters
  (`setX/setRX/…`) to avoid `setAxes()`'s positional quirk. See the library's
  `IndividualAxes` example.

## CI

`.github/workflows/hil.yml` runs entirely on **GitHub-hosted runners** — no
self-hosted runner, no inbound ports on your network:

- **build** — `pip install platformio`, `builder/build.sh`, upload the bundles.
- **hil-test** — brings up an **ephemeral Tailscale node** for the job
  (`tailscale/github-action`), `rsync`s the checked-out harness code (so the
  tester runs the same ref) **and** the bundles to the tester over the tailnet,
  `ssh`es in to run `tester/test.sh --bench` per bundle, pulls `results/` back,
  publishes the JUnit report.

The tester is a plain **SSH target** on the tailnet, not a runner — nothing
untrusted executes on it directly, and its own `hil_config.local.toml` (real
serial ports) is never overwritten.

### Setup

1. **Tailscale on the tester**: `tester/bootstrap-host.sh` installs it; then
   `sudo tailscale up` (tag it, e.g. `--advertise-tags=tag:hil-rig`). Note its
   MagicDNS name.
2. **Tailscale ACL**: allow `tag:ci` → the tester on `tcp:22`, e.g.
   ```jsonc
   "acls": [
     { "action": "accept", "src": ["tag:ci"], "dst": ["tag:hil-rig:22"] }
   ],
   "tagOwners": { "tag:ci": ["autogroup:admin"], "tag:hil-rig": ["autogroup:admin"] }
   ```
3. **OAuth client** (Tailscale admin → Settings → OAuth clients): scope
   *Auth Keys* (write), tag `tag:ci`. → `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET`.
4. **Repo secrets**: `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`, `HIL_TESTER_HOST`
   (the MagicDNS name), `HIL_TESTER_USER`, `HIL_TESTER_SSH_KEY` (a
   passphrase-less key in the tester user's `~/.ssh/authorized_keys`).

Triggers: push to `main` / `hil-*`, manual dispatch (with `lib_repo` / `lib_ref`
inputs), or `repository_dispatch` type `hil` from the library repo. A
`concurrency` group serialises runs — there's one physical rig.

**Untrusted code**: the build job compiles whatever library ref it's handed and
the test job flashes it to hardware. Keep the triggers to same-repo pushes +
manual dispatch; don't run it automatically on PRs from forks.

## macOS as a tester (unsupported — gap list)

Flashing and the serial command channel work on macOS (`esptool` + `pyserial`
are cross-platform; point `[board.<b>].port` at `/dev/cu.usbserial-*`). The two
things the suite needs from the OS — initiating the BLE bond and reading HID
events as ground truth — have no macOS implementation:

1. **A CoreBluetooth pairing backend** to replace BlueZ `bluetoothctl`
   (`host/hil/bluetooth.py`). *Blocker:* macOS hands a BLE-HID device's GATT
   service to the system HID stack, so an app can't trigger pairing; it stays a
   one-time manual step in System Settings, with the suite run `--no-pair`.
2. **An IOHIDManager read backend** to replace `host/hil/evdev_utils.py`. Needs
   `pyobjc-framework-IOKit` and the **Input Monitoring** TCC permission (granted
   by hand or MDM) for the python running pytest.
3. **macOS ground-truth mapping tables.** Every `test_*.py` asserts against
   Linux `hid-input` codes; IOKit exposes raw HID usages instead, so the
   expected values must be re-characterised and kept as a per-platform table.
4. **Backend selection + a macOS bootstrap.** `conftest.py` fixtures and
   `tester/requirements.txt` are evdev-hardwired; they'd dispatch on
   `sys.platform`.
5. **CI**: a headless Mac runner needs a logged-in GUI session for BLE plus
   pre-provisioned TCC grants — more friction than the Linux path.
