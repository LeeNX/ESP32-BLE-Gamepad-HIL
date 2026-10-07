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

- **2026-10-07** — ESP32-BLE-Gamepad `80a0d7c` (0.8.0, library `master`), all 3
  boards × the 5 suite profiles (`default specials minimal maxbtn maxfeat`) on
  the reference rig (Raspberry Pi 3B+, kernel 6.18.50, BlueZ 5.82), from the
  `--bench` + full observer matrix validation run for the v0.4.0 release
  ([run 37613401913](https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/actions/runs/37613401913)).
  The first snapshot timed from the serial write (rig 0.4.0); it replaces the
  2026-10-05 v0.3.0 one. `sinput` isn't in the suite, so it has no row: the
  observer matrix and `sinput_hil.py --latency` cover it.

| | |
|---|---|
| [`latency-vs-reportsize.svg`](latency-vs-reportsize.svg) | button latency (BLE-only + end-to-end p50) vs HID report size, per board |
| [`polling-rate.svg`](polling-rate.svg) | clean paced rate vs one report per connection interval, per profile/board |
| [`latency-distribution.svg`](latency-distribution.svg) | p50 / p90 / p99 spread per profile/board |

**Reading it:** every row ran solo (`links` 1) and dropped nothing. Button e2e
p50 is **~4.2–4.5 ms** on the esp32s3 and **~8.0–8.5 ms** on the esp32c3, both
on an 8.75 ms connection interval, and **~17.7 ms** on the esp32dev, whose
interval negotiated to 48.75 ms this run (43.75 ms in the v0.3.0 snapshot:
BlueZ settles it per connection). Latency still doesn't track report size
(5–28 B) within a board. p99 is connection-interval jitter, so treat p50 as the
signal.

**Not comparable with earlier snapshots.** Until rig 0.4.0 the bench timed from
the firmware's serial reply, after a blocking `tcdrain`, and the esp32c3's FTDI
bridge holds that reply up to 16 ms: the v0.3.0 snapshot's ~17.8 ms for the
esp32c3 was mostly the bridge. Timed from the write it's ~8 ms. Why it's still
~4 ms behind the esp32s3 on the same interval isn't settled: the same board
times at ~4.7 ms under `sinput_hil.py --latency` (the `sinput` profile, which
streams reports continuously). The esp32c3's **~63 Hz clean rate** is set by
its serial bridge, not BLE: paced commands wait for their replies (~16 ms PING
round trip, against ~2.6–3.9 ms on the CH340 boards).

**Clean rate vs the interval:** the red bar is one report per connection
interval (1000 ÷ interval). The esp32s3's 258–267 Hz is well above it: NimBLE
sends several notifications per connection event, so the interval bounds
latency, not paced rate.

**C3 / S3 `--bench` reliability:** the cheap external USB-UART bridges drop a
byte now and then under the burst sweep — ~1 run in 5 needed a retry (all
passed on the second attempt). `SerialDev.command()` now retries once, and CI
retries a failed `--bench` run. The functional (non-bench) suite is unaffected.
