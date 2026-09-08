#!/usr/bin/env bash
# TESTER role: flash + test every bundle in $HIL_BUNDLE_DIR (default ~/hil-bundles),
# one retry each. Args here pass to tester/test.sh; $HIL_TEST_FILTER (if set) is
# added as a single `pytest -k <expr>` for a focused run.
#
#   tester/test-all.sh --bench
#   HIL_BUNDLE_DIR=/tmp/bundles tester/test-all.sh -k buttons
#   HIL_TEST_FILTER='battery or descriptor' tester/test-all.sh --bench   # focused (what CI does)
#   tester/test-all.sh --by-board          # one lane per board, in parallel (functional only)
#
# --by-board runs the boards concurrently -- each lane loops its own profiles
# sequentially -- for ~3x on a 3-board rig. Timing-insensitive checks only: it
# refuses --bench, which must stay sequential + solo. Lane output goes to
# results/lane-<board>.log and is replayed in order at the end.
#
# Takes the rig lock once for the whole batch (no-op if the caller -- CI, or
# `tester/rig-lock.sh -- tester/test-all.sh` -- already holds it).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=tester/rig-lock.sh
source tester/rig-lock.sh
rig_lock_acquire || exit $?

BY_BOARD=0
rest=()
for a in "$@"; do
  case "$a" in
    --by-board) BY_BOARD=1 ;;
    *) rest+=("$a") ;;
  esac
done
set -- ${rest[@]+"${rest[@]}"}

kargs=()
[ -n "${HIL_TEST_FILTER:-}" ] && kargs=(-k "$HIL_TEST_FILTER")
BUNDLE_DIR=${HIL_BUNDLE_DIR:-$HOME/hil-bundles}
trc=0

ts() { date +%H:%M:%S; }

# Bundle dirs are <board>-<profile>-<sha>; board names carry no dash.
bundle_board() { basename "$1" | cut -d- -f1; }

all_bundles() {
  local b
  for b in "$BUNDLE_DIR"/*/; do
    [ -f "$b/manifest.json" ] && printf '%s\n' "${b%/}"
  done
}

# one bundle, one retry; returns non-zero on a second failure
test_bundle() {
  ./tester/test.sh "$1" "${@:2}" "${kargs[@]}" \
    || ./tester/test.sh "$1" "${@:2}" "${kargs[@]}"
}

run_lane() {  # <board> <pytest-args...> ; loop its bundles. rc 1 on any failure.
  local board=$1 b lrc=0
  shift
  while IFS= read -r b; do
    [ "$(bundle_board "$b")" = "$board" ] || continue
    test_bundle "$b" "$@" || lrc=1
  done < <(all_bundles)
  return $lrc
}

if [ "$BY_BOARD" = 1 ]; then
  for a in "$@"; do
    [ "$a" != "--bench" ] || {
      echo "test-all.sh: --by-board is functional-only; run --bench separately" >&2
      exit 2
    }
  done
  mkdir -p results
  mapfile -t boards < <(all_bundles | while IFS= read -r b; do bundle_board "$b"; done | sort -u)
  [ "${#boards[@]}" -gt 0 ] || { echo "no bundles in $BUNDLE_DIR" >&2; exit 0; }
  echo "== [$(ts)] by-board: ${boards[*]}  (one parallel lane each)"
  pids=()
  for board in "${boards[@]}"; do
    run_lane "$board" "$@" > "results/lane-$board.log" 2>&1 &
    pids+=("$!")
  done
  for p in "${pids[@]}"; do
    wait "$p" || trc=1
  done
  for board in "${boards[@]}"; do
    echo "========== lane: $board =========="
    cat "results/lane-$board.log" 2>/dev/null || true
  done
  echo "== [$(ts)] by-board done (rc $trc)"
  exit "$trc"
fi

while IFS= read -r b; do
  # one retry: the C3/S3 external UART bridges drop a byte under load now and
  # then (README "Benchmarking / Rig note").
  test_bundle "$b" "$@" || trc=$?
done < <(all_bundles)
exit "$trc"
