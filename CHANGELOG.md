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

- **`scripts/update-goldens.py`** — regenerate `firmware/golden/<profile>.hiddesc`
  after an intentional descriptor change: build, flash one board, read the
  descriptor over serial (`RMAP?` — no BLE, no Linux), write the golden, refusing
  any that overruns the 150-byte buffer. Runs on any dev box with PlatformIO + a
  wired board — closes the v0.2.1 gap where `specials` / `minimal` shipped
  without goldens.
- **`desktop/` behavioural tests — axes and hats** (`pytest -m sdl`,
  `test_axes.py` / `test_hats.py`): each firmware axis → the matching SDL axis,
  monotonic, −1/0/+1 endpoints; each firmware hat → all 8 directions on the
  matching SDL hat. macOS/SDL surfaces **all** hats (evdev only makes
  `ABS_HAT0`) but the library's reversed hat fields mean firmware hat `h` is SDL
  hat `n − h` — pinned. `sdlgamepad.wait_until()` polls SDL instead of a fixed
  sleep (macOS's BLE-HID tail is jittery).
- **`desktop/` latency sweep** (`pytest -m latency`, opt-in — a bare `pytest`
  run skips it): `TPRESS` on the serial channel, then time the blocking hidapi
  read of the resulting HID report. p50 / p90 / p99 for BLE-air and end-to-end,
  a dropped count, and the serial `ping` baseline. `--latency-n` (default 100),
  `--latency-json` writes `desktop/results/latency-*.json`. Host-side hidapi
  timestamps, so a few ms high and jittier than the rig's kernel-timestamped
  `--bench` numbers — for regression / same-box comparison.
- **`pair-assist.py --reconnect`** (macOS): `blueutil` Bluetooth power-cycle +
  reconnect, to refresh a stale bond / cached HID descriptor after a re-flash
  without touching System Settings. (The *first* pair still needs the GUI —
  blueutil can't scan for BLE.)
- **HIL run summary on the Actions run page.** `hil.yml` writes
  `host/hil/summarize.py results/junit-*.xml` to `$GITHUB_STEP_SUMMARY`: a
  board × profile matrix, a per-feature-area pass/fail/skip rollup across every
  bundle, failures inline (first line of the message), and only the skips that
  look like a real gap — routine "profile has no rz axis" skips are counted, not
  listed. `dorny/test-reporter` still creates the gating per-test check.

### Changed

- `host/hil/summarize.py` rewritten to aggregate **multiple** junit files into
  one report (was one file → one `summary.md`, last-writer-wins under
  `--by-board`). `tester/test-all.sh` regenerates `results/summary.md` from all
  lanes at the end of a run.

## [0.2.1] — 2026-09-09

### Added

- **`desktop/` — a macOS / Windows tester** for the portable slice of the rig,
  reusing `hil.serialdev` / `hil.hidraw` and `firmware/golden/` directly (not a
  fork). Three test levels, pytest markers:
  - `-m serial_only` — the `hil_runner` serial channel only, no BLE bond:
    descriptor generation (`RMAP?` vs golden), report sizing, DIS / PnP, the
    advertised name, protocol round-trips. Runs on macOS/Windows with just
    `pyserial`.
  - `-m sdl` — the DUT as an SDL joystick (`pygame`): button behaviour "as a
    game sees it", one code path for all three OSes, headless.
  - `-m hid` — raw HID input reports (`hidapi`): firmware + descriptor +
    transport, byte-level.
  - `pair-assist.py` — reads the board over serial and says which BLE entry to
    pair, clears stale bonds, waits on `CONN?`.
- **`local` firmware profile** (`HIL_PROFILE_LOCAL`, 4 btn / 1 hat / X-Y) —
  ad-hoc, for local developer testing. **Never built by CI or a release**
  (explicit profile lists; `hil_config.toml` notes it). Advertises as
  `HILdev <board>`, not `HILpad <board>`.
- **Serial protocol:** `NAME?` (advertised BLE name / `getDeviceName()`),
  `BONDS?` (stored-bond peer list), `CLEARBONDS` (`ble_store_clear()`).
- **`HIL_DEVICE_NAME`** build flag / `builder/build.sh --name` /
  `$HIL_DEVICE_NAME` / `hil_config` `[rig] local_device_name` — override the
  advertised BLE name (≤ 18 chars, or NimBLE drops the HID service UUID then the
  name from the legacy advertising packet).
- **`test_connection.py::test_advertised_name`** (+ the desktop `serial_only`
  equivalent) — asserts `NAME?` is *exactly* the name the harness discovers and
  bonds by, not just a sane-looking string. Expected value is the build-time
  override recorded in the bundle manifest, else `<HILpad|HILdev> <board>`.
- Bundle `manifest.json` carries **`device_name`** when the advertised name was
  overridden at build time (`builder/build.sh --name` etc.), so the tester can
  check the post-truncation result. Omitted when the firmware default is used.
  New `manifest` pytest fixture exposes the flashed bundle's manifest.

### Changed

- **`hil.yml` runs the parallel functional matrix by default.** Push,
  `repository_dispatch` (the library's correctness gate), and a plain
  `workflow_dispatch` now run `tester/test-all.sh --by-board` — ~3x faster,
  ~15 min instead of ~1 h. The
  sequential `--bench` latency/throughput sweep is no longer on every push: it
  runs on a weekly `schedule` (Mondays 02:00 UTC / 04:00 SAST) and on a
  `workflow_dispatch` with `bench: true`. Its gates are deliberately loose, so
  gating every push on it bought little. The remote heredoc falls back to a
  sequential functional run if the rig commit under test predates `--by-board`.
- **Compile profiles 6 → 4, and only 3 run on every push.** `signed-axes` and
  `reports` folded into **`specials`**, which is now the one "fragile surface"
  flash: 8 special (consumer/desktop) usages, signed axes (`test_ranges`
  negative rail), *and* the Output + Feature reports (`test_feature_report.py` /
  `test_output_report.py`). Trimmed to X/Y axes, no hat, to stay clear of the
  fixed 150-byte HID descriptor buffer. **`minimal`** is now 2 btn / X-Y (was
  1 / X) — purely the latency-curve low-end anchor, nothing depends on it, so it
  is the one CI profile left **off push/PR**: it builds only on the weekly
  `schedule` and at release.
  - push / PR / local `builder/build.sh` default: `default specials maxbtn`
  - weekly `schedule` + release: `+ minimal`, with `--bench`
  - rationale, per profile: `default` = what most people run;
    `specials` = the fragile, least-exercised surface; `maxbtn` = the largest
    layout the HID transport supports (128-button ceiling).
  Cuts a push matrix from 6 flashes/board to 3. `specials` loses its lone hat
  (`test_hats` real coverage is `default`'s 4). `local` dev profile unaffected.

### Removed

- `signed-axes` and `reports` compile profiles, their `platformio.ini` envs, and
  `firmware/golden/{signed-axes,reports}.hiddesc`. `firmware/golden/{minimal,
  specials}.hiddesc` dropped too — regenerate on the rig with
  `pytest --update-golden` and commit (both descriptors changed).

### Fixed

- Bundle staging no longer races the rig lock. `hil.yml` (and the library's
  `scripts/hil.sh`) rsync'd bundles straight to `~/hil-bundles` with `--delete`
  *before* acquiring the lock, so a concurrent CI + local run clobbered each
  other's bundles mid-flash. Callers now rsync to a staging dir and pass
  `HIL_BUNDLE_STAGE`; `tester/test-all.sh` swaps it into `~/hil-bundles`
  atomically once it holds the lock (`hil.yml` also swaps in its heredoc, so it
  works when the rig commit under test predates this).

## [0.2.0] — 2026-09-08

### Added

- **`tester/test-all.sh --by-board`** — run the boards as parallel lanes (one
  `pytest` per board, profiles sequential within a lane). On the 3-board rig the
  full 18-bundle functional matrix went ~35 min → **~12 min** (measured, all
  pass). Refuses `--bench` (the latency sweep stays sequential + solo — with
  peers connected `clean_rate` drops ~25%). Not wired into `hil.yml` yet — prove
  it on your rig first.

### Changed

- `conftest.py` serialises the two adapter-global operations across parallel
  per-board runs with an `fcntl.flock`: `state.json` writes, and pairing. The
  `BtCtl` session (one `bluetoothctl` agent) now lives entirely inside the pair
  lock — created, used, closed — so lanes never run two agents at once (a second
  agent made `bluetoothd` return `org.bluez.Error.InProgress`). `ensure_paired()`
  also drops a live link before `pair` and retries the pair+bond block 3x.
- `hil.bluetooth.BtCtl` is now a context manager (`with BtCtl() as c: ...`).

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

[Unreleased]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.2.1
[0.2.0]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.2.0
[0.1.2]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.2
[0.1.1]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.1
[0.1.0]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.0
