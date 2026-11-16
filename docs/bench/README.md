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

- **2026-11-16** — ESP32-BLE-Gamepad `f97085e` (the v0.6.0 release bundles),
  all 3 boards × the 5 suite profiles (`default specials minimal maxbtn
  maxfeat`) on the reference rig **mimir-u26** (x86_64 Intel i5-3470T, Ubuntu
  26.04.1, kernel 7.0.0-43, BlueZ 5.85), now on a **TP-Link UB500 USB dongle
  (Realtek RTL8761BU, `hci1`)** with the onboard Intel 8087:07da disabled
  ([run 43869977278](https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/actions/runs/43869977278),
  rig `479c186`). The Intel wedged under parallel pairing, which is why it was
  swapped out. The **Intel figures quoted below** are from the 2026-11-11
  snapshot this one replaces
  ([run 42984123196](https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/actions/runs/42984123196)).
  It ran the *identical* bundles on the same host, boards and serial adapters,
  so the Bluetooth adapter is the only thing that changed. `sinput` isn't in
  the suite, so it has no row: the observer matrix and `sinput_hil.py
  --latency` cover it.

| | |
|---|---|
| [`latency-vs-reportsize.svg`](latency-vs-reportsize.svg) | button latency (BLE-only + end-to-end p50) vs HID report size, per board |
| [`polling-rate.svg`](polling-rate.svg) | clean paced rate vs one report per connection interval, per profile/board |
| [`latency-distribution.svg`](latency-distribution.svg) | p50 / p90 / p99 spread per profile/board |

**Reading it:** every row ran solo (`links` 1) and dropped nothing. **All three
boards now connect at 7.5 ms** (8.75 ms on the Intel; the retired Pi gave the
esp32dev 48.75 ms). Button e2e p50 is **~7.4–7.6 ms** on the esp32dev (one
exception, below), **~8.7–8.9 ms** on the esp32c3 and **~6.7–7.4 ms** on the
esp32s3 (Intel, same bundles: ~13.5, ~8.9–10.4 and ~4.6–4.7 ms). So the
esp32dev, which sat ~9 ms behind the esp32s3 on the Intel, now roughly matches
it: that gap was the Intel adapter with a classic ESP32, not the board. The
dongle is **not a uniform win** at the median: the esp32s3's p50 rises
~2–2.7 ms. Its tail is much tighter though: p99 **~7.7–7.9 ms** against
~13.6–13.8 ms on the Intel, so nearly every press lands inside one interval.
p99 improves on every board: esp32dev ~13.7–15.3 ms (Intel ~20.5–22.6), esp32c3
~16.1–19.6 ms (Intel ~17.8–26.7).

One outlier: **esp32dev `default`** timed 12.7 ms p50 (p90 14.6 ms), about one
interval above its other four profiles (~7.4–7.6 ms), with a normal min and
max. The outlier moves between profiles from run to run. The bench/full run
before this one
([run 43849727351](https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/actions/runs/43849727351))
had it on esp32dev `specials` (axis p50 14.7 ms) instead, and the Intel
snapshot had it on esp32s3 `default`. So treat it as a run-level phase effect,
not something about the profile. Latency still doesn't track report size
(5–28 B) within a board. Treat p50 as the signal.

**Not comparable with earlier snapshots.** Until rig 0.4.0 the bench timed from
the firmware's serial reply, after a blocking `tcdrain`, and the esp32c3's FTDI
bridge holds that reply up to 16 ms: the v0.3.0 snapshot's ~17.8 ms for the
esp32c3 was mostly the bridge. Timed from the write it's ~8–9 ms. Why it's
still ~1.5–2 ms behind the esp32s3 on the same interval (~4–6 ms on the Intel)
isn't settled: the same board
times at ~4.7 ms under `sinput_hil.py --latency` (the `sinput` profile, which
streams reports continuously). The esp32c3's **~63 Hz clean rate** is set by
its serial bridge, not BLE: paced commands wait for their replies (~16 ms PING
round trip, against ~1.6–3.6 ms on the CH340 boards; ~2.6–3.9 ms on the Pi).

**Clean rate vs the interval:** the red bar is one report per connection
interval (1000 ÷ interval, ~133 Hz at 7.5 ms). The esp32s3's 403–412 Hz
(389–398 Hz on the Intel) is well above it: NimBLE sends several notifications
per connection event, so the interval bounds latency, not paced rate. The
esp32dev's 223–229 Hz (221–226 Hz on the Intel) is above it too. The esp32c3
is unchanged at ~63 Hz, still set by its bridge.

**C3 / S3 `--bench` reliability:** the cheap external USB-UART bridges drop a
byte now and then under the burst sweep — ~1 run in 5 needed a retry (all
passed on the second attempt). `SerialDev.command()` now retries once, and CI
retries a failed `--bench` run. The functional (non-bench) suite is unaffected.
