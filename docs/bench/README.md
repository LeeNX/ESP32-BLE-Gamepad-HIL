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

- **2026-11-11** — ESP32-BLE-Gamepad `f97085e` (the v0.6.0 release bundles),
  all 3 boards × the 5 suite profiles (`default specials minimal maxbtn
  maxfeat`) on the **new reference rig, mimir-u26** (x86_64 Intel i5-3470T,
  Ubuntu 26.04.1, kernel 7.0.0-43, BlueZ 5.85, Intel 8087:07da Bluetooth), from
  a `--bench` + full observer matrix dispatch
  ([run 42984123196](https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/actions/runs/42984123196)).
  mimir took over the Raspberry Pi 3B+'s boards and adapters unchanged on
  2026-11-11, so this snapshot replaces the 2026-10-07 Pi one (v0.4.0). **The
  Pi figures quoted below are not from that older snapshot.** They come from
  the Pi's own `--bench` of the *identical* v0.6.0 bundles a day earlier
  ([run 42814840087](https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/actions/runs/42814840087), same rig commit `84dbf85`, same
  Arduino-ESP32 3.3.12 core), so the host is the only thing that changed
  between the two. `sinput` isn't in
  the suite, so it has no row: the observer matrix and `sinput_hil.py
  --latency` cover it.

| | |
|---|---|
| [`latency-vs-reportsize.svg`](latency-vs-reportsize.svg) | button latency (BLE-only + end-to-end p50) vs HID report size, per board |
| [`polling-rate.svg`](polling-rate.svg) | clean paced rate vs one report per connection interval, per profile/board |
| [`latency-distribution.svg`](latency-distribution.svg) | p50 / p90 / p99 spread per profile/board |

**Reading it:** every row ran solo (`links` 1) and dropped nothing. **All three
boards now connect at 8.75 ms**, the esp32dev included. With the same v0.6.0
bundles the Pi negotiated 48.75 ms for it (8.75 ms for the C3/S3), so the long
interval was the Pi's host/adapter with a classic ESP32, not the library or the
core. Button e2e p50 is **~4.6–4.7 ms** on the esp32s3 (one exception, below),
**~8.9–10.4 ms** on the esp32c3 and **~13.5 ms** on the esp32dev (Pi, same
bundles: ~4.3–4.5, ~7.6–8.4 and ~17.7–18.2 ms). The host is **not a uniform
win**: the esp32dev gains ~4 ms, but the esp32c3 and esp32s3 are ~0.2–1.5 ms
slower on mimir. The esp32dev's p99 also tightens, from 18–66 ms to ~21–23 ms. Why the esp32dev still sits ~9 ms behind the esp32s3 on
the same interval isn't settled. One outlier: **esp32s3 `default`** timed
13.4 ms p50 (p99 39 ms, max 110 ms) while its axis p50 (4.7 ms) and the other
four esp32s3 profiles (~4.6 ms) were normal; treat it as a noisy run until a
re-run says otherwise. Latency still doesn't track report size (5–28 B) within
a board. p99 is connection-interval jitter, so treat p50 as the signal.

**Not comparable with earlier snapshots.** Until rig 0.4.0 the bench timed from
the firmware's serial reply, after a blocking `tcdrain`, and the esp32c3's FTDI
bridge holds that reply up to 16 ms: the v0.3.0 snapshot's ~17.8 ms for the
esp32c3 was mostly the bridge. Timed from the write it's ~8 ms. Why it's still
~4–6 ms behind the esp32s3 on the same interval isn't settled: the same board
times at ~4.7 ms under `sinput_hil.py --latency` (the `sinput` profile, which
streams reports continuously). The esp32c3's **~63 Hz clean rate** is set by
its serial bridge, not BLE: paced commands wait for their replies (~16 ms PING
round trip, against ~1.6–3.6 ms on the CH340 boards; ~2.6–3.9 ms on the Pi).

**Clean rate vs the interval:** the red bar is one report per connection
interval (1000 ÷ interval). The esp32s3's 389–398 Hz (252–376 Hz on the Pi,
same bundles) is well above it: NimBLE sends several notifications per
connection event, so the interval bounds latency, not paced rate. The
esp32dev's 221–226 Hz roughly doubles the Pi's 112–131 Hz with its shorter
interval. The esp32c3 is unchanged
at ~63 Hz, still set by its bridge.

**C3 / S3 `--bench` reliability:** the cheap external USB-UART bridges drop a
byte now and then under the burst sweep — ~1 run in 5 needed a retry (all
passed on the second attempt). `SerialDev.command()` now retries once, and CI
retries a failed `--bench` run. The functional (non-bench) suite is unaffected.
