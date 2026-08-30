#!/usr/bin/env bash
# TESTER role: one-time node provisioning. Idempotent -- safe to re-run after a
# harness update or to converge a half-set-up box. Turns a fresh Debian/Ubuntu
# host (Raspberry Pi OS included) into a HIL tester that Gitea CI can just
# rsync-and-ssh into: apt deps, the pytest venv, serial/input/hidraw access,
# the udev rule, and a hil_config.local.toml stub.
#
#   tester/bootstrap.sh                 # provision for $USER, venv at ~/.venvs/hil
#   HIL_VENV=/opt/hil sudo -E -u hil tester/bootstrap.sh
#
# What it does NOT do (still manual, by design -- hardware + secrets):
#   - attach the ESP32 (on a powered USB hub) and a BLE adapter
#   - set the real serial port in hil_config.local.toml (ls -l /dev/serial/by-id/)
#   - first BLE pairing (happens automatically on the first pytest run)
#   - authorise the CI ssh key in ~/.ssh/authorized_keys
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
VENV=${HIL_VENV:-$HOME/.venvs/hil}
TARGET_USER=${SUDO_USER:-$USER}

if [[ $EUID -eq 0 && -z "${SUDO_USER:-}" ]]; then
  echo "run as the tester user (with sudo available), not as root directly" >&2
  exit 1
fi
SUDO=$([[ $EUID -eq 0 ]] && echo "" || echo "sudo")

echo "== apt deps"
$SUDO apt-get update -qq
$SUDO apt-get install -y --no-install-recommends \
  bluez rfkill \
  python3-venv python3-dev build-essential \
  git rsync openssh-client \
  locales-all
# locales-all: sshd forwards the operator's LANG/LC_* (a Mac sends
# LC_ALL=en_US.UTF-8) and bash/python warn if that locale isn't built. Shipping
# every locale is simplest for a box many people ssh into; pin the node's own
# default too so cron/CI runs are deterministic regardless of client.
$SUDO update-locale LANG=C.UTF-8

echo "== bluetooth service"
$SUDO systemctl enable --now bluetooth
# rfkill lives in /usr/sbin; a fresh Pi image often ships BT soft-blocked, which
# makes `bluetoothctl power on` fail with org.bluez.Error.Failed. main.conf
# AutoEnable=true (Debian/RPi default) then powers it once unblocked.
RFKILL=$(command -v rfkill || echo /usr/sbin/rfkill)
$SUDO "$RFKILL" unblock bluetooth || true
if $SUDO "$RFKILL" list bluetooth 2>/dev/null | grep -q 'Soft blocked: yes'; then
  echo "   WARNING: bluetooth still soft-blocked -- pairing will not work"
fi

echo "== groups for $TARGET_USER (serial / input / hidraw)"
# 'input' is the one people miss: without it evdev.list_devices() returns [] for
# this user and every test times out finding the gamepad node.
$SUDO usermod -aG dialout,input,plugdev "$TARGET_USER"

echo "== udev rule for the DUT hidraw node"
# VID:PID 1D34:8010 is the ESP32-BLE-Gamepad default HID identity. The pytest
# suite is pure-evdev and does not open /dev/hidraw*, but the rule keeps the
# node group-readable for manual HID inspection.
rule='SUBSYSTEM=="hidraw", KERNELS=="0005:1D34:8010.*", MODE="0660", GROUP="plugdev"'
echo "$rule" | $SUDO tee /etc/udev/rules.d/99-esp32-gamepad.rules >/dev/null
$SUDO udevadm control --reload-rules
$SUDO udevadm trigger

echo "== pytest venv at $VENV"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
"$VENV/bin/pip" -q install --upgrade pip
"$VENV/bin/pip" -q install -r "$REPO/tester/requirements.txt"

echo "== hil_config.local.toml stub"
port=$("$VENV/bin/python" "$REPO/host/hil/config.py" board.esp32dev.port 2>/dev/null || true)
if [[ -e "$REPO/hil_config.local.toml" ]]; then
  echo "   exists -- left untouched"
elif [[ -n "$port" && "$port" != *CHANGE-ME* && -e "$port" ]]; then
  # committed hil_config.toml already points at a device present on this box
  echo "   hil_config.toml already resolves board.esp32dev.port -> $port; no stub needed"
else
  cat > "$REPO/hil_config.local.toml" <<'EOF'
# Per-machine tester overrides. Deep-merged over hil_config.toml.
# Fill in the real serial port, then this node is ready.
[board.esp32dev]
port = "/dev/serial/by-id/CHANGE-ME"   # ls -l /dev/serial/by-id/
EOF
  echo "   wrote a stub -- set the real port"
fi

echo "== health check"
"$VENV/bin/python" - "$REPO" <<'PY' || true
import sys, glob
sys.path.insert(0, sys.argv[1] + "/host")
try:
    import evdev
    n = len(evdev.list_devices())
    print(f"   evdev: {n} readable input node(s)" +
          ("" if n else "  <- 0: not in 'input' group yet, or no session refresh"))
except Exception as e:
    print("   evdev:", e)
try:
    import subprocess
    out = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=8).stdout
    powered = [l.strip() for l in out.splitlines() if "Powered:" in l]
    print("   bt:", powered[0] if powered else "no controller")
except Exception as e:
    print("   bt:", e)
print("   serial ports:", ", ".join(glob.glob("/dev/serial/by-id/*")) or "none")
PY

cat <<EOF

== done. Remaining manual steps:
  1. log out/in (or reboot) so the dialout/input/plugdev group adds take effect
  2. attach the ESP32 on a powered hub + a BLE adapter
  3. if the "no stub needed" line above did not print, set [board.esp32dev].port
     in $REPO/hil_config.local.toml  ($ ls -l /dev/serial/by-id/)
  4. smoke test:  $VENV/bin/pytest --board esp32dev --no-flash --port <port> -k connection
  5. for CI: add the CI ssh key to ~/.ssh/authorized_keys and set the
     HIL_PI_HOST / HIL_PI_USER / HIL_PI_SSH_KEY repo secrets
EOF
