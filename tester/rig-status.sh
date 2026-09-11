#!/usr/bin/env bash
# What's running on the HIL rig right now (or the last run's verdict).
#   ssh <tester> ESP32-BLE-Gamepad-HIL/tester/rig-status.sh
#   ssh <tester> ESP32-BLE-Gamepad-HIL/tester/rig-status.sh -f   # + the live log (blocks; Ctrl-C to stop)
set -euo pipefail
cd "$(dirname "$0")/.."

status=$(python3 host/hil/riglock.py status)
echo "$status"

follow=0
for a in "$@"; do
  case "$a" in
    -f | -v | --follow) follow=1 ;;
  esac
done
[[ $follow == 1 ]] || exit 0

# Newest file picks what to follow. `--by-board` writes one fixed-name
# results/lane-<board>.log per board (tail all of them together); a lone
# `tester/test.sh` run -- under --by-board or standalone -- writes a stamped
# results/log-<board>-<profile>-<stamp>.txt. Don't mix the two: a lane log
# already contains its board's log-*.txt content, so following both would
# double up the same lines.
# filenames are our own board/profile/stamp -- no globs/spaces to trip `ls -t` up
# shellcheck disable=SC2012
newest=$(ls -t results/lane-*.log results/log-*.txt 2>/dev/null | head -1) || true
if [[ -z "$newest" ]]; then
  echo "(no results/lane-*.log or results/log-*.txt yet)" >&2
  exit 0
fi
case "$newest" in
  results/lane-*.log) mapfile -t logs < <(ls -t results/lane-*.log) ;;
  *) logs=("$newest") ;;
esac

echo
if [[ "$status" == "RIG BUSY"* ]]; then
  echo "-- following (Ctrl-C to stop): ${logs[*]} --"
  exec tail -n 20 -f "${logs[@]}"
else
  echo "-- rig idle; tail of the last log: ${logs[*]} --"
  tail -n 40 "${logs[@]}"
fi
