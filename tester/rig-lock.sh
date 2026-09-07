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
  [ -n "${HIL_RIG_LOCK_HELD:-}" ] && return 0

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
  trap 'python3 "'"$_RL_PY"'" write idle "$?" || true' EXIT
  return 0
}

_rl_busy() {
  echo "== HIL rig is busy -- current holder:" >&2
  python3 "$_RL_PY" status >&2 || true
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
