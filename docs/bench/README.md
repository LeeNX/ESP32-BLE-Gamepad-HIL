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

- **2026-09-18** — ESP32-BLE-Gamepad `9282be1` (the `HIL_LIB_REF` fork branch
  pending merge to `master` — see [RELEASE.md](../../RELEASE.md)), all 3
  boards × the current 4 profiles (`default specials minimal maxbtn`) on the
  reference rig (Raspberry Pi 3B+, kernel 6.18.50, BlueZ 5.82), from the
  full-matrix + `--bench` CI run for the v0.2.6 release
  ([run 35344716184](https://github.com/LeeNX/ESP32-BLE-Gamepad-HIL/actions/runs/35344716184)).
  Replaces the 2026-09-05 snapshot, which still had the pre-profile-fold
  6-profile set (`minimal reports maxbtn specials default signed-axes`, PR
  #21 folded these to 4).

| | |
|---|---|
| [`latency-vs-reportsize.svg`](latency-vs-reportsize.svg) | button latency (BLE-only + end-to-end p50) vs HID report size, per board |
| [`polling-rate.svg`](polling-rate.svg) | clean paced rate vs the connection-interval ceiling, per profile/board |
| [`latency-distribution.svg`](latency-distribution.svg) | p50 / p90 / p99 spread per profile/board |

**Reading it:** button e2e p50 is a flat **~18.6 ms** on every board and profile
— latency does not track report size (3–28 B) or chip. p99 is dominated by
connection-interval jitter (one 48.75 ms interval) and swings run to run
depending on where the sample lands; treat p50 as the signal. Zero dropped
events across 200 paced presses per profile. The connection interval is
48.75 ms on every profile: the library does not request a fast one.

**Contention in this snapshot:** every row shows `links: 3` — all three
boards stayed bonded/connected to the adapter for the whole run (this
full-matrix release validation runs the functional `--by-board` matrix
immediately before `--bench` in the same CI job, and nothing disconnects the
other two boards in between), not the solo `links: 1` conditions this
snapshot is meant to represent. `esp32c3`'s clean paced rate is the most
visibly affected — **62–63 Hz**, roughly half the ~130 Hz in the 2026-09-05
solo snapshot; `esp32dev`/`esp32s3` are closer to normal (88–131 Hz) but
still somewhat below their old baselines on some profiles. Button e2e p50
itself is unaffected by this (still flat ~18.6 ms) — `clean_rate` is the
metric contention degrades, per the `links` column's own purpose (see the
table's footnote). Treat the `clean Hz` column in this snapshot with that
caveat; a true solo re-measurement would need a `--bench`-only run starting
from an unbonded adapter, not one immediately following `--by-board`.

**C3 / S3 `--bench` reliability:** the cheap external USB-UART bridges drop a
byte now and then under the burst sweep — ~1 run in 5 needed a retry (all
passed on the second attempt). `SerialDev.command()` now retries once, and CI
retries a failed `--bench` run. The functional (non-bench) suite is unaffected.
