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

```text
 BUILDER (CI runner / dev machine)          TESTER (Raspberry Pi + ESP32 + BLE)
 ┌────────────────────────────┐   bundle   ┌──────────────────────────────────┐
 │ builder/build.sh:          │  (rsync/   │ tester/test.sh:                  │
 │  pio run  (lib under test) │   CI       │  tester/flash.py  (esptool only) │
 │  -> bundles/<b>-<p>-<sha>/ │  artifact) │  pytest  (pyserial+evdev+bluez   │
 │     *.bin + manifest.json  │───────────►│         +dbus-fast)              │
 └────────────────────────────┘            │   USB─► ESP32 ─BLE─► /dev/input/ │
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
| `tester/flash.py` `tester/test.sh` | flash a bundle with esptool, run the suite + benchmark, write `results/`; SKIPs a board the tester doesn't have |
| `tester/test-all.sh` | loop `tester/test.sh` over every bundle in `~/hil-bundles`, one retry each (what CI runs); `--by-board` runs the boards as parallel lanes (functional only) |
| `tester/rig-lock.sh` `tester/rig-status.sh` `host/hil/riglock.py` | one-rig `flock` + run-status file — serialise CI and local runs; `rig-status.sh` shows who/what is running (see [CI](#rig-lock--status)) |
| `host/conftest.py` `host/hil/` `host/tests/` | the pytest suite. Helpers: `serialdev`, `evdev_utils`, `bluetooth`, `gatt` (DIS/PnP/battery over BlueZ D-Bus), `hidraw` (Feature/Output reports + descriptor), `latency`+`bench`, `sysinfo`, `detect` (present boards), `charts`, `summarize` |
| `hil_config.toml` (+ gitignored `hil_config.local.toml`) | per-machine ports, ssh host, builder board/profile matrix, per-board `enabled` |
| `run.sh` | one-box: build all bundles then flash+test each |
| `scripts/release.sh` `scripts/make-release-artifacts.sh` | cut a rig release (`VERSION` + `CHANGELOG.md` → tag → `release.yml`); see [RELEASE.md](RELEASE.md) |
| `.github/workflows/hil.yml` `release.yml` | CI: build → SSH-to-tester test; tag → firmware/suite release |

### Compile profiles (`firmware/include/hil_profile.h`)

| Profile | Layout | Purpose |
|---|---|---|
| `default` | 64 btn, 4 hat, 8 axis (0..32767) | mirrors `TestAll.ino` — known good |
| `signed-axes` | as default, axis min −32767 | signed-axis convention |
| `specials` | 16 btn, 1 hat, 8 axis, 8 special buttons | consumer/desktop special usages |
| `minimal` | 1 btn, 1 axis | smallest possible input report |
| `maxbtn` | 128 btn, no hats/axes | the library's button ceiling |
| `reports` | 16 btn, 2 axis, Output + Feature reports | `setEnableOutputReport` / `setEnableFeatureReport` |
| `local` | 4 btn, 1 hat, 2 axis | **ad-hoc, not built by CI** — for local developer smoke tests ([`desktop/`](desktop/)). Advertises as `HILdev <board>`, not `HILpad <board>`, so a dev board doesn't clash with the rig |

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
[macOS as a tester](#macos-as-a-tester-unsupported--gap-list) for what a macOS
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
the deliverable. A committed snapshot lives in [`docs/bench/`](docs/bench/).

`--bench-quick` (with `--bench`) runs a shorter sweep — n=40, 3 gap values,
~2 min vs ~6 — for a fast check.

The `bench-table.md` **links** column is how many BLE connections the adapter
was carrying during that sweep (normally `1`); `bench.py` records it as
`adapter_links` so a number taken while another bond lingered isn't mistaken
for a clean solo measurement.

### Findings (all 3 boards × 6 profiles, Raspberry Pi 3B+, kernel 6.18, BlueZ 5.82)

- Single button press → host in **~18.6 ms** median on **every board and
  profile** — `esp32dev`, `esp32c3`, `esp32s3` are indistinguishable at p50.
  **0 dropped** across 200 paced presses per profile.
- **Latency is flat vs HID report size** (3–28 B) and vs chip. p99 (~20–68 ms)
  is just connection-interval jitter — one 48.75 ms interval — and swings run
  to run with where the sample lands; p50 is the signal.
- **Connection interval 48.75 ms, MTU 255** on every board/profile — the
  library doesn't request a fast one. It bounds *latency*, not paced *rate*:
  NimBLE sends several packets per connection event, so paced input delivers
  ~100% at **80–133 Hz** (here it's the serial / bridge channel, not BLE, that
  runs out first).
- **Unpaced `sendReport()` bursts overflow and drop silently** — at gap=0 only
  ~2% of a 500-report burst survives. Don't call `sendReport()` faster than you
  can transmit.
- **Feature Report off-by-one**: the last byte of `setFeatureReportLength()`
  doesn't round-trip (host reads back length−1 data + a trailing zero) — pinned
  as a strict xfail (`test_feature_full_length_roundtrips`).
- **Rig note**: the C3/S3 external USB-UART bridges drop a byte occasionally
  under the burst sweep — ~1 `--bench` run in 5 needed a retry (`SerialDev`
  retries `command()` once; CI retries a failed `--bench`). The functional
  suite is solid on all three.

## Serial protocol (`firmware/src/hil_runner.cpp`)

115200 8N1, one `\n`-terminated command per line, one reply line each. The boot
banner and any debug lines are skipped by the host.

| Command | Reply |
|---|---|
| `PING` | `PONG` |
| `ID?` | `ID hil_runner profile=… board=… built=…` |
| `NAME?` | `NAME <advertised BLE name>` — `getDeviceName()`; `HILpad <board>`, or `HILdev <board>` / a `-D HIL_DEVICE_NAME` override for the `local` profile |
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
ESP32. So the firmware always advertises once booted; each board carries a
distinct name (`HILpad esp32dev` / `esp32c3` / `esp32s3`) so the harness bonds
the right one. The name is set at build time (`HIL_DEVICE_NAME` in
`hil_profile.h`) and reported live over serial (`NAME?`). Keep it **≤ 18
chars**: it shares the 31-byte legacy advertising packet with the flags,
appearance and HID service UUID, and NimBLE drops the service UUID (then the
name) once it overruns. The `local` profile defaults to `HILdev <board>`, and
`builder/build.sh --name "…"` overrides it — so a developer's board never
collides with the reference rig in a shared BLE space.

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

## ESP32-S3 dual-USB-C setup

Same underlying story as the C3 — `esp32-s3-devkitc-1` sets `ARDUINO_USB_MODE=1`
but not `ARDUINO_USB_CDC_ON_BOOT`, so `Serial` (hil_runner's command protocol)
is **UART0**, not the native USB port — but boards like the **ESP32-S3-DevKitC-1**
that expose **two USB-C connectors** already have the UART-bridge half of that
story built in, no soldering required:

| Port (silkscreen) | Interface | Use as |
|---|---|---|
| **"USB"** | native USB-OTG (the S3's built-in USB peripheral) | `flash_port` |
| **"UART"** | onboard CP2102/CH340 bridge → UART0 | `port` |

```toml
[board.esp32s3]
port       = "/dev/serial/by-id/usb-<CP2102-or-CH340-bridge>-if00-port0"  # "UART" port
flash_port = "/dev/serial/by-id/usb-Espressif…-if00"                     # "USB" port
```

Plug **both** cables into the powered hub, `ls -l /dev/serial/by-id/` to tell
them apart (the native port identifies as an Espressif device; the bridge as a
Silicon Labs/CP210x or CH340), fill in both paths, and it behaves exactly like
`esp32dev` — no `flash_port`/`port` juggling caveats beyond setting them once.
Native-USB flashing is still capped at 115200 (same flakiness as the C3 —
`tester/flash.py`'s `SLOW_CHIPS`).

A single-USB-C S3 board (no separate UART bridge) is the C3 situation: it needs
an external 3.3 V USB-UART adapter on UART0 — check your board's pinout for the
`U0TXD`/`U0RXD` pins (not necessarily GPIO43/44; that's DevKitC-1-specific).

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

Two workflows:

- **`.github/workflows/lint.yml`** — formatting + linting (ruff, shellcheck,
  markdownlint, taplo, actionlint) via `pre-commit`. Runs on every push and PR,
  no hardware. See [CONTRIBUTING.md](CONTRIBUTING.md).
- **`.github/workflows/hil.yml`** — the hardware suite, below.

`.github/workflows/hil.yml` runs entirely on **GitHub-hosted runners** — no
self-hosted runner, no inbound ports on your network:

- **build** — `pip install platformio`, `builder/build.sh`, upload the bundles.
  The **full** `board × profile` matrix is built (`esp32dev` + `esp32c3` + `esp32s3`).
- **hil-test** — brings up an **ephemeral Tailscale node** for the job
  (`tailscale/github-action`), `rsync`s the bundles to the tester over the
  tailnet, `ssh`es in to **`git reset --hard`** the tester's own checkout to the
  rig commit under test (`git clean -ffdx` keeps only the gitignored
  `hil_config.local.toml`, so the checkout never drifts), runs
  `tester/test-all.sh --bench` (flash + test every bundle, one retry each), pulls
  `results/` back (even on failure), publishes the JUnit report.

### Focused re-runs

`workflow_dispatch` (Actions tab → **HIL** → Run workflow) takes, besides
`lib_repo` / `lib_ref`:

| input | effect |
|---|---|
| `boards` | space-separated subset to build + test (blank = all three) |
| `profiles` | space-separated profile subset (blank = all six) |
| `test_filter` | a pytest `-k` expression, e.g. `feature_report` or `battery or descriptor` (blank = whole suite) |

Narrowing `boards` / `profiles` narrows the build matrix, and only the built
bundles are pushed, so the flash + test set shrinks with it. So
`boards=esp32s3`, `profiles=reports`, `test_filter=feature_report` flashes one
bundle and runs a handful of tests (~10 min) instead of the full ~80-min sweep —
the fast path for chasing a single red test. Locally the same:
`HIL_TEST_FILTER='battery or descriptor' tester/test-all.sh --bench`, or just
`tester/test.sh <bundle> -k battery`.

### Parallel functional runs (`--by-board`)

`tester/test-all.sh --by-board` runs one **lane per board** concurrently — each
lane flashes + tests its own profiles sequentially, but the boards overlap. On
the 3-board reference rig the full 18-bundle functional matrix runs in
**~12 min** (measured, `-k "buttons or descriptor"`) versus ~35 min sequential —
about 3x, bounded by the slowest board's lane.

Only the timing-insensitive checks parallelise. `--by-board` **refuses
`--bench`**: the latency / throughput sweep stays sequential and as close to solo
as possible (with peers connected `clean_rate` drops ~25% — the `bench-table.md`
**links** column flags it). Run bench as its own pass.

Why it's safe: the boards have independent serial channels and evdev nodes, and
one BLE adapter carries three concurrent *functional* HID streams with zero
dropped events (a 25-iteration 3-board soak, ~3900 button cycles, was clean).
`conftest.py` serialises the two adapter-global operations with an `flock`:
`state.json` writes, and pairing — the `BtCtl` session (one `bluetoothctl` agent)
lives entirely inside the pair lock, so lanes never run two agents at once.

Not wired into `hil.yml` yet — prove it on your own rig first
(`tester/rig-lock.sh -- tester/test-all.sh --by-board`).

### Rig lock / status

One physical rig, so every run — CI **and** local (`run.sh`, `tester/test.sh`,
`tester/test-all.sh`) — takes an `flock` on
`~/.cache/esp32-hil/rig.lock` first. A second run **waits up to ~45 min**, then
fails with the current holder's identity (who / what / commit / CI run URL). CI
also keeps its `concurrency: hil-rig` group (a cheap CI-vs-CI guard); the lock is
what stops CI and a local run from stomping each other (the `git reset --hard` on
the tester checkout is the real hazard).

```sh
ssh <tester> ESP32-BLE-Gamepad-HIL/tester/rig-status.sh   # who/what is running now
tester/test.sh <bundle> --no-wait                          # fail immediately if busy
tester/test.sh <bundle> --wait 300                         # give up after 5 min
```

flock releases automatically when the holder dies — there's no stale lockfile. If
a holder wedged and `rig-status.sh` shows its pid `DEAD`, clear it with
`rm ~/.cache/esp32-hil/rig.lock` (or `flock -u`).

### Which boards run

The build matrix is fixed, but a tester only flashes the boards it actually has.
`host/hil/detect.py` decides: a board runs when it's `enabled` (default true;
`[board.<b>] enabled = false` opts out), its `port` / `flash_port` are set (not
`CHANGE-ME`), and the device node exists. `tester/test.sh` **SKIPs** a bundle
whose board isn't present — a `SKIP` line in `results/run-verdicts.md`, exit 0,
not a failure. So `esp32c3` and `esp32s3` ship with their ports still
`CHANGE-ME` (build in CI, skip on a tester until you fill in a real board's —
see [ESP32-C3 serial bridge](#esp32-c3-serial-bridge) /
[ESP32-S3 dual-USB-C setup](#esp32-s3-dual-usb-c-setup)), and a newly-wired
board starts running with no CI change. All three are verified green on the
reference rig (Raspberry Pi 3B+).

```bash
PYTHONPATH=host python3 -m hil.detect            # table of present / absent + why
PYTHONPATH=host python3 -m hil.detect --json
```

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
5. **Forks only** — set repo **variable** `HIL_RIG_ENABLED=true`. `hil.yml` /
   `release.yml` run unconditionally in `LeeNX/ESP32-BLE-Gamepad-HIL`; in a fork
   they skip until this is set, so a fork with no tester wired shows a clean
   skipped run rather than a red one on the missing Tailscale secret.
6. **Dispatch token** — this lives on the repo that *triggers* your rig, not the
   rig repo. See [The dispatch token](#the-dispatch-token) below.

#### The dispatch token

`hil.yml` / `release.yml` here are driven by a `repository_dispatch` (or a manual
dispatch) from a **library** repo — a fork of `ESP32-BLE-Gamepad` running its
`.github/workflows/hil.yml`. That repo authenticates with a **`HIL_DISPATCH_TOKEN`**
secret it holds: a PAT for **your** rig repo with **Contents: write** (POST the
dispatch) + **Actions: read** (poll the run).

Create it — fine-grained PAT, by someone with write on the rig repo:

1. github.com → your avatar → **Settings** → **Developer settings** → **Personal
   access tokens** → **Fine-grained tokens** → **Generate new token**
2. **Resource owner**: your rig repo's owner (if an org, approve the token in its
   settings afterwards)
3. **Repository access** → *Only select repositories* → your
   `ESP32-BLE-Gamepad-HIL` fork
4. **Permissions → Repository permissions**: **Contents** → *Read and write*;
   **Actions** → *Read-only* (*Metadata: Read-only* is added automatically)
5. Set an **expiration** you'll rotate before — an expired token fails the
   library's dispatch step with `HIL_DISPATCH_TOKEN … is not set`
6. **Generate token**, copy it

Add it as an **Actions secret** named `HIL_DISPATCH_TOKEN` on the library repo
(its Settings → Secrets and variables → Actions → Secrets). Classic-PAT
alternative: **Generate new token (classic)** with the `repo` scope — broader
than needed; prefer fine-grained.

The library's `hil.yml` hardcodes the rig it dispatches to
(`RIG_REPO: LeeNX/ESP32-BLE-Gamepad-HIL`) — point that at your fork. `release.yml`'s
firmware-attach job already reads `vars.HIL_RIG_REPO` / `HIL_RIG_REF`.

**Which library ref gets built** — `hil.yml` and `release.yml` resolve it in this
order: an explicit `lib_repo` / `lib_ref` dispatch input → the
`repository_dispatch` payload (the library repo passes the ref under test) →
repo **variables** `HIL_LIB_REPO` / `HIL_LIB_REF` → the built-in
`LeeNX/ESP32-BLE-Gamepad` @ `master`. The variables are an escape hatch for
pinning a fork/branch — e.g. while a library change `hil_runner` needs is still
unmerged.

Triggers: push to `main` / `hil-*`, manual dispatch (with `lib_repo` / `lib_ref`
inputs), or `repository_dispatch` type `hil` from the library repo. A
`concurrency` group serialises runs — there's one physical rig.

**Untrusted code**: the build job compiles whatever library ref it's handed and
the test job flashes it to hardware. Keep the triggers to same-repo pushes +
manual dispatch; don't run it automatically on PRs from forks.

## Releases

The rig is versioned independently of the library — [SemVer](https://semver.org/)
tags, a [CHANGELOG](CHANGELOG.md), and a GitHub Release per tag carrying:

- **`…-firmware-vX.Y.Z.tar.gz`** — the whole `board × profile` bundle set
  (prebuilt `.bin`s + manifests), `golden/*.hiddesc`, and `index.json` (rig +
  library commit). Flash it and run the suite with no PlatformIO — see
  [REPRODUCE.md](REPRODUCE.md).
- **`…-suite-vX.Y.Z.tar.gz`** — a standalone copy of the pytest suite.

Cut one with `scripts/release.sh X.Y.Z` (see [RELEASE.md](RELEASE.md)); the
`v*` tag push drives `.github/workflows/release.yml`. The library's own release
workflow rebuilds the same firmware set from the pinned rig ref and attaches it
to the library release too, so a library version ships the firmware it was
HIL-validated with.

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

## Cross-platform tester (macOS / Windows) — TODO, investigate

The gap list above has one item that actually matters — **the ground-truth read
layer**. Flashing, the serial protocol, and the firmware-id check are already
cross-platform. Two things worth scoping:

### 1. A serial-only subset that runs anywhere `pyserial` does

**Step 1 done — lives in [`desktop/`](desktop/).** `desktop/` reuses this repo's
`hil.serialdev` / `hil.hidraw` and the `firmware/golden/` files directly (not a
fork) and adds `desktop/tests/test_serial_only.py` (`-m serial_only`). Verified
green on macOS against a local `esp32dev` on the `local` profile (`HILdev
<board>` — a dev board that doesn't clash with the rig). The rest of this
subsection is the original scoping notes.

The serial command channel needs no BLE and no host HID stack. It can already
verify:

- the board is alive and running the **expected firmware/profile**
  (`CONFIG?` — the same `firmware_id()` check `conftest.py::dut` does),
- the serial protocol round-trips (`PING`, `PRESS`, `AXIS`, `HAT`),
- connection parameters and report sizing (`PEERINFO?`, `RSIZE?`),
- the descriptor the **library** generated matches its own reported size and the
  checked-in golden (`getHidReportDescriptor()` vs
  `firmware/golden/<profile>.hiddesc` — the DUT-side half of the descriptor
  test, minus the "what the kernel received over GATT" half).

Mark those assertions `@pytest.mark.serial_only`; `pytest -m serial_only` then
runs on Windows (`COM*`) and macOS (`/dev/cu.usbserial-*`), with pairing done by
hand once in the OS BLE settings and the run in `--no-pair`. A helper can poll
`CONN?` and prompt "pair the board now, waiting…" — no CoreBluetooth /
`Windows.Devices.Bluetooth` code needed.

Catches: firmware regressions in descriptor generation, report sizing, the
serial protocol, connection params — a laptop smoke test between full Linux HIL
runs. Can't catch: anything that needs the host's HID interpretation.

A multi-board serial-only run would reuse the `--by-board` shape (one process per
board, independent once each has its port + manual bond). The one shared file,
`state.json`, is `fcntl.flock`-guarded — POSIX, so macOS is fine; Windows would
need `msvcrt.locking` / `portalocker` or per-board state files.

### 2. What replaces evdev on each platform

To run the **behavioural** assertions (press → one distinct event, axis → one
ABS code, …) off-Linux you need a host read path. **Prefer the host OS's native
input system** — the same one SDL's per-platform backends use. The point of
these tests is "what does a real app on this OS see?", and only the native stack
answers that: it applies the OS's own HID parsing, its usage→control mapping,
and any platform quirks we'd want a test to pin (the way the Linux suite pins
evdev's ~79-button ceiling, `ABS_HAT0`-only, and the reversed-hat quirk).

| Platform | Native input system (target) | SDL backend it mirrors | Python route |
|---|---|---|---|
| Linux (current) | evdev — `/dev/input/event*`, OS-decoded events | `linux/SDL_evdev` | `python-evdev` (`host/hil/evdev_utils.py`) |
| macOS | IOKit HID — `IOHIDManager`, HID elements decoded by usage-page/usage | `darwin/SDL_iokitjoystick` | `pyobjc-framework-IOKit`; **Input Monitoring** TCC grant for the pytest process |
| Windows | Raw Input + `hid.dll` preparsed data (`HidP_GetCaps` / `HidP_GetUsages`); `Windows.Gaming.Input` for the higher-level gamepad view | `SDL_rawinputjoystick`, `SDL_windows_gaming_input` | `pywinusb` or `ctypes` → `hid.dll`; WGI via `winrt` |

Each native backend needs its own per-platform expected-value table (gap-list
item 3), because each OS exposes the device its own way — that's the cost of
testing the real thing, and it's the same table SDL maintains as its mapping DB.

**hidapi as the last resort.** `hidapi` (what SDL wraps as `SDL_hidapi`) reads
**raw HID input reports** straight off the device on all three OSes
(hidraw / IOHIDManager / `hid.dll` underneath), bypassing the OS's input
interpretation. Parse those against the descriptor golden
(`firmware/golden/<profile>.hiddesc`) and you get button/axis/hat state with no
per-platform table — but you're then testing **the descriptor + firmware**, not
what the OS makes of them. Reach for it only to:

- bootstrap a platform before its native backend is written, or
- cover a corner case the native API can't observe (a field the OS collapses or
  hides), as an explicitly-marked complement to the native assertions.

CI on macOS/Windows stays hard (item 5): both want a logged-in GUI session for
BLE; macOS needs the Input Monitoring grant pre-provisioned. A self-hosted
runner, or a "run this locally before a release" checklist item, is more
realistic than hosted CI.
