#!/usr/bin/env bash
# Bluepad32 as the observer, by swapping two rig boards' roles for one run (no extra hardware):
#   esp32dev -> Bluepad32 HIL host firmware, esp32c3 -> hil_runner `sinput` profile; run tester/bp32_hil.py;
#   then restore both boards to their current default bundles and drop their BlueZ bonds (CI pairs fresh).
#
#   tester/rig-lock.sh -- tester/bp32_swap.sh <bluepad32 host image dir> <esp32c3 sinput bundle dir>
#
# The host image dir holds bootloader.bin, partitions.bin, ota_data_initial.bin, firmware.bin (factory app at
# 0x20000) from the Bluepad32 HIL rig's host/ build, with HIL_ALLOW_ADDR set to the esp32c3's BLE address. The
# esp32dev's settings region (0x9000-0x12000) is erased before and after, since the two firmwares lay it out differently.
# Keep both dirs outside ~/hil-bundles: builder pushes there with rsync --delete.
set -u
V=~/.venvs/hil/bin/python
cfg() { python3 host/hil/config.py "$1"; }
C3P=$(cfg board.esp32c3.port); C3F=$(cfg board.esp32c3.flash_port); DEVP=$(cfg board.esp32dev.port)
ET=("$V" -m esptool --chip esp32 --port "$DEVP" --baud 460800)
H=${1:?usage: bp32_swap.sh <bluepad32 host image dir> <esp32c3 sinput bundle dir>}
SINPUT_BUNDLE=${2:?usage: bp32_swap.sh <bluepad32 host image dir> <esp32c3 sinput bundle dir>}
newest() { local d=("$@"); echo "${d[-1]}"; }  # globs sort, so the last match is the newest lib sha
unbond() {
  local n m
  for n in "HILpad esp32c3" "HILpad esp32dev"; do
    for m in $(bluetoothctl devices | awk -v n="$n" 'index($0, n) {print $2}'); do bluetoothctl remove "$m" >/dev/null; done
  done
}

echo "== setup"
unbond
"$V" tester/flash.py "$SINPUT_BUNDLE" --port "$C3F" 2>&1 | tail -1
"${ET[@]}" erase-region 0x9000 0x9000 2>&1 | grep -iE "erased|error"
"${ET[@]}" write-flash 0x1000 "$H/bootloader.bin" 0x8000 "$H/partitions.bin" 0xf000 "$H/ota_data_initial.bin" \
    0x20000 "$H/firmware.bin" 2>&1 | grep -iE "verified|error" | tail -1
sleep 3
PYTHONPATH=host timeout 600 "$V" tester/bp32_hil.py "$C3P" "$DEVP"; rc=$?

echo "== restore"
"${ET[@]}" erase-region 0x9000 0x9000 2>&1 | grep -iE "erased|error"
"$V" tester/flash.py "$(newest ~/hil-bundles/esp32dev-default-*)" --port "$DEVP" 2>&1 | tail -1
"$V" tester/flash.py "$(newest ~/hil-bundles/esp32c3-default-*)" --port "$C3F" 2>&1 | tail -1
unbond
exit $rc
