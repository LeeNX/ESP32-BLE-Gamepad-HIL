#!/usr/bin/env bash
# Update the library checkout to the ref under test, run the HIL suite for each
# board, write JUnit XML + results/summary.md.
#
#   ./run.sh                              # current checkout, default board, default profile
#   LIB_REF=my-branch ./run.sh            # check out a ref first
#   ./run.sh --board esp32c3              # single board; extra pytest args pass through
#   HIL_PROFILES="default specials" ./run.sh   # run each profile in turn (re-pairs between)
set -euo pipefail

cd "$(dirname "$0")"
REPO=$(pwd)
VENV=${HIL_VENV:-$HOME/.venvs/hil}
LIB_DIR=$(python3 -c "import tomllib,sys;print(tomllib.load(open('hil_config.toml','rb'))['rig']['lib_dir'])")
BOARDS=(${HIL_BOARDS:-esp32dev})
PROFILES=(${HIL_PROFILES:-default})
PYTEST_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --board) BOARDS=("$2"); shift 2 ;;
    *) PYTEST_ARGS+=("$1"); shift ;;
  esac
done

if [[ -n "${LIB_REF:-}" ]]; then
  echo "== library checkout: $LIB_DIR @ $LIB_REF"
  git -C "$LIB_DIR" fetch --all --quiet
  git -C "$LIB_DIR" checkout --quiet "$LIB_REF"
  git -C "$LIB_DIR" pull --ff-only --quiet || true
fi
echo "== library at $(git -C "$LIB_DIR" describe --always --dirty) ($(git -C "$LIB_DIR" rev-parse --abbrev-ref HEAD))"

mkdir -p results
STAMP=$(date +%Y%m%d-%H%M%S)
rc=0
for board in "${BOARDS[@]}"; do
  for profile in "${PROFILES[@]}"; do
    tag="${board}-${profile}-${STAMP}"
    xml="results/junit-${tag}.xml"
    echo "== board: $board  profile: $profile -> $xml"
    "$VENV/bin/pytest" --board "$board" --profile "$profile" --junit-xml="$xml" \
      "${PYTEST_ARGS[@]}" 2>&1 | tee "results/log-${tag}.txt" || rc=$?
    "$VENV/bin/python" host/hil/summarize.py "$xml" "results/summary-${tag}.md" || rc=$?
    cp "results/summary-${tag}.md" "results/summary.md"
    cat "results/summary.md"
  done
done
exit $rc
