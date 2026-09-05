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
- **All three boards (`esp32dev`, `esp32c3`, `esp32s3`) now verified green
  end-to-end on the reference rig** (Raspberry Pi 3B+): flash → BLE pair → the
  full 62-test suite over the real command channel (native USB bridge for
  esp32dev; an external UART bridge on UART0 + native-USB `flash_port` for the
  C3/S3). Each: 41 passed / 19 skipped (profile-gated) / 2 xfailed (known Linux
  HID-mapping limits).

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

## [0.1.0] — 2026-09-02

First tagged rig. Everything up to and including: the builder/tester split,
`bootstrap-host.sh` + `bootstrap.sh`, six compile profiles (`default`,
`signed-axes`, `specials`, `minimal`, `maxbtn`, `reports`), the BLE-HID +
GATT + descriptor + latency suite, the Tailscale-based CI, and green runs on the
`rp3b-ble-hil` tester.

[Unreleased]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.0
