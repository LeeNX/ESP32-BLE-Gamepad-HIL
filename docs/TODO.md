# Possible future work

Nothing here is planned. It's a parking lot for ideas that only earn their keep
if someone actually wants the data they'd produce — pick one up when that
happens, not before. The rig's job is to answer questions people are asking; a
test nobody reads is just flash wear.

## Feature coverage gaps

The suite covers the gamepad core well (buttons, hats, axes, special buttons,
HID descriptor, Device Info / PnP, battery, Feature / Output reports, latency).
These ESP32-BLE-Gamepad features have **no firmware command and no test**:

- [ ] **Simulation controls** — rudder / throttle / accelerator / brake /
  steering. `setWhichSimulationControls(false, …)` is hardcoded off in
  `hil_profile.h`. Real HID report-descriptor content (flight / racing rigs).
  Cost: a `SIMCTRL` serial command, a profile that enables them, a `test_*.py`,
  a golden.
- [ ] **Motion controls** — accelerometer / gyroscope. Same shape as above
  (`MOTION` command, profile, test, golden).
- [ ] **Rumble** (`setEnableRumble`) — host → device, adjacent to the Output
  Report path already covered.
- [ ] **Player LED** (`isPlayerLedReceived`) — host → device, like Feature /
  Output reports.
- [ ] **Nordic UART Service** (`sendDataOverNUS`, NUS receive callback) — the
  HIL firmware uses a *wired* serial channel, so this needs a BLE-side test.
  Prior art on the `bluepad32-observer` branch (see below).
- [ ] **SInput protocol** (`setEnableSInput`) — an alternate input report
  format; scope depends on whether it's a shipped feature. Prep work done:
  `host/hil/sdlreport.py` (Linux rig) + `desktop/sdlgamepad.py`'s `rumble()`
  (macOS/Windows) quantify what today's default HID descriptor actually looks
  like through SDL, the layer most real apps/games use — see
  `desktop/README.md` "What macOS/SDL does differently from the Linux rig".
  Headline gaps SInput would need to address: Linux/SDL only sees 22 of 64
  buttons and 2 of 8 axes (evdev's hid-generic mapping ceiling, not an SDL
  limit — macOS/SDL sees all of them), and no force-feedback on either
  platform (no FF usage in the descriptor).
- [ ] **TX power level** (`setTXPowerLevel`) — checkable from the advertising
  data during a scan.

## Reporting / infrastructure

- [ ] **Feature-tag markers + a generated coverage gate.** `@pytest.mark.feature("hats")`
  on each test; a script diffs the tag set against a canonical feature list and
  warns (or fails) on anything untested. Self-maintaining version of the list
  above.
- [ ] **Bench trend history.** `--bench` numbers are only ever compared to the
  checked-in `docs/bench/` snapshot. A small time series (per board / profile,
  p50 / p99 / clean-rate) would catch slow drift, not just step regressions.
- [ ] **Self-healing Bluepad32 lane.** `tester/bp32_hil.py` waits up to 90 s
  for `HIL ready` and then fails the lane. On 2026-11-17 the C3 observer's
  pairing with the esp32dev dropped (`Reason=0x3e`) and the gamepad then
  stopped advertising for good, so every C3 → esp32dev lane went red for a
  day. The cause was a bad USB hub port: the kernel logged
  `cp210x ttyUSB0: failed to get comm status: -121`, and moving the cable
  fixed it. Proposal: if the observer isn't ready after ~20 s, ask the gamepad
  `CONN?` / `STATS?`, log what both sides saw (the observer's `Reason=` line and
  the gamepad's state), reset the gamepad (RTS → EN) and retry once. Record the
  retry in the verdict (like `gaps=`) so a flaky port shows up as a warning
  rather than either a red run or a hidden pass. A kernel-log check for
  `-121` / USB disconnects on the lane's ports would point at the hardware
  directly.
- [ ] **NimBLE-Arduino after 2.5.2.** `firmware/platformio.ini` leaves
  `h2zero/NimBLE-Arduino` unpinned, so builds take the latest release (2.5.2,
  2026-10-25). In 2.5.2, `NimBLEServer::getPeerInfo(index)` returns
  `m_connectedPeers[count]` instead of the peer it found. So whenever the live
  link isn't in slot 0, hil_runner's `PEERINFO?` reports all zeros
  (`interval=0 … mtu=0`) although the link is up. Upstream `master` already
  has the fix (`getPeerInfoByHandle(peer)`). Until the next release, don't
  trust an all-zero `PEERINFO?`. `master` also carries `[Breaking]` callback
  changes (write-callback value parameter, `setCallbacks` renames), and an
  unpinned dependency picks up the next release, major version included, on
  the next CI build. So decide whether to pin `h2zero/NimBLE-Arduino@~2.5.2`
  until ESP32-BLE-Gamepad has been checked against the new API. Then move up
  deliberately and re-run the suite.
- [ ] **Re-check desktop testing against the current Linux CI/CD flow.**
  `desktop/` (macOS / Windows) was last touched 2026-10-07 (v0.4.0); the rig
  side has moved on since: `sinput` profile + observer matrix, multi-slot
  flashing, power-state (0x2A1A) required by default, `CONFIG?` libsha
  mismatch check, retry/backoff, rig health. Walk `desktop/README.md` end to
  end on real hardware and sync it up: does `builder/build.sh --profiles local`
  still produce what the desktop suite expects; do the shared
  `hil.serialdev` / `hil.hidraw` helpers still match the firmware's serial
  protocol; and which `host/tests/` the desktop subset now lacks
  (`test_battery`, `test_descriptor`, `test_device_info`,
  `test_output_report`, `test_special_buttons`, `test_sdl_report`; some
  overlap `desktop/gatt.py` / `test_hid_reports.py`). Then decide whether
  desktop gets a lint/smoke job in CI or stays a manual, documented path.
  **Goal: one codebase, not two.** Linux CI and desktop should share the same
  firmware build (`builder/build.sh`, `firmware/`), the same `host/hil/`
  helpers and, where the transport allows, the same test files — only a thin
  platform adapter (hidraw/evdev vs `hidapi`/`bleak`) should differ. Prefer
  moving a desktop test into `host/tests/` behind a transport fixture over
  keeping a parallel copy, and fold the desktop-only `local` profile into a
  shared one if its only reason to exist is history.
- [ ] **NuS / OTA testing from the old `bluepad32-observer` branch.** The
  branch (gitea only, 7 commits, kept on purpose) predates the
  `firmware/observer` rewrite and has hardware-verified work that never landed:
  a NuS second host mirroring the HIL output and commands, OTA over NuS
  (authenticated / unauthenticated, arming via GPIO0 / gamepad chord / serial,
  rollback, factory reset, factory + ota_0 + ota_1 partitions), a NuS access
  gate, and runners `ota_client.py`, `ota_test.py`, `gate_test.py`,
  `chord_test.py` plus `PARTITIONS.md` and BLE-vs-serial OTA timings. Worth
  mining for the **Nordic UART Service** gap above and for an OTA-over-BLE
  test next to the multi-slot flashing. Port the ideas onto
  `firmware/observer` / `hil_runner`, don't merge the branch (its layout is
  obsolete). A second reason since 2026-11-17: the esp32dev's CP2102 loses
  the odd reply line (and adds a packet of `0xFF` junk) only while the radio
  is busy, and a NuS copy of the command channel would tell "the serial path
  lost the line" from "the board never replied" with one command. Keep it a
  complement to serial, not a replacement, and opt-in (a build flag, off in
  the CI profiles): it shares the radio and the connection with the HID
  reports the bench measures, adds a GATT service the descriptor and
  device-info tests would see, and can't help before pairing, for flashing,
  or when BLE itself is the fault.
