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

- **2026-10-05** — ESP32-BLE-Gamepad `80a0d7c` (0.8.0, library `master`), all 3
  boards × the 5 suite profiles (`default specials minimal maxbtn maxfeat`) on
  the reference rig (Raspberry Pi 3B+, kernel 6.18.50, BlueZ 5.82), from the
  full-matrix + `--bench` CI run for the v0.3.0 release
  ([run 37295281897](https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/actions/runs/37295281897)).
  Replaces the 2026-09-18 v0.2.6 snapshot (library `9282be1`). `sinput` isn't
  in the suite, so it has no row: the observer matrix covers it.

| | |
|---|---|
| [`latency-vs-reportsize.svg`](latency-vs-reportsize.svg) | button latency (BLE-only + end-to-end p50) vs HID report size, per board |
| [`polling-rate.svg`](polling-rate.svg) | clean paced rate vs the connection-interval ceiling, per profile/board |
| [`latency-distribution.svg`](latency-distribution.svg) | p50 / p90 / p99 spread per profile/board |

> **Correction (2026-10-06, rig 0.3.2):** the esp32c3's latency in this
> snapshot is a measurement artifact, not BLE. The bench started timing only
> after the firmware's serial reply (and a blocking `tcdrain`), and the
> esp32c3's FTDI bridge holds that reply up to 16 ms, so its ~17.8 ms is mostly
> the bridge. With the timing fixed (from the serial write, no drain) a quick
> bench measures the esp32c3 at **~7.5 ms** p50 (`minimal`, 8.75 ms interval),
> and the same report times at ~4.7–4.9 ms on hidraw and evdev
> (`sinput_hil.py --latency`). The contention explanation below doesn't hold
> for it. The esp32c3's clean rate is probably bridge-bound for the same
> reason. The CH340 boards (esp32dev, esp32s3) reply in ~2.6 ms and are much
> less affected. A full re-bench with the fix replaces this snapshot.

**Reading it:** the connection interval moved since v0.2.6. It was 48.75 ms on
every board then; now it is **8.75 ms** on the esp32c3 and esp32s3 and
**43.75 ms** on the esp32dev. The rig itself didn't change (same kernel and
BlueZ), so this follows the library update (`9282be1` → `80a0d7c`). Button e2e
p50 follows suit: **~4.9 ms** on the esp32s3 (was ~18.6 ms everywhere), **~13.6 ms**
on the esp32dev, and **~17.8 ms** on the esp32c3. As before, latency doesn't track
report size (5–28 B) within a board. p99 is dominated by connection-interval
jitter, so treat p50 as the signal. Zero dropped events in every row.

**Contention differs per board in this snapshot**: the `links` column is **3**
for the esp32c3, **2** for the esp32dev and **1** for the esp32s3. The boards bench
in that order after the functional `--by-board` matrix, with one fewer link
live each time: the first benches with all three up, the last alone. That
confounds the comparison between boards: the esp32c3 and esp32s3
negotiate the same 8.75 ms interval, yet the esp32c3 (3 links) shows ~17.8 ms p50
and ~63 Hz clean rate against the esp32s3's ~4.9 ms and 258–276 Hz (1 link) --
though see the correction above: most of that gap is the esp32c3's FTDI, not
the links.
Compare a board with itself across snapshots, not boards with each other; a
like-for-like comparison needs a `--bench`-only run from an unbonded adapter.

**C3 / S3 `--bench` reliability:** the cheap external USB-UART bridges drop a
byte now and then under the burst sweep — ~1 run in 5 needed a retry (all
passed on the second attempt). `SerialDev.command()` now retries once, and CI
retries a failed `--bench` run. The functional (non-bench) suite is unaffected.
