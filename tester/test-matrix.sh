#!/usr/bin/env bash
# TESTER role: the observer matrix. Every rotation makes one board a Bluepad32 observer and the other two
# gamepads; each profile then runs two lanes in parallel:
#
#   BlueZ lane      gamepad A, observed by the Pi:   tester/test.sh (pytest suite) for minimal/maxbtn,
#                                                    tester/sinput_hil.py (hid-generic + sinput driver) for sinput
#   Bluepad32 lane  gamepad B, observed by the observer board: tester/bp32_hil.py --profile <p>
#
# Rotating the observer through every board means each board is observed by both BlueZ and Bluepad32, and each
# board runs Bluepad32 once (esp32: BR/EDR + BLE; esp32c3/esp32s3: BLE only).
#
#   tester/test-matrix.sh                                   # all rotations, minimal maxbtn sinput
#   tester/test-matrix.sh --rotations esp32c3 --profiles sinput
#
# Bundles: the profile bundles <board>-<profile>-* (sinput ones under matrix/) and the observer bundles
# matrix/<board>-bp32obs-* (builder/build-observers.sh), newest by name. Ends by putting every board back on its
# <board>-default-* bundle and dropping the HILpad BlueZ bonds, so the normal suite pairs fresh. Results go to
# results/matrix/ (one log per lane) and results/matrix-verdicts.md.
set -u
cd "$(dirname "$0")/.." || exit 2
REPO=$(pwd)
# shellcheck source=tester/rig-lock.sh
source "$REPO/tester/rig-lock.sh"
rig_lock_acquire || exit $?

VENV=${HIL_VENV:-$HOME/.venvs/hil}
PY="$VENV/bin/python"
BUNDLE_DIR=${HIL_BUNDLE_DIR:-$HOME/hil-bundles}
BOARDS=(esp32dev esp32c3 esp32s3)
ROTATIONS=("${BOARDS[@]}")
PROFILES=(minimal maxbtn sinput)
while [[ $# -gt 0 ]]; do
  case $1 in
    --rotations) read -r -a ROTATIONS <<<"$2"; shift 2 ;;
    --profiles) read -r -a PROFILES <<<"$2"; shift 2 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

cfg() { python3 host/hil/config.py "$1"; }
port() { cfg "board.$1.port"; }
flash_port() { local p; p=$(cfg "board.$1.flash_port"); echo "${p:-$(port "$1")}"; }
chip() { case "$1" in esp32dev) echo esp32 ;; esp32c3) echo esp32c3 ;; esp32s3) echo esp32s3 ;; esac; }
newest() { local d=("$@"); [[ -d "${d[-1]}" ]] && echo "${d[-1]}"; }  # globs sort: last = newest name
bundle() {  # <board> <profile>
  if [[ $2 == sinput || $2 == bp32obs ]]; then newest "$BUNDLE_DIR/matrix/$1-$2"-*; else newest "$BUNDLE_DIR/$1-$2"-*; fi
}
flash() { "$PY" tester/flash.py "$2" --port "$(flash_port "$1")" 2>&1 | tail -1; }
# Settings (NVS/otadata) differ between hil_runner and the Bluepad32 observer: clear them when a board changes role.
erase_settings() {
  "$PY" -m esptool --chip "$(chip "$1")" --port "$(flash_port "$1")" erase-region 0x9000 0x9000 2>&1 \
    | grep -iE "erased|error" | tail -1
}
unbond() {
  local m
  for m in $(bluetoothctl devices | awk '/HILpad/ {print $2}'); do bluetoothctl remove "$m" >/dev/null; done
}

OUT=results/matrix
mkdir -p "$OUT"
VERDICTS=results/matrix-verdicts.md
echo "## matrix $(date -u +%FT%TZ) rotations=${ROTATIONS[*]} profiles=${PROFILES[*]}" >> "$VERDICTS"
fail=0
summary=()

for obs in "${ROTATIONS[@]}"; do
  # The other two boards, in a fixed cyclic order: the first is observed by BlueZ, the second by Bluepad32.
  i=0; for b in "${BOARDS[@]}"; do [[ $b == "$obs" ]] && break; i=$((i + 1)); done
  bz=${BOARDS[$(((i + 1) % 3))]}; bp=${BOARDS[$(((i + 2) % 3))]}
  obs_bundle=$(bundle "$obs" bp32obs) || { echo "no observer bundle for $obs" >&2; fail=1; continue; }
  echo; echo "==== rotation: observer=$obs  BlueZ<-$bz  Bluepad32<-$bp"
  unbond
  erase_settings "$obs"
  flash "$obs" "$obs_bundle"

  for p in "${PROFILES[@]}"; do
    bz_bundle=$(bundle "$bz" "$p"); bp_bundle=$(bundle "$bp" "$p")
    if [[ -z $bz_bundle || -z $bp_bundle ]]; then
      echo "SKIP $obs/$p: missing bundle (bluez=$bz_bundle bp32=$bp_bundle)" | tee -a "$VERDICTS"
      continue
    fi
    echo "== $obs/$p: flashing $bz and $bp"
    # One at a time: on the Pi 3B+ every USB port and the Ethernet share one controller, and parallel flashing
    # plus BLE traffic has wedged it hard enough to need a power cycle (2026-09-11, 2026-10-04). test-all.sh
    # serializes flash+pair for the same reason; only the test lanes below run in parallel.
    flash "$bz" "$bz_bundle"
    flash "$bp" "$bp_bundle"
    sleep 3
    tag="obs-$obs-$p"
    (
      if [[ $p == sinput ]]; then
        PYTHONPATH=host "$PY" tester/sinput_hil.py "$(port "$bz")" "HILpad $bz" --cycles 3
      else
        HIL_JUNIT_TAG="matrix-$obs" tester/test.sh "$bz_bundle" --no-flash
      fi
    ) > "$OUT/$tag-bluez-$bz.log" 2>&1 &
    bz_pid=$!
    PYTHONPATH=host timeout 900 "$PY" tester/bp32_hil.py --profile "$p" "$(port "$bp")" "$(port "$obs")" \
      > "$OUT/$tag-bp32-$bp.log" 2>&1 &
    bp_pid=$!
    wait $bz_pid; bz_rc=$?
    wait $bp_pid; bp_rc=$?
    for lane in "bluez:$bz:$bz_rc" "bp32:$bp:$bp_rc"; do
      IFS=: read -r name board rc <<<"$lane"
      log="$OUT/$tag-$name-$board.log"
      verdict=$([[ $rc == 0 ]] && echo PASS || echo FAIL)
      result=$(grep -E "^(PASSED|FAILED)|passed|failed" "$log" | tail -1)
      gaps=$(grep -c "^GAP " "$log")
      line="$verdict observer=$obs profile=$p lane=$name gamepad=$board rc=$rc gaps=$gaps  $result"
      echo "$line" | tee -a "$VERDICTS"
      summary+=("$line")
      [[ $rc == 0 ]] || fail=1
    done
    # The two gamepad boards may have bonded with BlueZ; drop that before the next profile's descriptor.
    unbond
  done
  erase_settings "$obs"
done

echo; echo "==== restore"
for b in "${BOARDS[@]}"; do
  def_bundle=$(bundle "$b" default) || { echo "no default bundle for $b" >&2; continue; }
  erase_settings "$b"
  flash "$b" "$def_bundle"
done
unbond

echo; echo "==== matrix summary"
printf '%s\n' "${summary[@]}"
exit $fail
