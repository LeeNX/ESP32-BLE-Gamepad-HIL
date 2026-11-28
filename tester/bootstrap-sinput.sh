#!/usr/bin/env bash
# TESTER role, PRIVILEGED, optional: set a tester up for SInput testing (the
# hil_runner `sinput` profile and the out-of-tree LeeNX/linux-hid-sinput kernel
# driver). Run once by a host admin; idempotent, safe to re-run (e.g. to stage a
# newer driver .deb).
#
#   sudo tester/bootstrap-sinput.sh --user hil [sinput-common_<ver>_all.deb] [sinput-modules-<kernel>_<ver>_<arch>.deb ...]
#
# What it does:
#   1. udev rule: /dev/hidraw* group-rw (plugdev) for the SInput VID:PID
#      2E8A:10C6 over BLE, like 99-esp32-gamepad.rules does for 1D34:8010, so
#      the test user can read state reports and send SInput commands.
#   2. Stages the given driver .debs in root-owned /opt/hil-sinput/, named after
#      their own Package/Version/Architecture fields: any architecture (arm64 on a
#      Pi, amd64 on Ubuntu) and Ubuntu-style versions (0.4.5-ubuntu26.04), and a
#      GitHub release download's '.' for '+' doesn't matter. Pass sinput-common too
#      (module packages from 0.4.1 on depend on it) unless it's already installed.
#   3. Installs /usr/local/sbin/hil-sinput-driver and a sudoers entry letting the
#      test user run only that wrapper. The wrapper installs a .deb that is
#      already staged in /opt/hil-sinput (by version, for the running kernel and
#      architecture, with the staged sinput-common it needs), removes it,
#      loads/unloads the module, or re-probes SInput devices -- nothing else.
#      Loading takes over an already-connected SInput device from
#      hid-generic (the HID core re-probes on driver registration); unloading
#      hands it back by re-probing, so a test can switch drivers mid-connection.
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
    -h|--help) sed -n '2,29p' "$0"; exit 0 ;;
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
  # Named from the package's own fields, not the file name: a GitHub release
  # asset has '.' where the package name has '+' (6.18.60.rpt-rpi-v8).
  fields=$(dpkg-deb -f "$deb" Package Version Architecture) || { echo "not a .deb: $deb" >&2; exit 2; }
  pkg=$(sed -n 's/^Package: //p' <<<"$fields")
  ver=$(sed -n 's/^Version: //p' <<<"$fields")
  arch=$(sed -n 's/^Architecture: //p' <<<"$fields")
  # The same character sets the wrapper accepts for a version (no '/', so the
  # name can't leave /opt/hil-sinput).
  [[ $ver =~ ^[0-9][0-9A-Za-z.+~-]*$ && $arch =~ ^[a-z0-9]+$ ]] || { echo "unexpected version/arch in $deb: $ver $arch" >&2; exit 2; }
  case $pkg in
    sinput-modules-*-dbgsym) echo "debug symbols, not a driver package: $deb" >&2; exit 2 ;;
    sinput-common) [[ $arch == all ]] || { echo "sinput-common should be Architecture: all: $deb" >&2; exit 2; } ;;
    sinput-modules-*) [[ $pkg =~ ^sinput-modules-[0-9A-Za-z.+~-]+$ ]] || { echo "unexpected package name in $deb: $pkg" >&2; exit 2; } ;;
    *) echo "not a sinput-modules or sinput-common .deb: $deb ($pkg)" >&2; exit 2 ;;
  esac
  install -o root -g root -m 0644 "$deb" "/opt/hil-sinput/${pkg}_${ver}_${arch}.deb"
  echo "   staged ${pkg}_${ver}_${arch}.deb"
done

echo "== /usr/local/sbin/hil-sinput-driver"
cat > /usr/local/sbin/hil-sinput-driver <<'WRAPPER'
#!/bin/sh
# Root-owned; the HIL test user may run it via sudo (tester/bootstrap-sinput.sh).
#   hil-sinput-driver install <version> | remove | load | unload | reprobe | status
set -eu
dir=/opt/hil-sinput
pkg="sinput-modules-$(uname -r)"
arch=$(dpkg --print-architecture)
# Re-probe SInput HID devices that no driver holds (after an unload), so
# hid-generic takes them back without a BLE reconnect.
reprobe() {
  for dev in /sys/bus/hid/devices/*:2E8A:10C6.*; do
    [ -e "$dev" ] || continue
    [ -e "$dev/driver" ] && continue
    printf '%s' "$(basename "$dev")" > /sys/bus/hid/drivers_probe || true
  done
}
case "${1:-}" in
  install)
    # A package version, e.g. 0.4.1 or 0.4.5-ubuntu26.04; 0.4.5 also finds the
    # one staged -ubuntu<version> package of it. No '/': it's part of a path.
    ver="${2:-}"
    case "$ver" in ''|[!0-9]*|*[!0-9A-Za-z.+~-]*) echo "version must look like 0.4.5 or 0.4.5-ubuntu26.04" >&2; exit 2 ;; esac
    deb="$dir/${pkg}_${ver}_${arch}.deb"
    if [ ! -f "$deb" ]; then
      set -- "$dir/${pkg}_${ver}"-ubuntu*"_${arch}.deb"
      [ $# -le 1 ] || { echo "more than one staged package for $ver: $*" >&2; exit 2; }
      [ -f "$1" ] || { echo "not staged: $deb" >&2; exit 2; }
      deb=$1
    fi
    # The module package depends on sinput-common of its version (without any
    # -ubuntu<version>) or later. Install the staged one along with it, unless
    # that version or a newer one is installed already: a newer one may be what
    # another kernel's module needs, so it's never downgraded.
    cver=${ver%-ubuntu*}
    common="$dir/sinput-common_${cver}_all.deb"
    have=$(dpkg-query -W -f '${db:Status-Abbrev}${Version}' sinput-common 2>/dev/null || true)
    case "$have" in 'ii '*) have=${have#ii } ;; *) have= ;; esac
    if [ -n "$have" ] && dpkg --compare-versions "$have" ge "$cver"; then
      dpkg -i "$deb"
    elif [ -f "$common" ]; then
      dpkg -i "$common" "$deb"
    else
      # Modules before 0.4.1 don't depend on it; dpkg says if this one does.
      echo "note: no sinput-common_${cver}_all.deb staged (installed: ${have:-none})" >&2
      dpkg -i "$deb"
    fi
    modprobe sinput ;;
  remove) modprobe -r sinput 2>/dev/null || true; dpkg -r "$pkg"; reprobe ;;
  load) modprobe sinput ;;
  unload) modprobe -r sinput; reprobe ;;
  reprobe) reprobe ;;
  status) dpkg-query -W -f '${Package} ${Version}\n' "$pkg" 2>/dev/null || echo "$pkg not installed"
          grep -q '^sinput ' /proc/modules && echo "module loaded" || echo "module not loaded"
          ls "$dir" ;;
  *) echo "usage: $0 install <version> | remove | load | unload | reprobe | status" >&2; exit 2 ;;
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
