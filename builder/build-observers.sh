#!/usr/bin/env bash
# BUILDER role: Bluepad32 "observer" bundles, one per board, for the SInput / observer matrix (tester/bp32_hil.py).
# A board flashed with one runs Bluepad32 (the Bluepad32 HIL rig's host firmware, leenx-foss/antBot-hil host/,
# built with HIL_OBSERVER=1: no NuS/OTA) and prints what it parsed on its serial console, so it can observe another
# board running hil_runner, next to BlueZ.
#
#   builder/build-observers.sh                          # [builder] boards, paths from [observer] in hil_config
#   builder/build-observers.sh --boards "esp32c3" --out /tmp/bundles
#
# Bundles land in <out>/matrix/<board>-bp32obs-<bluepad32 sha8>/ -- under matrix/ so tester/test-all.sh (which only
# runs top-level bundles) never flashes one as a gamepad. Each carries a blank NVS image, so an observer always
# starts without stale bonds, and puts the app in the host's factory slot (PlatformIO's idedata would pick ota_0).
#
# Needs: a PlatformIO >= 6.2 (the host uses the pioarduino platform), the antBot-hil checkout, and a Bluepad32
# checkout (LeeNX/bluepad32 feature/sinput until ricardoquesada/bluepad32#234 merges, then upstream develop) with
# its BTstack submodule and l2cap patch applied (see that repo's external/patches).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
cfg() { python3 host/hil/config.py "$1"; }
path() { python3 -c 'import os,sys; print(os.path.expanduser(sys.argv[1]))' "$1"; }

HOST_DIR=$(path "${HIL_OBSERVER_HOST_DIR:-$(cfg observer.host_dir)}")
BP32_DIR=$(path "${HIL_BLUEPAD32_DIR:-$(cfg observer.bluepad32_dir)}")
PIO=${HIL_OBSERVER_PIO:-$(cfg observer.pio)}; PIO=${PIO:-$(cfg rig.pio)}; PIO=$(path "${PIO:-pio}")
OUT_ROOT=${HIL_BUNDLES:-$REPO/bundles}
# SC2206: BOARDS is a space-separated list we deliberately word-split.
# shellcheck disable=SC2206
BOARDS=(${HIL_BOARDS:-$(cfg builder.boards)})
while [[ $# -gt 0 ]]; do
  case $1 in
    --boards) read -r -a BOARDS <<<"$2"; shift 2 ;;
    --out) OUT_ROOT=$2; shift 2 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[[ -f "$HOST_DIR/platformio.ini" ]] || { echo "[observer].host_dir: no platformio.ini in '$HOST_DIR'" >&2; exit 2; }
[[ -d "$BP32_DIR/src/components/bluepad32" ]] || { echo "[observer].bluepad32_dir: not a Bluepad32 checkout: '$BP32_DIR'" >&2; exit 2; }

board_env() { case "$1" in
  esp32dev) echo esp32dev ;; esp32c3) echo esp32-c3-devkitc-02 ;; esp32s3) echo esp32-s3-devkitc-1 ;;
  *) echo "no observer env for board: $1" >&2; exit 2 ;; esac; }
board_chip() { case "$1" in
  esp32dev) echo esp32 ;; esp32c3) echo esp32c3 ;; esp32s3) echo esp32s3 ;;
  *) echo "unknown board: $1" >&2; exit 2 ;; esac; }
# partitions.csv field (offset or size) of a partition, by name.
part() { awk -F, -v n="$1" -v f="$2" '$1 ~ "^"n"[ \t]*$" {gsub(/[ \t]/, "", $f); print $f}' "$HOST_DIR/partitions.csv"; }

BP32_SHA=$(git -C "$BP32_DIR" rev-parse --short=8 HEAD)
NVS_OFF=$(part nvs 4); NVS_SIZE=$(part nvs 5); APP_OFF=$(part factory 4)
[[ -n "$NVS_OFF" && -n "$NVS_SIZE" && -n "$APP_OFF" ]] || { echo "can't read nvs/factory from partitions.csv" >&2; exit 2; }
echo "== bluepad32 $(git -C "$BP32_DIR" describe --tags --always --dirty) ($(git -C "$BP32_DIR" rev-parse --abbrev-ref HEAD))"
echo "== host $HOST_DIR @ $(git -C "$HOST_DIR" rev-parse --short HEAD); nvs $NVS_OFF+$NVS_SIZE, factory app $APP_OFF"

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
python3 -c 'import sys; open(sys.argv[1], "wb").write(b"\xff" * int(sys.argv[2], 0))' "$tmp/nvs_blank.bin" "$NVS_SIZE"

mkdir -p "$OUT_ROOT/matrix"
for board in "${BOARDS[@]}"; do
  env=$(board_env "$board"); chip=$(board_chip "$board")
  echo "== build observer $board (env=$env chip=$chip)"
  # The firmware version string and HIL_OBSERVER come from the environment, which the build doesn't track: wipe.
  rm -rf "$HOST_DIR/.pio/build/$env" "$HOST_DIR/sdkconfig.$env"
  export HIL_OBSERVER=1 HIL_BLUEPAD32_DIR="$BP32_DIR" HIL_FW_VERSION="bp32obs-$BP32_SHA"
  "$PIO" run -e "$env" -d "$HOST_DIR"
  ide=$(mktemp)
  "$PIO" run -e "$env" -d "$HOST_DIR" -t idedata > "$ide" 2>/dev/null
  python3 builder/make_bundle.py \
    --build-dir "$HOST_DIR/.pio/build/$env" \
    --idedata "$ide" --env "$env" --profile bp32obs \
    --board "$board" --chip "$chip" --lib-dir "$BP32_DIR" --out-root "$OUT_ROOT/matrix" \
    --app-offset "$APP_OFF" --extra-image "$NVS_OFF:$tmp/nvs_blank.bin"
  rm -f "$ide"
done
echo "== observer bundles in $OUT_ROOT/matrix:"
ls -1 "$OUT_ROOT/matrix"
