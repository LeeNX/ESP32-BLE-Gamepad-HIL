#!/usr/bin/env bash
# One physical HIL rig -- serialise every run against it (CI *and* local), so a
# `git reset --hard` / flash / BLE session never lands on top of another.
#
# The lock is an flock on $XDG_CACHE_HOME/esp32-hil/rig.lock (auto-released when
# the holder dies -- no stale lockfile). host/hil/riglock.py keeps a sibling
# rig-status.json with who/what/where for `tester/rig-status.sh`.
#
# Wrapper (owns the whole lifecycle):
#   tester/rig-lock.sh [--wait SECS|--no-wait] -- <cmd...>
# Sourced (for scripts that can't be wrapped -- installs an EXIT trap):
#   source tester/rig-lock.sh; rig_lock_acquire || exit $?
# Re-entrant: a no-op when HIL_RIG_LOCK_HELD is already set (nested test.sh in a
# batch just inherits the parent's lock).

_RL_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
_RL_PY="$(cd "$_RL_DIR/.." && pwd)/host/hil/riglock.py"
_RL_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/esp32-hil"
_RL_LOCK="$_RL_CACHE/rig.lock"

# rig_lock_acquire [--wait SECS | --no-wait]
# 0 = held (ours, or just acquired); 75 = someone else has it.
rig_lock_acquire() {
  local wait=2700
  while [ $# -gt 0 ]; do
    case "$1" in
      --wait) wait="$2"; shift 2 ;;
      --no-wait) wait=0; shift ;;
      *) break ;;
    esac
  done
  if [ -n "${HIL_RIG_LOCK_HELD:-}" ]; then
    # Re-entrant (nested test.sh in a batch, or CI's remote wrapper --
    # hil.yml's "Flash + test on the tester" step takes its own raw flock and
    # pre-exports this var before calling us). We don't own the actual lock
    # here, but the health sampler is independent of that -- start it once
    # regardless, or CI never gets a health-timeline CSV at all (only the
    # per-test before/after snapshots), since CI never hits the fresh-acquire
    # branch below.
    _rl_health_start
    trap '_rl_health_stop' EXIT
    return 0
  fi

  if ! command -v flock >/dev/null 2>&1; then
    # no util-linux (e.g. a macOS one-box dev run) -- can't serialise, carry on.
    echo "rig-lock: flock not found -- running WITHOUT the rig lock" >&2
    return 0
  fi

  mkdir -p "$_RL_CACHE"
  exec 9>"$_RL_LOCK"
  if [ "$wait" -eq 0 ]; then
    flock -n 9 || { _rl_busy; return 75; }
  else
    flock -w "$wait" 9 || { _rl_busy; return 75; }
  fi

  export HIL_RIG_LOCK_HELD=$$ HIL_RUN_PID=$$
  python3 "$_RL_PY" write busy || true
  _rl_health_start
  trap '_rl_on_exit "$?"' EXIT
  return 0
}

_rl_busy() {
  echo "== HIL rig is busy -- current holder:" >&2
  python3 "$_RL_PY" status >&2 || true
}

# Background sampler for the lock's whole lifetime: temp/freq/under-voltage/
# load every $HIL_HEALTH_INTERVAL_SECS (default 15s), so a crash mid-run
# leaves a timeline behind instead of just a before/after snapshot. Linux-only
# (sysfs paths) -- silently a no-op elsewhere (e.g. a macOS one-box dev run).
# See hil-rig-usb-bus-crash-sep11 memory for why this exists.
_rl_health_start() {
  # Guards against every --by-board lane (each a nested, re-entrant
  # rig_lock_acquire) starting its own redundant sampler -- exported so
  # child processes see it, not just this one.
  [ -n "${HIL_HEALTH_STARTED:-}" ] && return 0
  export HIL_HEALTH_STARTED=1
  [ -d /sys/class/thermal ] || return 0
  local out
  out="results/health-timeline-$(date +%Y%m%d-%H%M%S)-$$.csv"
  mkdir -p results
  echo "ts,temp_c,freq_mhz,undervoltage,loadavg" > "$out"
  local uv_alarm="" h
  for h in /sys/class/hwmon/hwmon*/name; do
    [ "$(cat "$h" 2>/dev/null)" = "rpi_volt" ] || continue
    uv_alarm="$(dirname "$h")/in0_lcrit_alarm"
    break
  done
  (
    while true; do
      sleep "${HIL_HEALTH_INTERVAL_SECS:-15}"
      local temp freq uv load
      temp=$(($(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0) / 1000))
      freq=$(($(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || echo 0) / 1000))
      uv=$([ -n "$uv_alarm" ] && cat "$uv_alarm" 2>/dev/null || echo "")
      load=$(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || echo "?")
      printf '%s,%s,%s,%s,%s\n' "$(date -Iseconds)" "$temp" "$freq" "$uv" "$load" >> "$out"
    done
  ) &
  _RL_HEALTH_PID=$!
}

_rl_health_stop() {
  [ -n "${_RL_HEALTH_PID:-}" ] && kill "$_RL_HEALTH_PID" 2>/dev/null
}

_rl_on_exit() {
  local rc=$1
  _rl_health_stop
  python3 "$_RL_PY" write idle "$rc" || true
}

# Executed directly (not sourced): wrapper / --status-write passthrough.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  set -uo pipefail

  if [ "${1:-}" = "--status-write" ]; then
    shift
    exec python3 "$_RL_PY" write "$@"
  fi

  wait_args=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --wait) wait_args=(--wait "$2"); shift 2 ;;
      --no-wait) wait_args=(--no-wait); shift ;;
      --) shift; break ;;
      *) break ;;
    esac
  done

  rig_lock_acquire "${wait_args[@]}" || exit $?
  [ $# -gt 0 ] || { echo "rig-lock.sh: nothing to run" >&2; exit 2; }
  "$@"                       # EXIT trap writes the idle/rc status
  exit $?
fi
