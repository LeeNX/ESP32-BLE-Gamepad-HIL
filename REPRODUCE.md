# Reproducing a HIL run

This archive is what the hardware-in-the-loop rig
([ESP32-BLE-Gamepad-HIL](https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL)) built
and tested for a given release. `index.json` records the exact rig commit, the
ESP32-BLE-Gamepad library commit, and every bundle.

You do **not** need PlatformIO or the library source — the firmware is
prebuilt. You need a Linux box, an ESP32, a BLE adapter, and Python.

## What's here

| Path | |
|---|---|
| `firmware-bundles/<board>-<profile>-<libsha8>/` | flashable `.bin` parts + `manifest.json` (chip, offsets, sha256s) |
| `firmware-bundles/matrix/<board>-sinput-<libsha8>/` | the `sinput` profile, which the pytest suite doesn't run (see below) |
| `golden/<profile>.hiddesc` | the HID report descriptor each profile is expected to generate |
| `suite/` | the pytest suite, `tester/` scripts, `conftest.py`, `hil_config.toml` |
| `index.json` | provenance: rig + library commit, board/profile list |

## Steps

```bash
# 1. deps (Debian/Ubuntu; see the rig's tester/bootstrap-host.sh for the details)
sudo apt install -y bluez rfkill
python3 -m venv .venv && .venv/bin/pip install -r suite/tester/requirements.txt

# 2. point the config at your serial port
cd suite
cp hil_config.toml hil_config.local.toml
# edit [board.esp32dev].port -> your /dev/serial/by-id/... path

# 3. flash a bundle and run the suite
.venv/bin/python tester/flash.py ../firmware-bundles/esp32dev-default-<libsha8>/ --port <port>
.venv/bin/pytest --board esp32dev --profile default --no-flash --port <port>
```

`tester/test.sh <bundle-dir> --bench` does flash + suite + the latency benchmark
in one go (it expects the repo layout, so run it from a full rig checkout rather
than this archive).

A green run means the library commit in `index.json` behaves on real hardware
exactly as the rig recorded. A red run on the same firmware points at your host
(kernel `hid-input` version, BlueZ version, adapter) — compare against the
`results/` in the rig's own run for that release.

## The `sinput` profile and the observer matrix

`sinput` bundles (under `firmware-bundles/matrix/`) aren't tested by the pytest
suite. Two scripts test them:

- **`tester/sinput_hil.py`** runs against `hid-generic` and the
  [linux-hid-sinput](https://github.com/LeeNX/linux-hid-sinput) kernel driver.
  It needs one board and the driver set up by `tester/bootstrap-sinput.sh`.
- **`tester/test-matrix.sh`** runs the observer matrix. It needs three boards,
  with one at a time acting as a Bluepad32 *observer*.

**Observer firmware isn't in this archive.** It is built with
`builder/build-observers.sh` from two checkouts outside the rig repo. For this
release they were:

| Source | Ref |
|---|---|
| Bluepad32 with the SInput parser: [LeeNX/bluepad32](https://github.com/LeeNX/bluepad32) `feature/sinput` | `6f603c7`, BTstack submodule with Bluepad32's l2cap patch applied (`external/patches`) |
| HIL host firmware: leenx-foss/antBot-hil `host/`, built with `HIL_OBSERVER=1` | `observer-build` @ `e1ebe64` |

Point `[observer].host_dir` and `[observer].bluepad32_dir` in
`hil_config.local.toml` at those checkouts, run `builder/build-observers.sh`,
and pass the output to `test-matrix.sh` with `HIL_BUNDLE_DIR`. The bundle
manifest records only the Bluepad32 commit, and its `-dirty` suffix is the
BTstack patch. A later rig release moves the observer firmware into this repo
and ships its bundles.
