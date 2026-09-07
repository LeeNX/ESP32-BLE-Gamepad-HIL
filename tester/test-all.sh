#!/usr/bin/env bash
# TESTER role: flash + test every bundle in $HIL_BUNDLE_DIR (default ~/hil-bundles),
# one retry each. Extra args pass straight to tester/test.sh (e.g. --bench).
#
#   tester/test-all.sh --bench
#   HIL_BUNDLE_DIR=/tmp/bundles tester/test-all.sh -k buttons
#
# Takes the rig lock once for the whole batch (no-op if the caller -- CI, or
# `tester/rig-lock.sh -- tester/test-all.sh` -- already holds it).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
# shellcheck source=tester/rig-lock.sh
source tester/rig-lock.sh
rig_lock_acquire || exit $?

BUNDLE_DIR=${HIL_BUNDLE_DIR:-$HOME/hil-bundles}
trc=0
for b in "$BUNDLE_DIR"/*/; do
  [ -f "$b/manifest.json" ] || continue
  # one retry: the C3/S3 external UART bridges drop a byte under the --bench
  # burst sweep now and then (README "Benchmarking / Rig note").
  ./tester/test.sh "$b" "$@" || ./tester/test.sh "$b" "$@" || trc=$?
done
exit "$trc"
