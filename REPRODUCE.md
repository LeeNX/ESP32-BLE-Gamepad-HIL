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
| `firmware-bundles/matrix/<board>-bp32obs-<bp32sha8>-<rigsha8>/` | the Bluepad32 observer firmware for the observer matrix (see below) |
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

**The observer firmware ships in this archive** as the `bp32obs` bundles. It
is built from the rig's `firmware/observer/` against Bluepad32, whose commit
`index.json` records under `bluepad32` (each observer manifest also has it as
`lib_sha`, plus the rig commit as `rig_sha`). The `-dirty` in its describe is
the BTstack patch Bluepad32 ships in `external/patches`, applied at build time.

`tester/test-matrix.sh` takes these bundles as they are: from `suite/` (as in
the steps above), point `HIL_BUNDLE_DIR` at them with
`HIL_BUNDLE_DIR="$PWD/../firmware-bundles"`. To rebuild one, run
`builder/build-observers.sh` from a full rig checkout; with no
`[observer].bluepad32_dir` set it fetches the pinned Bluepad32
(`[observer].bluepad32_repo` @ `bluepad32_ref`) itself.
