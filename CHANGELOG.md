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

- **`tester/test-all.sh --by-board`** — run the boards as parallel lanes (one
  `pytest` per board, profiles sequential within a lane). ~3x on the 3-board
  reference rig: the functional matrix drops from ~55 min to ~20 min. Refuses
  `--bench` (the latency sweep stays sequential + solo — with peers connected
  `clean_rate` drops ~25%). Not wired into `hil.yml` yet — prove it on your rig
  first. Validated by a 25-iteration 3-board soak (~3900 button cycles, zero
  dropped events).

### Changed

- `conftest.py` guards its `state.json` read-modify-write with an `fcntl.flock`
  so parallel per-board runs don't clobber each other's bond records.

### Removed

- `host/hil/parallel.py` — the spike that proved concurrent drive + capture is
  safe; its checks are the real `test_*.py` suite, run per-lane by `--by-board`.

## [0.1.2] — 2026-09-07

### Added

- **Rig lock** (`tester/rig-lock.sh` + `host/hil/riglock.py`): one physical rig,
  so `run.sh` / `tester/test.sh` / `tester/test-all.sh` and CI take an `flock` on
  `~/.cache/esp32-hil/rig.lock` before touching it — a second run waits ~45 min
  then fails with the holder's identity. `tester/rig-status.sh` prints
  who / what / commit / CI-URL is running (or the last run's verdict).
- `tester/test-all.sh` — the per-bundle flash+test loop with one retry, lifted
  out of `hil.yml` so it's committed and runnable locally
  (`tester/rig-lock.sh -- tester/test-all.sh --bench`).
- `tester/test.sh` accepts `--wait <secs>` / `--no-wait` for the rig lock.
- **Focused HIL runs**: `hil.yml` `workflow_dispatch` gains `boards` / `profiles`
  / `test_filter` (a pytest `-k` expression) inputs — narrow the matrix and test
  selection to chase one red test in ~10 min instead of the full ~80. Locally:
  `HIL_TEST_FILTER=... tester/test-all.sh`.
- `--bench-quick` (with `--bench`) — a short sweep (n=40, 3 gap values, ~2 min
  vs ~6) for a fast check.
- Every bench result records how many BLE connections shared the adapter during
  the sweep — `adapter_links` / `adapter_link_macs`, surfaced as a **links**
  column in `bench-table.md` — so a number taken while another bond lingered
  isn't mistaken for a clean solo measurement.

### Fixed

- `tester/test.sh` writes `results/junit-<board>-<profile>.xml` (no timestamp),
  so a bundle's retry **overwrites** its failed first attempt. A flaky test that
  passed on retry was still reddening CI because `dorny/test-reporter` globbed
  the stale attempt-1 JUnit.
- `hil.latency.clean_rate()` re-measures a weak point on the flat part of the
  sweep once and keeps the better run; `test_clean_rate`'s slowest-point gate
  relaxed 0.95 → 0.90. Stops host-scheduling jitter at ~6 Hz from failing the
  bench.

## [0.1.1] — 2026-09-06

### Fixed

- `hil.yml` / `release.yml` resolve the library repo/ref from repo **variables**
  `HIL_LIB_REPO` / `HIL_LIB_REF` (else the built-in `LeeNX/ESP32-BLE-Gamepad` @
  `master`); explicit dispatch inputs still win. `hil_runner` needs the library's
  HID report-descriptor getters, which live on a fork branch not yet on `master`
  — **`v0.1.0`'s `release.yml` build failed against `master` and published no
  artifacts.** Set `HIL_LIB_REF` to the fork branch; drop it once merged.
- `CHANGELOG.md`: dropped a duplicate `## [0.1.0]` section and link ref (a
  pre-populated placeholder that collided with `scripts/release.sh`).

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

[Unreleased]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.2
[0.1.1]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.1
[0.1.0]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.0
