# Changelog

All notable changes to this rig — the `hil_runner` serial protocol, the firmware
bundle format, and the test-suite semantics — are recorded here. The format is
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are
[SemVer](https://semver.org/). The rig is versioned independently of the
[ESP32-BLE-Gamepad](https://github.com/LeeNX/ESP32-BLE-Gamepad) library it
tests — a rig release pins nothing about the library, it just says "this is how
the rig behaved on this date".

What a bump means:

- **major** — a breaking change to the serial protocol or the bundle/manifest
  format (an older tester can't run a newer bundle, or vice versa).
- **minor** — new profiles, new tests, new `test.sh` / `detect.py`
  behaviour, new config keys (backwards compatible).
- **patch** — fixes and doc changes that don't move any of the above.

## [Unreleased]

## [0.1.0] — 2026-09-05

First tagged rig. Baseline (predating this release): the builder/tester split,
`bootstrap-host.sh` + `bootstrap.sh`, the six compile profiles (`default`,
`signed-axes`, `specials`, `minimal`, `maxbtn`, `reports`), the BLE-HID + GATT +
descriptor + latency suite, and the Tailscale-based CI. Plus:

### Added

- `VERSION`, this changelog, `scripts/release.sh`, `.github/workflows/release.yml`
  and `RELEASE.md` — the rig now cuts versioned GitHub releases carrying a
  reproducible firmware-bundle set (`firmware-bundles/` + `golden/` + `index.json`)
  and a standalone copy of the test suite.
- `host/hil/detect.py` — reports which configured boards are physically
  present and `enabled`. `tester/test.sh` now **skips** (exit 0, `SKIP` verdict)
  a bundle whose board is absent or disabled instead of failing the run.
- `hil_config.toml`: `[board.<b>] enabled` (default `true`) — set `false` to keep
  a board in the build matrix but out of the flash/test loop.
- `host/hil/config.py --boards` lists the configured board names.
- `esp32s3` board: `[env:esp32s3*]` in `firmware/platformio.ini` (6 profiles,
  `esp32-s3-devkitc-1`) and `[board.esp32s3]` in `hil_config.toml`. Dual-USB-C S3
  boards (e.g. DevKitC-1) have the C3's UART-bridge fix built in — no external
  wiring — see README "ESP32-S3 dual-USB-C setup".
- **All three boards × all six profiles verified green on the reference rig**
  (Raspberry Pi 3B+), functional suite **and** `--bench`: flash → BLE pair →
  62-test suite + latency/throughput sweep over the real command channel
  (onboard CP2102 for esp32dev; external UART bridge on UART0 + native-USB
  `flash_port` for the C3/S3).
- `docs/bench/` — committed snapshot of the cross-board benchmark
  (`bench-table.md` + 3 SVGs). Button e2e p50 is a flat ~18.6 ms on every
  board/profile; conn interval 48.75 ms / MTU 255 everywhere; 0 dropped.

### Fixed

- `hidraw.find_node()` now matches the DUT by MAC (`HID_UNIQ`), not just the
  shared VID/PID — on a multi-board tester the descriptor / feature / output
  tests were reading a *different* bonded board's hidraw node.
- `SerialDev.command()` retries once on a timeout — the cheap C3/S3 external
  USB-UART bridges drop a byte occasionally under the `--bench` burst load
  (~1 run in 5 previously needed a manual retry). CI also retries a failed
  `--bench` run once.

### Changed

- CI (`.github/workflows/hil.yml`) builds the full `esp32dev` + `esp32c3` +
  `esp32s3` matrix; the tester runs only the boards it actually has wired
  (`esp32c3` / `esp32s3` ship with `port`/`flash_port` still `CHANGE-ME`, so
  they build here and skip on a tester until real ports are set — see README
  "ESP32-C3 serial bridge" / "ESP32-S3 dual-USB-C setup"). No board defaults to
  `enabled = false` any more.
- The `hil-test` job now `git fetch` + `git reset --hard`es the tester's checkout
  to the rig commit under test (was: `rsync` over it, which left the tester's
  `.git` drifting behind a working tree full of "modifications"). Only the
  gitignored `hil_config.local.toml` is preserved. `results/` is pulled back even
  when the suite fails.
- `hil.yml` / `release.yml` jobs are guarded: they always run in
  `LeeNX/ESP32-BLE-Gamepad-HIL`, and in a fork only when repo variable
  `HIL_RIG_ENABLED=true` is set — so a fork with no tester wired gets a clean
  skipped run, not a failure on the missing Tailscale secret.
- `hil.yml` / `release.yml` take the library repo/ref from repo variables
  `HIL_LIB_REPO` / `HIL_LIB_REF` (default `LeeNX/ESP32-BLE-Gamepad` @ `master`),
  so the rig can build+test against a fork branch until the library-side HID
  descriptor getters land on `master`. Explicit dispatch inputs still win.

[Unreleased]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.0
