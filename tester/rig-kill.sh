#!/usr/bin/env bash
# Abort whatever's running on the HIL rig right now, in one command -- the
# whole process group (test-all.sh, its --by-board lanes, the live tail,
# pytest, esptool), not just the top-level pid. Non-interactive scripts run
# with job control off, so `&` background jobs never get their own process
# group -- everything test-all.sh spawned shares HIL_RUN_PID's group, which
# is what makes a single `kill -TERM -<pgid>` catch all of it.
#   ssh <tester> ESP32-BLE-Gamepad-HIL/tester/rig-kill.sh        # asks first
#   ssh <tester> ESP32-BLE-Gamepad-HIL/tester/rig-kill.sh -y     # no prompt (scripts)
#
# flock releases on process death regardless (see rig-status.sh's "Rig lock /
# status" note in README.md); this also folds the killed run into rig-status.json's
# `last` so a follow-up `rig-status.sh` reads clean instead of a stale BUSY/DEAD.
set -euo pipefail
cd "$(dirname "$0")/.."

pid=$(python3 host/hil/riglock.py pid) || { echo "rig idle -- nothing to kill" >&2; exit 0; }

if ! kill -0 "$pid" 2>/dev/null; then
  echo "recorded pid $pid is already dead (stale status) -- clearing it" >&2
  python3 host/hil/riglock.py write idle 0
  exit 0
fi

pgid=$(ps -o pgid= "$pid" 2>/dev/null | tr -d ' ')
[ -n "$pgid" ] || pgid=$pid

yes=0
for a in "$@"; do
  case "$a" in
    -y | --yes) yes=1 ;;
  esac
done

if [ "$yes" != 1 ]; then
  python3 host/hil/riglock.py status >&2
  read -r -p "Kill this run (process group $pgid)? [y/N] " ans
  case "$ans" in
    y | Y) ;;
    *) echo "aborted" >&2; exit 1 ;;
  esac
fi

echo "== sending SIGTERM to process group $pgid" >&2
kill -TERM "-$pgid" 2>/dev/null || true

for _ in $(seq 1 30); do
  kill -0 "$pid" 2>/dev/null || {
    echo "== stopped" >&2
    python3 host/hil/riglock.py write idle 143 2>/dev/null || true
    exit 0
  }
  sleep 0.5
done

echo "== still alive after 15s -- sending SIGKILL to process group $pgid" >&2
kill -KILL "-$pgid" 2>/dev/null || true
sleep 0.5
python3 host/hil/riglock.py write idle 137 2>/dev/null || true
echo "== killed" >&2
