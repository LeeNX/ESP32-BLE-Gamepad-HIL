#!/usr/bin/env bash
# TESTER role, PRIVILEGED, optional: set a tester up for SInput testing (the
# hil_runner `sinput` profile and the out-of-tree LeeNX/linux-hid-sinput kernel
# driver). Run once by a host admin; idempotent, safe to re-run (e.g. to stage a
# newer driver .deb).
#
#   sudo tester/bootstrap-sinput.sh --user hil [sinput-modules-<kernel>_<ver>_arm64.deb ...]
#
# What it does:
#   1. udev rule: /dev/hidraw* group-rw (plugdev) for the SInput VID:PID
#      2E8A:10C6 over BLE, like 99-esp32-gamepad.rules does for 1D34:8010, so
#      the test user can read state reports and send SInput commands.
#   2. Stages the given driver .debs in root-owned /opt/hil-sinput/.
#   3. Installs /usr/local/sbin/hil-sinput-driver and a sudoers entry letting the
#      test user run only that wrapper. The wrapper installs a .deb that is
#      already staged in /opt/hil-sinput (by version, for the running kernel),
#      removes it, or loads/unloads the module -- nothing else.
#
# Why a wrapper and a root-owned staging dir: a package's maintainer scripts run
# as root, so letting the test user `dpkg -i` a file it controls is the same as
# giving it root. Here only an admin can put a .deb where the wrapper will take it.
set -euo pipefail

TARGET_USER=${HIL_USER:-}
DEBS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --user) TARGET_USER=$2; shift 2 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) DEBS+=("$1"); shift ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo "run as root (sudo)" >&2; exit 1; }
[[ -n "$TARGET_USER" ]] || { echo "--user <test user> (or HIL_USER) is required" >&2; exit 2; }
id "$TARGET_USER" >/dev/null

echo "== udev rule for SInput hidraw (2E8A:10C6)"
echo 'SUBSYSTEM=="hidraw", KERNELS=="0005:2E8A:10C6.*", MODE="0660", GROUP="plugdev"' \
  > /etc/udev/rules.d/99-esp32-gamepad-sinput.rules
udevadm control --reload-rules

echo "== staging dir /opt/hil-sinput"
install -d -o root -g root -m 0755 /opt/hil-sinput
for deb in "${DEBS[@]}"; do
  base=$(basename "$deb")
  [[ $base =~ ^sinput-modules-[^_/]+_[0-9.]+_arm64\.deb$ ]] || { echo "not a sinput-modules arm64 .deb: $base" >&2; exit 2; }
  install -o root -g root -m 0644 "$deb" "/opt/hil-sinput/$base"
  echo "   staged $base"
done

echo "== /usr/local/sbin/hil-sinput-driver"
cat > /usr/local/sbin/hil-sinput-driver <<'WRAPPER'
#!/bin/sh
# Root-owned; the HIL test user may run it via sudo (tester/bootstrap-sinput.sh).
#   hil-sinput-driver install <version> | remove | load | unload | status
set -eu
dir=/opt/hil-sinput
pkg="sinput-modules-$(uname -r)"
case "${1:-}" in
  install)
    ver="${2:-}"
    case "$ver" in ''|*[!0-9.]*) echo "version must be digits and dots" >&2; exit 2 ;; esac
    deb="$dir/${pkg}_${ver}_arm64.deb"
    [ -f "$deb" ] || { echo "not staged: $deb" >&2; exit 2; }
    dpkg -i "$deb"
    modprobe sinput ;;
  remove) modprobe -r sinput 2>/dev/null || true; dpkg -r "$pkg" ;;
  load) modprobe sinput ;;
  unload) modprobe -r sinput ;;
  status) dpkg-query -W -f '${Package} ${Version}\n' "$pkg" 2>/dev/null || echo "$pkg not installed"
          grep -q '^sinput ' /proc/modules && echo "module loaded" || echo "module not loaded"
          ls "$dir" ;;
  *) echo "usage: $0 install <version> | remove | load | unload | status" >&2; exit 2 ;;
esac
WRAPPER
chown root:root /usr/local/sbin/hil-sinput-driver
chmod 0755 /usr/local/sbin/hil-sinput-driver

echo "== sudoers: $TARGET_USER may run hil-sinput-driver"
tmp=$(mktemp)
echo "$TARGET_USER ALL=(root) NOPASSWD: /usr/local/sbin/hil-sinput-driver" > "$tmp"
visudo -cf "$tmp" >/dev/null
install -o root -g root -m 0440 "$tmp" /etc/sudoers.d/hil-sinput
rm -f "$tmp"

echo "== done. As $TARGET_USER:  sudo hil-sinput-driver status"
