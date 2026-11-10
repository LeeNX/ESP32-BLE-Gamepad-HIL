# Partition tables

Every flash layout the rig uses. The two firmwares are built against their own tables. A multi-slot board
(`tester/flash.py`'s default, [docs/multislot-ota.md](../../docs/multislot-ota.md)) gets `multislot.csv` written
instead, and either firmware's app runs from any of its slots.

| File | Used by | Layout |
|---|---|---|
| `runner-dual-ota-4mb.csv` | hil_runner build, esp32dev / esp32c3 (`firmware/platformio.ini`) | nvs, otadata, `app0` + `app1` 1.25 MB each, spiffs (unused), coredump |
| `runner-dual-ota-8mb.csv` | hil_runner build, esp32s3 (8 MB devkit) | the same, `app0` + `app1` 3.2 MB each |
| `observer-factory-ota.csv` | Bluepad32 observer build, every board (`firmware/observer`, `builder/build-observers.sh`) | nvs, otadata, phy_init, factory 1.1 MB + `ota_0` / `ota_1` 1.25 MB each |
| `multislot.csv` | the board, written by `tester/flash.py` / `builder/make_multislot.py` | nvs, otadata, phy_init, `ota_0`..`ota_3` 960 KB each, coredump (first 4 MB on the S3) |

The two runner tables are byte-for-byte copies of Arduino-ESP32's `default.csv` / `default_8MB.csv`, the tables the
runner used implicitly before they were pinned here. A core update can no longer change them unnoticed.

The build tables apply only with legacy flashing (`HIL_MULTISLOT=0`): a bundle's `partitions.bin` comes from them.
On a multi-slot board `flash.py` keeps `multislot.csv` and writes only the app.
