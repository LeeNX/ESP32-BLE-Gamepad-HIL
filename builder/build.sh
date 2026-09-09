#!/usr/bin/env bash
# BUILDER role: compile hil_runner against the library under test and produce
# flashable firmware bundles (see builder/make_bundle.py). Optionally rsync them
# to the tester.
#
#   builder/build.sh                                   # config defaults
#   LIB_REF=some-branch builder/build.sh               # check the library out first
#   builder/build.sh --boards "esp32dev" --profiles "default specials"
#   PUSH=1 builder/build.sh                            # also rsync bundles to [tester].ssh_host
#
# SC2206: BOARDS / PROFILES are space-separated lists we deliberately word-split.
# shellcheck disable=SC2206
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)

cfg() { python3 host/hil/config.py "$1"; }

PIO=${HIL_PIO:-$(cfg rig.pio)}; PIO=${PIO:-$HOME/.local/bin/pio}
LIB_DIR=$(cfg rig.lib_dir); LIB_DIR=${LIB_DIR:-$HOME/src/ESP32-BLE-Gamepad}
# platformio.ini resolves the library-under-test via symlink://${sysenv.HIL_LIB_DIR}
export HIL_LIB_DIR="$LIB_DIR"
BOARDS=(${HIL_BOARDS:-$(cfg builder.boards)}); BOARDS=(${BOARDS[@]:-esp32dev})
PROFILES=(${HIL_PROFILES:-$(cfg builder.profiles)}); PROFILES=(${PROFILES[@]:-default})
OUT_ROOT=${HIL_BUNDLES:-$REPO/bundles}

# Advertised BLE name override (intended for `--profiles local`), highest wins:
#   --name <n>  >  $HIL_DEVICE_NAME  >  hil_config.local.toml [rig] local_device_name
# Unset -> firmware default from hil_profile.h ("HILpad <board>", or
# "HILdev <board>" for the local profile).
DEVICE_NAME=${HIL_DEVICE_NAME:-$(cfg rig.local_device_name)}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --boards) BOARDS=($2); shift 2 ;;
    --profiles) PROFILES=($2); shift 2 ;;
    --name) DEVICE_NAME=$2; shift 2 ;;
    --out) OUT_ROOT=$2; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Cap at 18 chars -- past that NimBLE drops the HID service UUID from the advert,
# then the name (README "How pairing works").
if [[ -n "$DEVICE_NAME" ]]; then
  if (( ${#DEVICE_NAME} > 18 )); then
    echo "== warning: --name '$DEVICE_NAME' is ${#DEVICE_NAME} chars; truncating to 18" >&2
    DEVICE_NAME=${DEVICE_NAME:0:18}
  fi
  # PlatformIO shlex-splits PLATFORMIO_BUILD_FLAGS: '"..."' keeps the inner
  # double quotes so the compiler sees a real C string literal (needed -- a
  # name can contain a space).
  export PLATFORMIO_BUILD_FLAGS="${PLATFORMIO_BUILD_FLAGS:-} -DHIL_DEVICE_NAME='\"${DEVICE_NAME}\"'"
  echo "== advertised name: $DEVICE_NAME"
fi

profile_suffix() { case "$1" in
  default) echo "" ;; specials) echo "-specials" ;;
  minimal) echo "-minimal" ;; maxbtn) echo "-maxbtn" ;;
  local) echo "-local" ;;
  *) echo "unknown profile: $1" >&2; exit 2 ;; esac; }
board_chip() { case "$1" in
  esp32dev) echo esp32 ;; esp32c3) echo esp32c3 ;; esp32s3) echo esp32s3 ;;
  esp32c6) echo esp32c6 ;; esp32h2) echo esp32h2 ;;
  *) echo "unknown board: $1" >&2; exit 2 ;; esac; }

if [[ -n "${LIB_REF:-}" ]]; then
  echo "== library $LIB_DIR @ $LIB_REF"
  git -C "$LIB_DIR" fetch --all --quiet
  git -C "$LIB_DIR" checkout --quiet "$LIB_REF"
  git -C "$LIB_DIR" pull --ff-only --quiet 2>/dev/null || true
fi
echo "== library at $(git -C "$LIB_DIR" describe --tags --always --dirty) ($(git -C "$LIB_DIR" rev-parse --abbrev-ref HEAD))"

mkdir -p "$OUT_ROOT"
for board in "${BOARDS[@]}"; do
  chip=$(board_chip "$board")
  for profile in "${PROFILES[@]}"; do
    env="${board}$(profile_suffix "$profile")"
    echo "== build $env  (board=$board chip=$chip profile=$profile)"
    "$PIO" run -e "$env" -d "$REPO/firmware"
    ide=$(mktemp)
    "$PIO" run -e "$env" -d "$REPO/firmware" -t idedata > "$ide" 2>/dev/null
    python3 builder/make_bundle.py \
      --build-dir "$REPO/firmware/.pio/build/$env" \
      --idedata "$ide" --env "$env" --profile "$profile" \
      --board "$board" --chip "$chip" --lib-dir "$LIB_DIR" --out-root "$OUT_ROOT" \
      --device-name "$DEVICE_NAME"
    rm -f "$ide"
  done
done

if [[ -n "${PUSH:-}" ]]; then
  ssh_host=$(cfg tester.ssh_host); ssh_user=$(cfg tester.ssh_user)
  bundle_dir=$(cfg tester.bundle_dir); bundle_dir=${bundle_dir:-'~/hil-bundles'}
  dest="${ssh_user:+$ssh_user@}$ssh_host:$bundle_dir/"
  [[ -n "$ssh_host" ]] || { echo "PUSH set but [tester].ssh_host empty" >&2; exit 2; }
  echo "== rsync bundles -> $dest"
  rsync -a --delete "$OUT_ROOT"/ "$dest"
fi

echo "== bundles in $OUT_ROOT:"
ls -1 "$OUT_ROOT"
