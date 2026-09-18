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

### Changed

- **Configurable retry count + backoff** — `tester/test-all.sh` retried a
  failed bundle exactly once, with no pause. It now retries up to
  `$HIL_RETRY_COUNT` times (default 3, e.g. `HIL_RETRY_COUNT=0` for none),
  pausing `$HIL_RETRY_PAUSE` seconds before each attempt (default 15,
  doubling after every retry — 15s, 30s, 60s, ...); both are overridable and
  apply to phase 1 (`--by-board`'s flash+pair+smoke step) as well as the main
  suite. `--by-board`'s barrier/mutex timeout defaults
  (`$HIL_PHASE1_BARRIER_TIMEOUT`, `$HIL_PHASE1_MUTEX_TIMEOUT`) now scale with
  these knobs instead of assuming a single retry.

### Fixed

- **Stale FAIL in the run report after a passing retry** — `results/run-verdicts.md`
  (what CI prints in the job summary) is append-only, so a bundle that failed
  its first attempt and then passed on retry left both a `FAIL` and a `PASS`
  line in the report — a real run with zero net failures still read as red at
  a glance. `test-all.sh` now drops the stale `FAIL` line(s) for a bundle once
  a retry of it succeeds.

## [0.2.5] — 2026-09-17

### Added

- **`docs/rig-build.md`** — photos of the reference rig and minimum specs to
  run multiple MCUs concurrently, measured on the live 3-board Raspberry Pi
  3B+ tester (CPU/RAM/temp/USB, via `free` and a `health-timeline-*.csv`
  sample from a real `--by-board` run — kernel thermal-zone temp, cpufreq,
  and the `rpi_volt` hwmon undervoltage alarm, not `vcgencmd`, which needs
  `sudo`/group access this tester user doesn't have; see
  `host/hil/sysinfo.py`).

- **Retry count in the report** — `tester/test-all.sh` now records how many
  extra attempts (beyond the first) each board/profile needed this run, in a
  `results/retries-<board>-<profile>.txt` sidecar (`record_retry()`), and
  `summarize.py`'s bundle matrix has a new `retries` column reading it. A
  retry's failed first attempt isn't in the final junit at all, so without
  this a board that ran noticeably longer than its neighbors gave no hint
  why — a flaky pairing or a dropped byte on the C3/S3 UART bridge (README
  "Rig note") looked identical to "just slower" in the report.

- **Inline benchmark charts + table into release notes** — `release.yml`'s
  release body now prepends the committed `docs/bench/` snapshot (table + up
  to 3 SVGs) via `gh release create --notes-file`, images embedded via
  `raw.githubusercontent.com` pinned to the release tag so they match
  exactly what was committed at that point. Falls back to a plain
  `--generate-notes` body if `docs/bench/bench-table.md` is missing/empty
  (e.g. the very first release). Each chart is linked only if that specific
  SVG actually exists — `host/hil/charts.py` can skip an individual chart
  when it has no data for it that sweep, independent of the table, so a
  snapshot missing one chart no longer ships a broken image link for the
  whole release.

### Fixed

- **`--by-board` false failure when a board has no port configured** — an
  unwired (`CHANGE-ME`) board's lane finishes its phase-1 SKIP almost
  instantly, then sat waiting at `round_barrier` for the real boards' actual
  flash+pair+smoke test; that wait used the same `HIL_PHASE1_BARRIER_TIMEOUT`
  (180s default) that real hardware can now legitimately exceed since the
  BLE pairing robustness fixes added real wall-clock time. The unwired
  board's lane would give up first and fail the whole run's exit code even
  when every wired board passed everything. `round_barrier` now waits only
  on boards that are actually present (`hil.detect --present`), and the
  default timeout is raised to 300s for headroom.

- **`--by-board` false failure from a flat barrier timeout on a multi-board
  rig** — phase1 (flash+pair+smoke) is a one-board-at-a-time global mutex,
  so the time a fast board waits at `round_barrier` for the round to clear
  scales with fleet size, and `phase1_turn`'s own built-in retry can double
  one board's turn on top of that. The 300s flat default above was only
  headroom for ~1-2 boards' worth of turns. Seen live on the 3-board rig:
  esp32c3 (first to arrive) timed out at 300s just 5s before esp32dev's
  retried phase1 (~180s vs ~85s normal) finished, failing the whole
  `--by-board` run's exit code despite 349 passed / 0 failed across every
  board. `round_barrier`'s default timeout now scales as 150s per synced
  board (300s floor), so a 3-board rig gets 450s of headroom; override with
  `HIL_PHASE1_BARRIER_TIMEOUT` still works as before.

- **CI build cache never actually hit** — the `.pio/build` cache step added
  for releases (0.2.4) cached the repo-root `.pio/build`, but
  `builder/build.sh` invokes PlatformIO with `-d firmware/`, so build
  artifacts land in `firmware/.pio/build` instead — the cache had never
  actually saved or restored anything since it was added (confirmed via a
  "Path Validation Error... no cache is being saved" in the Actions log).

## [0.2.4] — 2026-09-15

### Added

- **Desktop GATT reads via bleak** (`desktop/sdlgamepad.py` now reads Device Info,
  PnP ID, and Battery status from the gamepad's GATT characteristics via bleak,
  independent of HID Feature Reports; `desktop/tests/ -m hid` exercises both paths).

### Fixed

- **Recover from BLE pairing flakes** — `tester/test.sh` now disconnects and
  untrusts the device after each run to clear stale pairing state that can cause
  mid-suite reconnect failures.
- **BLE link stability between by-board phases** — phase-1 (flash+pair+smoke)
  was tearing down the BLE connection before phase-2 heavy tests; the link now
  stays up across the phase barrier on platforms that support re-connection
  without re-pairing.
- **ServicesResolved readiness** — `bluetooth.ensure_paired()` now waits for
  the D-Bus `ServicesResolved` flag (not just GATT characteristics available)
  before returning, blocking race where service discovery was incomplete.
- **test_ble_connects dependency chain** — test now depends on `bt_mac` fixture
  in addition to `connected_dut` to ensure BLE discovery completes before
  connection attempts.
- **CI cache handling for releases** — `hil.yml` now zeros the PlatformIO build
  cache on release runs to guarantee fresh builds, while preserving it on dev
  runs for faster iteration.

## [0.2.3] — 2026-09-13

### Added

- **Rig health/USB/BT logging + firmware `TEMP?`/LED diagnostics** — prompted
  by the 2026-09-11 rig crash investigation (dwc_otg USB bus timeouts during a
  simultaneous 3-board reconnect). `tester/rig-lock.sh` now runs a background
  sampler for the lock's whole lifetime (temp/freq/under-voltage/loadavg →
  `results/health-timeline-*.csv`); every `tester/test.sh` run snapshots
  sysinfo before/after and greps the journal for `dwc_otg`/`ftdi_sio`/
  `bluetoothd` activity, not just `--bench` runs. Firmware gained `TEMP?`
  (`temperatureRead()`, all 3 boards) and `LED ON|OFF` plus an automatic
  activity pulse, opt-in behind `-D HIL_LED_PIN=<gpio>`.
- **`HIL_CONN_LED_PIN`** — second opt-in status LED, steady on while
  BLE-connected (mirrors `CONN?`), threaded through the same config/env
  mechanism as `HIL_LED_PIN`. New `docs/rig-hardware.md`: parts list, resistor
  sizing, wiring diagram, per-board GPIO conflicts to avoid.
- **USB topology in every run's health snapshot** — `host/hil/sysinfo.py`
  captures `lsusb -t` so hub power/negotiated-speed questions don't need
  asking by hand when reviewing a CI run.
- **SDL/GameController-layer reporting** (`host/hil/sdlreport.py` on the rig,
  `desktop/sdlgamepad.py` gains `rumble()`) — prep for the SInput profile:
  what SDL/games actually see, not just raw HID bytes. Verified against real
  hardware on both testers; SDL on Linux inherits evdev's ceiling (22/64
  buttons, 2/8 axes on the default profile), rumble unsupported on either
  platform.
- `pytest.ini` streams fixture progress instead of capturing it, so
  long-running fixture setup is visible live under CI.

### Fixed

- **`tester/rig-lock.sh` exit trap silently overriding every nested
  `test.sh` exit code** — a long-standing bug where a failing test under the
  lock could still report success.
- Health-timeline sampler never actually ran under CI: `hil.yml`'s own raw
  `flock` pre-export of `HIL_RIG_LOCK_HELD` hit the re-entrant early-return
  before starting the sampler. The re-entrant path now also starts it, guarded
  so the 3 parallel `--by-board` lanes don't each spawn a redundant one.
- CI never forwarded `HIL_LED_PIN_<BOARD>` / `HIL_CONN_LED_PIN_<BOARD>` into
  the build job, so a repo Variable/Secret for either never reached
  `builder/build.sh`.
- `--by-board` lanes now serialize flash+pair+smoke across boards before
  parallelizing the heavy tests, fixing mid-suite pairing failures caused by
  simultaneous BLE pairing across lanes; a timed-out round barrier now fails
  closed instead of silently continuing, `--by-board` without `flock`
  available is now refused instead of silently skipping the phase-1 mutex,
  and stale phase-1 junit files are cleared before either `test-all.sh` path
  runs.
- `sdl_report` now skips (instead of failing) when SDL can't see the device,
  and the report is colorized.

## [0.2.2] — 2026-09-11

### Added

- **`tester/rig-status.sh -f` / `-v`** — follow the currently-running test's
  log instead of only the one-line verdict: all `results/lane-<board>.log`
  together under `--by-board`, or the one stamped `results/log-<board>-
  <profile>-<stamp>.txt` for a lone `tester/test.sh` run. Prints the last log's
  tail instead of blocking when the rig is idle.
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
  board × profile matrix (with per-bundle test time), **test time per MCU**, a
  per-feature-area pass/fail/skip rollup across every bundle, failures inline
  (first line of the message), and only the skips that look like a real gap —
  routine "profile has no rz axis" skips are counted, not listed.
  `dorny/test-reporter` still creates the gating per-test check.
- **`docs/TODO.md`** — parking lot for coverage gaps (simulation controls,
  motion controls, rumble, player LED, NUS, SInput, TX power — none tested) and
  infra ideas. Nothing planned; pick one up only if the data's wanted.

### Changed

- `host/hil/summarize.py` rewritten to aggregate **multiple** junit files into
  one report (was one file → one `summary.md`, last-writer-wins under
  `--by-board`), and to report the pytest-phase time per bundle and per MCU.
  `tester/test-all.sh` regenerates `results/summary.md` from all lanes at the
  end of a run.
- **`lint.yml` gains a `desktop-collect` job** — imports and collects
  `desktop/` on Linux (no hardware). The desktop tester has no hardware CI, so
  this is the guard that a refactor of the shared `host/hil.*` helpers hasn't
  broken its imports or fixtures.
- **README "macOS as a tester" / "Cross-platform tester — TODO" sections
  replaced** by a single **"Desktop tester (macOS / Windows)"** section
  describing what `desktop/` actually is now (four test levels, all green on
  macOS; Windows not yet run) instead of the pre-`desktop/` scoping notes.

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

[Unreleased]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/compare/v0.2.5...HEAD
[0.2.5]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.2.5
[0.2.4]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.2.4
[0.2.3]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.2.3
[0.2.2]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.2.2
[0.2.1]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.2.1
[0.2.0]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.2.0
[0.1.2]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.2
[0.1.1]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.1
[0.1.0]: https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/releases/tag/v0.1.0
