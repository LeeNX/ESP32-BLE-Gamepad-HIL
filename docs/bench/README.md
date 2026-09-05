# HIL benchmark snapshot

`tester/test.sh <bundle> --bench` runs `host/hil/bench.py`'s latency / throughput
sweep per flashed profile and drops a `results/bench-<board>-<profile>-*.json`;
`python -m hil.charts results/` turns the accumulated JSON into
[`bench-table.md`](bench-table.md) and three SVGs. This directory is a committed
snapshot of that output — refresh it after a meaningful firmware/library change:

```bash
# on the tester, after a full --bench sweep of every bundle:
python -m hil.charts results/
cp results/bench-table.md results/*.svg <this repo>/docs/bench/
```

## This snapshot

- **2026-09-05** — ESP32-BLE-Gamepad `v0.7.5-rc0-9-gf7abaaa`, all 3 boards ×
  6 profiles on the reference rig (Raspberry Pi 3B+, kernel 6.18.34, BlueZ 5.82).
  `esp32dev` on its onboard CP2102; `esp32c3` / `esp32s3` on an external
  USB-UART bridge on UART0 (PL2303 / CH340) with the native-USB `flash_port`.

| | |
|---|---|
| [`latency-vs-reportsize.svg`](latency-vs-reportsize.svg) | button latency (BLE-only + end-to-end p50) vs HID report size, per board |
| [`polling-rate.svg`](polling-rate.svg) | clean paced rate vs the connection-interval ceiling, per profile/board |
| [`latency-distribution.svg`](latency-distribution.svg) | p50 / p90 / p99 spread per profile/board |

**Reading it:** button e2e p50 is a flat **~18.6 ms** on every board and profile
— latency does not track report size (3–28 B) or chip. p99 is dominated by
connection-interval jitter (one 48.75 ms interval) and swings run to run
depending on where the sample lands; treat p50 as the signal. Zero dropped
events across 200 paced presses per profile. Clean paced rate 80–133 Hz — in
this rig it's the USB-serial command channel (or, on the C3/S3, the external
bridge) that saturates first, not BLE. The connection interval is 48.75 ms on
every profile: the library does not request a fast one.

**C3 / S3 `--bench` reliability:** the cheap external USB-UART bridges drop a
byte now and then under the burst sweep — ~1 run in 5 needed a retry (all
passed on the second attempt). `SerialDev.command()` now retries once, and CI
retries a failed `--bench` run. The functional (non-bench) suite is unaffected.
