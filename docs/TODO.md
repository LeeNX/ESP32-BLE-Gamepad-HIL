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
