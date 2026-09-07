#!/usr/bin/env bash
# One-box path: build firmware bundles here, then flash + test each of them
# here. For the split builder/tester setup use builder/build.sh --push on the
# builder and tester/test.sh on the tester (or let Gitea CI do it).
#
#   ./run.sh                              # config defaults (see hil_config.toml [builder])
#   LIB_REF=my-branch ./run.sh            # check the library out first
#   ./run.sh --boards esp32dev --profiles "default specials"
#   ./run.sh --profiles default -- -k buttons      # args after -- go to pytest
set -euo pipefail
cd "$(dirname "$0")"
REPO=$(pwd)

BUILD_ARGS=()
PYTEST_ARGS=()
seen_ddash=0
for a in "$@"; do
  if [[ $seen_ddash == 1 ]]; then PYTEST_ARGS+=("$a"); continue; fi
  [[ "$a" == "--" ]] && { seen_ddash=1; continue; }
  BUILD_ARGS+=("$a")
done

echo "=== build ==="
rm -rf "$REPO/bundles"
"$REPO/builder/build.sh" "${BUILD_ARGS[@]}"

# one physical rig -- wait for any other run (CI or local) before touching it.
# shellcheck source=tester/rig-lock.sh
source "$REPO/tester/rig-lock.sh"
export HIL_RUN_WHAT="${HIL_RUN_WHAT:-run.sh ${BUILD_ARGS[*]:-}}"
rig_lock_acquire || exit $?

rc=0
for bundle in "$REPO"/bundles/*/; do
  [[ -f "$bundle/manifest.json" ]] || continue
  echo "=== test: $(basename "$bundle") ==="
  "$REPO/tester/test.sh" "$bundle" "${PYTEST_ARGS[@]}" || rc=$?
done
exit $rc
