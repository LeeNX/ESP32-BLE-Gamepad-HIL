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
- **minor** — new profiles, new tests, new `test.sh` / `detect-boards.sh`
  behaviour, new config keys (backwards compatible).
- **patch** — fixes and doc changes that don't move any of the above.

## [Unreleased]

### Added

- `VERSION`, this changelog, `scripts/release.sh`, `.github/workflows/release.yml`
  and `RELEASE.md` — the rig now cuts versioned GitHub releases carrying a
  reproducible firmware-bundle set (`firmware-bundles/` + `golden/` + `index.json`)
  and a standalone copy of the test suite.
- `tester/detect-boards.sh` — reports which configured boards are physically
  present and `enabled`. `tester/test.sh` now **skips** (exit 0, `SKIP` verdict)
  a bundle whose board is absent or disabled instead of failing the run.
- `hil_config.toml`: `[board.<b>] enabled` (default `true`) — set `false` to keep
  a board in the build matrix but out of the flash/test loop.
- `host/hil/config.py --boards` lists the configured board names.

### Changed

- CI (`.github/workflows/hil.yml`) builds the full `esp32dev` + `esp32c3` matrix;
  the tester runs only the boards it actually has wired (`esp32c3` ships as
  `enabled = false` until its UART bridge is fitted — see README "ESP32-C3 serial
  bridge").

## [0.1.0] — 2026-09-02

First tagged rig. Everything up to and including: the builder/tester split,
`bootstrap-host.sh` + `bootstrap.sh`, six compile profiles (`default`,
`signed-axes`, `specials`, `minimal`, `maxbtn`, `reports`), the BLE-HID +
GATT + descriptor + latency suite, the Tailscale-based CI, and green runs on the
`rp3b-ble-hil` tester.

[Unreleased]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.0
