#!/usr/bin/env bash
# TESTER role: flash one firmware bundle and run the BLE HID suite against it.
# Needs only esptool + the pytest deps (tester/requirements.txt) -- no PlatformIO.
#
#   tester/test.sh bundles/esp32dev-default-4410936/
#   tester/test.sh <bundle> --no-flash -k buttons        # extra args pass to pytest
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
VENV=${HIL_VENV:-$HOME/.venvs/hil}

BUNDLE=${1:?usage: tester/test.sh <bundle_dir> [pytest args]}
shift || true
BUNDLE=$(cd "$BUNDLE" && pwd)

read -r BOARD PROFILE < <(python3 -c "import json;m=json.load(open('$BUNDLE/manifest.json'));print(m['board'],m['profile'])")
PORT=$(python3 host/hil/config.py "board.$BOARD.port")
# flash_port defaults to port; esp32-c3 with an external UART bridge flashes on
# its native USB and talks on the bridge (README "ESP32-C3 serial bridge").
FLASH_PORT=$(python3 host/hil/config.py "board.$BOARD.flash_port")
[[ -n "$FLASH_PORT" ]] || FLASH_PORT=$PORT

echo "== tester: board=$BOARD profile=$PROFILE port=$PORT flash_port=$FLASH_PORT"
echo "== bundle: $BUNDLE"

FLASH=1
PYTEST_EXTRA=()
for a in "$@"; do
  [[ "$a" == "--no-flash" ]] && FLASH=0
  PYTEST_EXTRA+=("$a")
done
if [[ $FLASH == 1 ]]; then
  "$VENV/bin/python" tester/flash.py "$BUNDLE" --port "$FLASH_PORT"
fi

mkdir -p results
STAMP=$(date +%Y%m%d-%H%M%S)
tag="${BOARD}-${PROFILE}-${STAMP}"
xml="results/junit-${tag}.xml"
rc=0
"$VENV/bin/pytest" --bundle "$BUNDLE" --board "$BOARD" --profile "$PROFILE" \
  --junit-xml="$xml" "${PYTEST_EXTRA[@]}" 2>&1 | tee "results/log-${tag}.txt" || rc=$?
"$VENV/bin/python" host/hil/summarize.py "$xml" "results/summary-${tag}.md" || rc=$?
cp "results/summary-${tag}.md" results/summary.md
cat results/summary.md
exit $rc
