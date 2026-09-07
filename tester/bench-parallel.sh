#!/usr/bin/env bash
# TESTER role: the multi-gamepad BLE contention experiment -- sweep every wired
# board solo, then all of them at once on one adapter, and diff the two.
# Needs the same deps as tester/test.sh (esptool + tester/requirements.txt).
# Wraps `python -m hil.bench_parallel`; all args pass straight through.
#
#   tester/bench-parallel.sh                       # detected boards, flash once, short sweep
#   tester/bench-parallel.sh --no-flash            # reuse what's already on the boards
#   tester/bench-parallel.sh --boards "esp32dev esp32c3"
#   tester/bench-parallel.sh --keep-peers-connected   # solo pass keeps peers connected but idle
#   tester/bench-parallel.sh --full               # full sweep instead of the short one
set -euo pipefail
cd "$(dirname "$0")/.."
VENV=${HIL_VENV:-$HOME/.venvs/hil}

# one physical rig -- serialise against CI / other local runs (rig-lock.sh).
# No-op if we're already under a lock (HIL_RIG_LOCK_HELD).
export HIL_RUN_WHAT="${HIL_RUN_WHAT:-bench-parallel $*}"
exec tester/rig-lock.sh -- env PYTHONPATH=host "$VENV/bin/python" -m hil.bench_parallel "$@"
