#!/usr/bin/env bash
# TESTER role: flash + test every bundle in $HIL_BUNDLE_DIR (default ~/hil-bundles),
# one retry each. Args here + $HIL_PYTEST_EXTRA (space-split) pass to tester/test.sh.
#
#   tester/test-all.sh --bench
#   HIL_BUNDLE_DIR=/tmp/bundles tester/test-all.sh -k buttons
#   HIL_PYTEST_EXTRA='-k feature_report' tester/test-all.sh --bench   # focused (what CI does)
#
# Takes the rig lock once for the whole batch (no-op if the caller -- CI, or
# `tester/rig-lock.sh -- tester/test-all.sh` -- already holds it).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=tester/rig-lock.sh
source tester/rig-lock.sh
rig_lock_acquire || exit $?

read -ra _extra <<< "${HIL_PYTEST_EXTRA:-}"    # e.g. "-k feature_report" -> (-k feature_report)
BUNDLE_DIR=${HIL_BUNDLE_DIR:-$HOME/hil-bundles}
trc=0
for b in "$BUNDLE_DIR"/*/; do
  [ -f "$b/manifest.json" ] || continue
  # one retry: the C3/S3 external UART bridges drop a byte under the --bench
  # burst sweep now and then (README "Benchmarking / Rig note").
  ./tester/test.sh "$b" "$@" "${_extra[@]}" || ./tester/test.sh "$b" "$@" "${_extra[@]}" || trc=$?
done
exit "$trc"
