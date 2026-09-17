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
# $HIL_BUNDLE_STAGE: a dir the caller rsync'd bundles into. It is swapped into
# $HIL_BUNDLE_DIR *under the rig lock* -- an unlocked `rsync --delete` straight
# to ~/hil-bundles races a concurrent run (CI vs a local scripts/hil.sh) that
# does the same. Stage to a private dir, hand us the path, we swap atomically.
#
# --by-board runs the boards concurrently -- each lane loops its own profiles
# sequentially -- for ~3x on a 3-board rig. Timing-insensitive checks only: it
# refuses --bench, which must stay sequential + solo. Lane output goes to
# results/lane-<board>.log and is replayed in order at the end.
#
# Pairing is adapter-global (one BT radio on the rig), so a lane mid-pairing
# can't share airtime with another lane's already-connected, actively-
# transmitting link -- a flock around just the `pair` call (conftest.py's
# pair.lock) isn't enough, since it doesn't stop an *already-paired* lane's
# traffic from starving a *different* lane's fresh pairing attempt (seen
# live: esp32s3 failed 51 tests mid-pair while esp32dev was mid-suite on the
# same adapter). So each bundle now runs in two phases: phase1_turn() takes
# the rig's BT radio one board at a time for flash+pair+test_connection.py
# (the quick "is this board alive" smoke check), and no lane starts its
# phase-2 (the rest of the suite, parallel as before) until every board has
# finished phase 1 for that profile round.
#
# Takes the rig lock once for the whole batch (no-op if the caller -- CI, or
# `tester/rig-lock.sh -- tester/test-all.sh` -- already holds it).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=tester/rig-lock.sh
source tester/rig-lock.sh
rig_lock_acquire || exit $?

# swap staged bundles into place, now that we hold the lock
BUNDLE_DIR=${HIL_BUNDLE_DIR:-$HOME/hil-bundles}
if [ -n "${HIL_BUNDLE_STAGE:-}" ] && [ -d "$HIL_BUNDLE_STAGE" ] \
   && [ "$HIL_BUNDLE_STAGE" != "$BUNDLE_DIR" ]; then
  echo "== staging bundles: $HIL_BUNDLE_STAGE -> $BUNDLE_DIR"
  rm -rf "$BUNDLE_DIR"
  mv "$HIL_BUNDLE_STAGE" "$BUNDLE_DIR"
fi

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
trc=0
PY=$(command -v python3 || echo "${HIL_VENV:-$HOME/.venvs/hil}/bin/python")

ts() { date +%H:%M:%S; }

# Bundle dirs are <board>-<profile>-<sha>; board names carry no dash (all
# current ones: esp32c3/esp32dev/esp32s3), but profile names might (see
# summarize.py's _bundle(), which already accounts for a "signed-axes"-style
# profile) -- so bundle_profile strips the leading "<board>-" and the
# trailing "-<sha>" (a git short hash, itself always dash-free) rather than
# just taking the 2nd dash-separated field, which would silently truncate a
# dashed profile (and every consumer of $round below, junit filenames
# included) to its first component.
bundle_board() { basename "$1" | cut -d- -f1; }
bundle_profile() {
  local name
  name=$(basename "$1")
  name=${name#*-}
  printf '%s\n' "${name%-*}"
}

all_bundles() {
  local b
  for b in "$BUNDLE_DIR"/*/; do
    [ -f "$b/manifest.json" ] && printf '%s\n' "${b%/}"
  done
}

# record_retry <bundle> <n> -- note that <bundle> needed <n> extra attempt(s)
# this run, in results/retries-<board>-<profile>.txt (read by summarize.py's
# "retries" column). A board that's slower than its neighbors is often just a
# board that had to retry -- a flaky pairing or a dropped byte on the C3/S3
# UART bridge (README "Rig note") -- and that's invisible in the final junit
# (a retry's failed first attempt isn't in it) unless something records it
# separately. Accumulates across phase1 and phase2 within one run; callers
# don't need to know the other phase's count.
record_retry() {
  local bundle=$1 n=$2
  [ "$n" -gt 0 ] || return 0
  local file
  file="results/retries-$(bundle_board "$bundle")-$(bundle_profile "$bundle").txt"
  local cur=0
  [ -f "$file" ] && cur=$(cat "$file" 2>/dev/null || echo 0)
  mkdir -p results
  echo $(( cur + n )) > "$file"
}

# one bundle, one retry; returns non-zero on a second failure
test_bundle() {
  ./tester/test.sh "$1" "${@:2}" "${kargs[@]}" && return 0
  record_retry "$1" 1
  ./tester/test.sh "$1" "${@:2}" "${kargs[@]}"
}

# roll every bundle's junit into results/summary.md and echo it (CI lifts this
# into the job summary; locally it's the at-a-glance board x profile matrix)
summarize_all() {
  ls results/junit-*.xml >/dev/null 2>&1 || return 0
  echo
  "$PY" host/hil/summarize.py results/junit-*.xml --out results/summary.md 2>/dev/null || true
}

# --- by-board phase1: flash+pair+smoke, one board at a time, rig-wide ------
PHASE1_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/esp32-hil"
PHASE1_LOCK="$PHASE1_CACHE/byboard-phase1.lock"
PHASE1_ARRIVED="$PHASE1_CACHE/byboard-phase1-arrived"
CONN_TEST="$(pwd)/host/tests/test_connection.py"
HAVE_FLOCK=0
command -v flock >/dev/null 2>&1 && HAVE_FLOCK=1

# round_barrier <board> <key> -- record <board> as done with <key> (a
# "<round>.phase1" or "<round>.phase2" checkpoint), then block until every
# board in $sync_boards has reached the *same* key (or a timeout elapses).
# Record unconditionally, even from a board this tester doesn't have wired
# (its lane SKIPs every bundle instantly) -- but $sync_boards (unlike $boards)
# excludes those, so an unwired board's own barrier calls never have to
# out-wait real hardware; it just records and moves on. Without that split, a
# CHANGE-ME lane finishes phase1 near-instantly, sits waiting on the wired
# boards' real flash+pair, and gives up first -- failing the whole run on a
# timeout despite doing no actual test work (seen live: esp32c3 unwired,
# esp32dev/esp32s3's phase1 ran ~238s under the old 180s default).
#
# Both checkpoints matter: phase1's keeps any board from starting its heavy
# phase2 while another is still pairing; phase2's keeps a fast board from
# starting its *next* round's pairing while a slower board's phase2 is still
# generating live GATT traffic on the shared adapter -- without it, round
# N+1's phase1 could still overlap round N's phase2 on a different lane,
# which is the exact contention this whole scheme exists to remove.
#
# Returns 0 once every synced board has arrived, 1 on timeout -- callers must
# fail closed on 1 (skip phase 2 / stop the lane), not treat "gave up
# waiting" as "safe to proceed"; whoever we were waiting on may just be slow,
# not gone.
#
# The default timeout scales with ${#sync_boards[@]}, not a flat constant --
# phase1 is a one-board-at-a-time global mutex (see phase1_turn), so the
# longest any board waits for the round to clear grows with fleet size, and
# phase1_turn's own built-in retry can double one board's turn on top of
# that. A flat 300s (this scheme's previous default) is only headroom for
# ~1-2 boards' worth of turns; seen live on a 3-board rig: esp32c3 (first to
# arrive) timed out at 300s just 5s before esp32dev's retried phase1 (~180s
# vs ~85s normal for the other boards) finished, failing the whole
# --by-board run despite 0 actual test failures across every board.
round_barrier() {
  local board=$1 key=$2
  local state="${PHASE1_ARRIVED}.${key}"
  ( flock -w 30 211 && printf '%s\n' "$board" >>"$state" ) 211>"${state}.lock" || true

  local default_timeout=$(( 150 * ${#sync_boards[@]} ))
  [ "$default_timeout" -lt 300 ] && default_timeout=300
  local deadline=$((SECONDS + ${HIL_PHASE1_BARRIER_TIMEOUT:-$default_timeout}))
  while (( SECONDS < deadline )); do
    [ "$(sort -u "$state" 2>/dev/null | wc -l | tr -d ' ')" -ge "${#sync_boards[@]}" ] && return 0
    sleep 1
  done
  # Fail closed: a board that gave up waiting here must not be treated as
  # "clear to proceed" -- whoever we were waiting on may just be slow, not
  # gone, and could still be mid-pair (or mid-phase2 traffic) when the caller
  # acts on a zero return. Callers fold this into their own failure path.
  echo "== [$(ts)] barrier for $key timed out -- not clear to proceed" >&2
  return 1
}

# phase1_turn <board> <round> <bundle> -- flash+pair+test_connection.py for
# <bundle>, one board at a time across the whole rig (a flock, not just the
# conftest.py pair.lock -- see the --by-board header comment), one retry like
# test_bundle. Deliberately ignores $kargs/-k -- this smoke check always runs
# in full regardless of a focused run's filter.
phase1_turn() {
  local board=$1 round=$2 bundle=$3 rc=0
  mkdir -p "$PHASE1_CACHE"
  (
    flock -w "${HIL_PHASE1_MUTEX_TIMEOUT:-600}" 210 || exit 75
    # HIL_KEEP_LINK=1 -- phase 2 runs --no-pair (no BtCtl of its own, see the
    # --by-board header comment) and starts the moment this session ends, so
    # conftest.py's bt_mac must leave the link connected+trusted instead of
    # its normal end-of-session disconnect+untrust -- otherwise phase 2
    # inherits a dead link with nothing left to reconnect it.
    HIL_TEST_PATH="$CONN_TEST" HIL_JUNIT_TAG=phase1 HIL_KEEP_LINK=1 ./tester/test.sh "$bundle" && exit 0
    record_retry "$bundle" 1
    HIL_TEST_PATH="$CONN_TEST" HIL_JUNIT_TAG=phase1 HIL_KEEP_LINK=1 ./tester/test.sh "$bundle"
  ) 210>"$PHASE1_LOCK" || rc=$?
  round_barrier "$board" "${round}.phase1" || rc=1
  return "$rc"
}

run_lane() {  # <board> <pytest-args...> ; loop its bundles. rc 1 on any failure.
  local board=$1 b lrc=0
  shift
  while IFS= read -r b; do
    [ "$(bundle_board "$b")" = "$board" ] || continue
    local round; round=$(bundle_profile "$b")
    local p1xml="results/junit-${board}-${round}.phase1.xml"
    local outxml="results/junit-${board}-${round}.xml"
    if phase1_turn "$board" "$round" "$b"; then
      test_bundle "$b" --no-flash --no-pair "--ignore=$CONN_TEST" "$@" || lrc=1
      "$PY" host/hil/junit_merge.py "$p1xml" "$outxml" "$outxml" 2>/dev/null || true
    else
      lrc=1
      cp -f "$p1xml" "$outxml" 2>/dev/null || true
    fi
    # the temp phase1 report is now folded into $outxml (or copied over it) --
    # drop it, or the junit-*.xml glob (summarize.py, dorny/test-reporter)
    # double-counts its cases as a spurious extra "<profile>.phase1" bundle.
    rm -f "$p1xml"
    # don't start the *next* round's phase1 (pairing) until every board has
    # also finished *this* round's phase2 -- see round_barrier's comment. A
    # timeout here means we can't be sure everyone's done generating traffic,
    # so stop this lane rather than risk this board's next pairing attempt
    # overlapping someone else's still-running phase 2.
    if ! round_barrier "$board" "${round}.phase2"; then
      lrc=1
      break
    fi
  done < <(all_bundles)
  return $lrc
}

# A prior --by-board run's temp phase-1 junit can outlive it (killed mid-run,
# or a bundle whose phase1_turn never got as far as test.sh -- e.g. the
# mutex itself timed out). run_lane's own rm -f only cleans up the bundle it
# just finished, so a stale one can (a) get `cp -f`'d in as if it were this
# run's result the next time that exact board/profile hits the same
# never-ran-test.sh path, or (b) just pollute summarize_all's junit-*.xml
# glob in ANY later run, by-board or sequential. Clear them unconditionally,
# before branching, so neither path inherits the other's leftovers. Same
# reasoning for the retries-*.txt sidecars record_retry writes -- a stale one
# would make this run's report claim a retry that actually happened last time.
mkdir -p results
rm -f results/junit-*.phase1.xml results/retries-*.txt

if [ "$BY_BOARD" = 1 ]; then
  for a in "$@"; do
    [ "$a" != "--bench" ] || {
      echo "test-all.sh: --by-board is functional-only; run --bench separately" >&2
      exit 2
    }
  done
  # Phase 1's one-board-at-a-time serialization (the whole point of this
  # scheme -- see round_barrier's comment) is a flock mutex; with no flock,
  # every lane would flash+pair concurrently, unprotected. Refuse rather than
  # silently run --by-board's multi-board case without its safety mechanism.
  [ "$HAVE_FLOCK" = 1 ] || {
    echo "test-all.sh: --by-board needs util-linux flock to serialize phase 1" \
      "(none found) -- install it, or run sequentially without --by-board" >&2
    exit 2
  }
  rm -f "$PHASE1_ARRIVED".* "$PHASE1_LOCK"  # stale barrier state from a previous run
  mapfile -t boards < <(all_bundles | while IFS= read -r b; do bundle_board "$b"; done | sort -u)
  [ "${#boards[@]}" -gt 0 ] || { echo "no bundles in $BUNDLE_DIR" >&2; exit 0; }
  # sync_boards: boards that are both bundled AND actually wired here -- the
  # set round_barrier waits for. A board with no port configured (CHANGE-ME)
  # still gets a lane (so its bundles report SKIP), but never makes the real
  # lanes wait on it, and never waits on them itself -- see round_barrier.
  mapfile -t present < <(PYTHONPATH=host "$PY" -m hil.detect --present | tr ' ' '\n')
  mapfile -t sync_boards < <(comm -12 <(printf '%s\n' "${boards[@]}" | sort -u) <(printf '%s\n' "${present[@]}" | sort -u))
  echo "== [$(ts)] by-board: ${boards[*]}  (one parallel lane each; syncing on: ${sync_boards[*]:-none})"
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
  summarize_all
  exit "$trc"
fi

while IFS= read -r b; do
  # one retry: the C3/S3 external UART bridges drop a byte under load now and
  # then (README "Benchmarking / Rig note").
  test_bundle "$b" "$@" || trc=$?
done < <(all_bundles)
summarize_all
exit "$trc"
