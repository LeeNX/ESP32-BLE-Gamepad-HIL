#!/usr/bin/env bash
# TESTER role, PRIVILEGED half. Run once by a host admin (root / sudo). Does
# everything that needs root; the CI/test user then runs tester/bootstrap.sh
# (no sudo) for the venv + config. Idempotent -- safe to re-run.
#
#   sudo tester/bootstrap-host.sh --user hil
#   sudo HIL_USER=hil tester/bootstrap-host.sh
#
# What it does: apt deps, system locale, the bluetooth service + rfkill unblock,
# adds the target user to dialout/input/plugdev, and installs the DUT udev rule.
#
# What it does NOT do (unprivileged -- tester/bootstrap.sh) or manual (hardware
# / secrets): the pytest venv, hil_config.local.toml, attaching the ESP32 + BLE
# adapter, the CI ssh key in the user's ~/.ssh/authorized_keys.
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET_USER=${HIL_USER:-}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) TARGET_USER=$2; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "must run as root: sudo tester/bootstrap-host.sh --user <ci-user>" >&2
  exit 1
fi
TARGET_USER=${TARGET_USER:-${SUDO_USER:-}}
if [[ -z "$TARGET_USER" ]]; then
  echo "set the target (CI/test) user: --user <name> or HIL_USER=<name>" >&2
  exit 2
fi
id "$TARGET_USER" >/dev/null 2>&1 || { echo "no such user: $TARGET_USER" >&2; exit 2; }

echo "== apt deps"
apt-get update -qq
apt-get install -y --no-install-recommends \
  bluez rfkill upower \
  python3-venv python3-dev build-essential \
  git rsync openssh-client \
  locales-all
# upower: userland battery cross-check (test_battery.py). It reads BlueZ's
# org.bluez.Battery1 interface, which the tests also check directly via D-Bus.
# locales-all: sshd forwards the operator's LANG/LC_* (a Mac sends
# LC_ALL=en_US.UTF-8) and bash/python warn if that locale isn't built. Shipping
# every locale is simplest for a box many people ssh into; pin the node's own
# default too so cron/CI runs are deterministic regardless of client.
update-locale LANG=C.UTF-8

echo "== bluetooth service"
systemctl enable --now bluetooth
# rfkill lives in /usr/sbin; a fresh Pi image often ships BT soft-blocked, which
# makes `bluetoothctl power on` fail with org.bluez.Error.Failed. main.conf
# AutoEnable=true (Debian/RPi default) then powers it once unblocked.
RFKILL=$(command -v rfkill || echo /usr/sbin/rfkill)
"$RFKILL" unblock bluetooth || true
if "$RFKILL" list bluetooth 2>/dev/null | grep -q 'Soft blocked: yes'; then
  echo "   WARNING: bluetooth still soft-blocked -- pairing will not work"
fi

echo "== groups for $TARGET_USER (serial / input / hidraw)"
# 'input' is the one people miss: without it evdev.list_devices() returns [] for
# this user and every test times out finding the gamepad node.
usermod -aG dialout,input,plugdev "$TARGET_USER"

echo "== udev rule for the DUT hidraw node"
# VID:PID 1D34:8010 is the HIL firmware's HID identity (hil_profile.h). The
# rule makes /dev/hidraw* group-rw so a `plugdev` user can drive Feature /
# Output reports (test_feature_report.py / test_output_report.py) without root.
rule='SUBSYSTEM=="hidraw", KERNELS=="0005:1D34:8010.*", MODE="0660", GROUP="plugdev"'
echo "$rule" > /etc/udev/rules.d/99-esp32-gamepad.rules
udevadm control --reload-rules
udevadm trigger

# Tailscale -- so a GitHub-hosted CI runner can reach this tester over the
# tailnet (.github/workflows/hil.yml). Skip with NO_TAILSCALE=1.
if [[ -z "${NO_TAILSCALE:-}" ]] && ! command -v tailscale >/dev/null 2>&1; then
  echo "== tailscale"
  curl -fsSL https://tailscale.com/install.sh | sh
  echo "   run:  sudo tailscale up --ssh   (or with --authkey ...); then note"
  echo "         its MagicDNS name for the HIL_TESTER_HOST repo secret"
fi

cat <<EOF

== host setup done for '$TARGET_USER'. Next:
  1. $TARGET_USER logs out/in (or reboot) so the group adds take effect
  2. as $TARGET_USER, no sudo:  tester/bootstrap.sh
  3. attach the ESP32(s) on a powered hub + a BLE adapter
  4. for CI: sudo tailscale up (tag it for the ACL), add the CI ssh key to
     $TARGET_USER's ~/.ssh/authorized_keys, and set the repo secrets
     (TS_OAUTH_* + HIL_TESTER_HOST/USER/SSH_KEY) -- see .github/workflows/hil.yml
EOF
