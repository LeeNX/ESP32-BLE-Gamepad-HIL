#!/usr/bin/env bash
# TESTER role: the observer matrix. Every rotation makes one board a Bluepad32 observer and the next two (in
# [matrix].boards order) gamepads; any further boards sit that rotation out. Each profile then runs two lanes:
#
#   BlueZ lane      gamepad A, observed by the Pi:   tester/test.sh (pytest suite) for minimal/maxbtn,
#                                                    tester/sinput_hil.py (hid-generic + sinput driver) for sinput
#   Bluepad32 lane  gamepad B, observed by the observer board: tester/bp32_hil.py --profile <p>
#
# Rotating the observer through every board means each board is observed by both BlueZ and Bluepad32, and each
# board runs Bluepad32 once (esp32: BR/EDR + BLE; esp32c3/esp32s3: BLE only). A board sitting a rotation out is
# reset first: it is the previous observer, and would otherwise keep its allow filter and bonds.
#
#   tester/test-matrix.sh                                   # all rotations, minimal maxfeat sinput
#   tester/test-matrix.sh --boards "esp32dev esp32c3 esp32s3 esp32dev2"   # default: [matrix].boards
#   tester/test-matrix.sh --rotations esp32c3 --profiles sinput
#   tester/test-matrix.sh --max-cells 1 --lanes serial      # ramp up: one cell, lanes one after the other
#   tester/test-matrix.sh --restore                         # finish with every board on its default bundle
#
# Flash wear: a board is only written when it doesn't already hold the bundle (tester/flash.py verifies first),
# and boards are left on whatever they ran last unless --restore -- the next run (or CI's suite) flashes only
# what differs. A board's settings region (NVS: bonds) is wiped only when it changes firmware family (hil_runner
# <-> Bluepad32 observer, whose layouts differ) or on its first flash of the run -- not between hil_runner profiles,
# where it would drop a gamepad's bond the observer still holds.
#
# USB safety (the Pi 3B+'s one USB controller, shared with Ethernet, has wedged under parallel flashing):
# flashing is serialized (tester/flash.py's flash lock); the two lanes run one after the other by default
# (--lanes parallel overlaps them); and hil.usbhealth is checked before the run and after every flash and cell.
# On the first unhealthy check the run stops without restore-flashing -- reboot the host (bootstrap-watchdog.sh),
# then restore by hand or rerun.
#
# Bundles: the profile bundles <board>-<profile>-* (sinput ones under matrix/) and the observer bundles
# matrix/<board>-bp32obs-* (builder/build-observers.sh), the most recently built of each. Ends by dropping the
# HILpad BlueZ bonds, so the normal suite pairs fresh. Results go to results/matrix/ (one log per lane) and results/matrix-verdicts.md;
# each verdict line carries the lane's VERSIONS (kernel, sinput driver, firmware/library, Bluepad32 build).
set -u
cd "$(dirname "$0")/.." || exit 2
REPO=$(pwd)
# shellcheck source=tester/rig-lock.sh
source "$REPO/tester/rig-lock.sh"
rig_lock_acquire || exit $?

VENV=${HIL_VENV:-$HOME/.venvs/hil}
PY="$VENV/bin/python"
BUNDLE_DIR=${HIL_BUNDLE_DIR:-$HOME/hil-bundles}
BOARDS=()
ROTATIONS=()
PROFILES=(minimal maxfeat sinput)
LANES=serial
MAX_CELLS=0
RESTORE=0
while [[ $# -gt 0 ]]; do
  case $1 in
    --boards) read -r -a BOARDS <<<"$2"; shift 2 ;;
    --rotations) read -r -a ROTATIONS <<<"$2"; shift 2 ;;
    --profiles) read -r -a PROFILES <<<"$2"; shift 2 ;;
    --lanes) LANES=$2; shift 2 ;;
    --max-cells) MAX_CELLS=$2; shift 2 ;;
    --restore) RESTORE=1; shift ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

cfg() { python3 host/hil/config.py "$1"; }
port() { cfg "board.$1.port"; }
((${#BOARDS[@]})) || read -r -a BOARDS <<<"$(cfg matrix.boards)"
((${#BOARDS[@]})) || BOARDS=(esp32dev esp32c3 esp32s3)
((${#BOARDS[@]} >= 3)) || { echo "the matrix needs 3 or more boards, got: ${BOARDS[*]}" >&2; exit 2; }
((${#ROTATIONS[@]})) || ROTATIONS=("${BOARDS[@]}")
for r in "${ROTATIONS[@]}"; do
  [[ " ${BOARDS[*]} " == *" $r "* ]] || { echo "rotation $r is not one of the boards: ${BOARDS[*]}" >&2; exit 2; }
done
flash_port() { local p; p=$(cfg "board.$1.flash_port"); echo "${p:-$(port "$1")}"; }
# Most recently built (rsync keeps the build's mtime): names end in shas, so sorting them says nothing about age.
newest() {
  local d best=""
  for d in "$@"; do [[ -d $d && ( -z $best || $d -nt $best ) ]] && best=$d; done
  [[ -n $best ]] && echo "$best"
}
bundle() {  # <board> <profile>
  if [[ $2 == sinput || $2 == bp32obs ]]; then newest "$BUNDLE_DIR/matrix/$1-$2"-*; else newest "$BUNDLE_DIR/$1-$2"-*; fi
}
# Boards flashed over native USB (USB-Serial/JTAG) re-enumerate after the reset: give that time to settle (and
# any dwc_otg warnings it causes time to land) before the next health check counts them.
# flash.py skips a board that already holds the bundle. The settings region (NVS: BLE bonds) is wiped only when
# a board changes firmware family (hil_runner <-> Bluepad32 observer, whose layouts differ) or on its first flash of
# the run (state unknown) -- not between hil_runner profiles: wiping a gamepad's bond while the observer keeps its
# copy leaves the observer reconnecting with a stale key, and the gamepad never comes up.
# A failed flash stops the run: the next cell would test the old firmware.
declare -A FAMILY=()
flash() {  # <board> <bundle>
  local out rc fam wipe=()
  fam=$([[ $2 == *-bp32obs-* ]] && echo observer || echo runner)
  [[ ${FAMILY[$1]:-} != "$fam" ]] && wipe=(--wipe-settings)
  out=$("$PY" tester/flash.py "$2" --port "$(flash_port "$1")" "${wipe[@]}" 2>&1)
  rc=$?
  grep '^flash:' <<<"$out" | tail -1
  if ((rc != 0)); then
    tail -5 <<<"$out" >&2
    echo "ABORT at flash $1: tester/flash.py rc=$rc" | tee -a "$VERDICTS"
    printf '%s\n' "${summary[@]}"
    exit 3
  fi
  FAMILY[$1]=$fam
  # Even an unchanged bundle resets the chip (verify_flash's hard reset), so a native-USB port re-enumerates.
  [[ -n $(cfg "board.$1.flash_port") ]] && sleep 5
  return 0
}
health() { PYTHONPATH=host python3 -m hil.usbhealth "${@:-check}"; }
# Restart a board, like bp32_hil.py resets the observer: through its native USB when it has one, else RTS -> EN on its
# console bridge. Used on a board a rotation doesn't use (the previous observer) and, at the end, on every board that ran
# as an observer: on boot the observer drops its allow filter and bonds, so it can't hold on to (or be reconnected by)
# a gamepad another lane -- or whatever runs after the matrix (a bench, the next CI suite) -- is using. A leftover
# observer still linked to its last gamepad doubled that gamepad's bench latency and can stop it advertising.
reset_board() {  # <board>
  local p
  p=$(cfg "board.$1.flash_port"); p=${p:-$(port "$1")}
  [[ -e $p ]] || { echo "$1: no port ($p), not reset" >&2; return 0; }
  "$PY" - "$p" <<'PY'
import sys, time
import serial
s = serial.Serial()
s.port, s.dtr, s.rts = sys.argv[1], False, True
s.open()
time.sleep(0.1)
try:
    s.rts = False
    s.close()
except (OSError, serial.SerialException):  # a native USB port re-enumerates as the chip resets
    pass
PY
}
# Stop at the first unhealthy check: more flashing on a wedged bus only makes it worse.
guard() {  # <where>
  local out
  # Re-baseline after every passing check, so a guard counts the dwc_otg warnings of *this* step: a wedge comes
  # as one burst (dozens), while a board's reset can leave a burst of 6-9 even through a UART bridge, and those
  # would add up past the threshold over a run.
  out=$(health check) && { health baseline >/dev/null; return 0; }
  echo "ABORT at $1: $out -- reboot the host, then restore the boards" | tee -a "$VERDICTS"
  printf '%s\n' "${summary[@]}"
  exit 3
}
unbond() {
  local m
  for m in $(bluetoothctl devices | awk '/HILpad/ {print $2}'); do bluetoothctl remove "$m" >/dev/null; done
}

OUT=results/matrix
mkdir -p "$OUT"
VERDICTS=results/matrix-verdicts.md
echo "## matrix $(date -u +%FT%TZ) rotations=${ROTATIONS[*]} profiles=${PROFILES[*]} lanes=$LANES" >> "$VERDICTS"
fail=0
cells=0
summary=()
# Before the run: is the bus usable now? (Warnings from an earlier burst it recovered from don't count; the
# fresh baseline below makes the in-run guards count only this run's.)
health check --ignore-dwc || { echo "USB unhealthy before the run: not starting" >&2; exit 3; }
health baseline

for obs in "${ROTATIONS[@]}"; do
  # The next two boards, in a fixed cyclic order: the first is observed by BlueZ, the second by Bluepad32.
  n=${#BOARDS[@]}
  i=0; for b in "${BOARDS[@]}"; do [[ $b == "$obs" ]] && break; i=$((i + 1)); done
  bz=${BOARDS[$(((i + 1) % n))]}; bp=${BOARDS[$(((i + 2) % n))]}
  idle=()
  for b in "${BOARDS[@]}"; do [[ $b == "$obs" || $b == "$bz" || $b == "$bp" ]] || idle+=("$b"); done
  obs_bundle=$(bundle "$obs" bp32obs) || { echo "no observer bundle for $obs" >&2; fail=1; continue; }
  # bp32_hil.py resets the observer at the start of each lane; through its native USB when it has one, since a
  # console bridge may lack RTS -> EN.
  obs_rst=$(cfg "board.$obs.flash_port")
  echo; echo "==== rotation: observer=$obs  BlueZ<-$bz  Bluepad32<-$bp${idle[*]:+  idle: ${idle[*]}}"
  unbond
  for b in "${idle[@]}"; do reset_board "$b"; done
  flash "$obs" "$obs_bundle"
  guard "flash observer $obs"

  for p in "${PROFILES[@]}"; do
    if ((MAX_CELLS > 0 && cells >= MAX_CELLS)); then break; fi
    cells=$((cells + 1))
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
    guard "flash $bz"
    flash "$bp" "$bp_bundle"
    guard "flash $bp"
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
    if [[ $LANES == serial ]]; then wait $bz_pid; bz_rc=$?; fi
    PYTHONPATH=host timeout 900 "$PY" tester/bp32_hil.py --profile "$p" "$(port "$bp")" "$(port "$obs")" \
      ${obs_rst:+--bp32-reset-port "$obs_rst"} \
      > "$OUT/$tag-bp32-$bp.log" 2>&1 &
    bp_pid=$!
    if [[ $LANES != serial ]]; then wait $bz_pid; bz_rc=$?; fi
    wait $bp_pid; bp_rc=$?
    for lane in "bluez:$bz:$bz_rc" "bp32:$bp:$bp_rc"; do
      IFS=: read -r name board rc <<<"$lane"
      log="$OUT/$tag-$name-$board.log"
      verdict=$([[ $rc == 0 ]] && echo PASS || echo FAIL)
      result=$(grep -E "^(PASSED|FAILED)|passed|failed" "$log" | tail -1)
      gaps=$(grep -c "^GAP " "$log")
      vers=$(grep -m1 "^VERSIONS " "$log" | cut -d' ' -f2-)
      line="$verdict observer=$obs profile=$p lane=$name gamepad=$board rc=$rc gaps=$gaps  $result${vers:+  [$vers]}"
      echo "$line" | tee -a "$VERDICTS"
      summary+=("$line")
      [[ $rc == 0 ]] || fail=1
    done
    # The two gamepad boards may have bonded with BlueZ; drop that before the next profile's descriptor.
    unbond
    guard "after cell $tag"
  done
done
# Leave no observer linked to a gamepad behind the run (--restore reflashes them anyway).
if ((!RESTORE)); then
  for b in "${BOARDS[@]}"; do [[ ${FAMILY[$b]:-} == observer ]] && reset_board "$b"; done
fi

if ((RESTORE)); then
  echo; echo "==== restore"
  for b in "${BOARDS[@]}"; do
    def_bundle=$(bundle "$b" default) || { echo "no default bundle for $b" >&2; continue; }
    flash "$b" "$def_bundle"
    guard "restore $b"
  done
fi
unbond

echo; echo "==== matrix summary"
printf '%s\n' "${summary[@]}"
exit $fail
