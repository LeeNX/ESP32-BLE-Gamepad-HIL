#!/usr/bin/env bash
# TESTER role: flash one firmware bundle and run the BLE HID suite against it.
# Needs only esptool + the pytest deps (tester/requirements.txt) -- no PlatformIO.
#
#   tester/test.sh bundles/esp32dev-default-4410936/
#   tester/test.sh <bundle> --no-flash -k buttons        # extra args pass to pytest
#   tester/test.sh <bundle> --bench                       # + latency/throughput sweep
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

ts() { date +%H:%M:%S; }
say() { echo "== [$(ts)] $*"; }
load_now() { cut -d' ' -f1-3 /proc/loadavg 2>/dev/null || echo "?"; }

say "tester board=$BOARD profile=$PROFILE port=$PORT flash_port=$FLASH_PORT  load $(load_now)"
say "bundle $(basename "$BUNDLE")"

# Skip (not fail) a board this tester doesn't have wired / has disabled -- lets
# CI build the full matrix but run only what's attached. See host/hil/detect.py.
if ! why=$(PYTHONPATH=host python3 -m hil.detect --check "$BOARD" 2>&1); then
  mkdir -p results
  line="SKIP  $BOARD/$PROFILE  ($why)"
  say "$line"
  printf '%s\n' "- $line" >> results/run-verdicts.md
  exit 0
fi

FLASH=1
BENCH=0
PYTEST_EXTRA=()
for a in "$@"; do
  [[ "$a" == "--" ]] && continue        # tolerate a `-- <pytest args>` separator
  [[ "$a" == "--no-flash" ]] && FLASH=0
  [[ "$a" == "--bench" ]] && BENCH=1
  PYTEST_EXTRA+=("$a")
done
if [[ $FLASH == 1 ]]; then
  say "flash (esptool)"
  "$VENV/bin/python" tester/flash.py "$BUNDLE" --port "$FLASH_PORT"
fi

mkdir -p results
STAMP=$(date +%Y%m%d-%H%M%S)
tag="${BOARD}-${PROFILE}-${STAMP}"
xml="results/junit-${tag}.xml"
log="results/log-${tag}.txt"
rc=0

say "pytest  (this streams; pairing takes ~20s, --bench adds several minutes)"
[[ $BENCH == 1 ]] && say "  bench sweep enabled -- latency + throughput, ~5-8 min"
start=$(date +%s)
# Pin rootdir/ini: --bundle is an absolute path outside the repo and pytest's
# first-pass arg parse treats it as a positional test path -> rootdir discovery
# would never find this pytest.ini and conftest.py would not load.
"$VENV/bin/pytest" -c "$REPO/pytest.ini" --rootdir "$REPO" "$REPO/host/tests" \
  --bundle "$BUNDLE" --board "$BOARD" --profile "$PROFILE" \
  --junit-xml="$xml" "${PYTEST_EXTRA[@]}" 2>&1 | tee "$log" || rc=$?
elapsed=$(( $(date +%s) - start ))

"$VENV/bin/python" host/hil/summarize.py "$xml" "results/summary-${tag}.md" || true
cp "results/summary-${tag}.md" results/summary.md

if ls results/bench-*.json >/dev/null 2>&1; then
  PYTHONPATH=host "$VENV/bin/python" -m hil.charts results/ || true
  [[ -f results/bench-table.md ]] && { echo; cat results/bench-table.md; } >> results/summary.md
fi

# one-line verdict, echoed and appended to a cumulative results/summary.md header
verdict=$(grep -oE '[0-9]+ (passed|failed|error|xfailed|skipped)[^,]*' "$log" | paste -sd', ' - || true)
mark=$([[ $rc == 0 ]] && echo "PASS" || echo "FAIL")
line="$mark  $BOARD/$PROFILE  ${verdict:-no summary}  (${elapsed}s, load $(load_now))"
say "$line"
printf '%s\n' "- $line" >> results/run-verdicts.md

exit $rc
