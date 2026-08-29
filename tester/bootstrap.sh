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
  git rsync openssh-client

echo "== bluetooth service"
$SUDO systemctl enable --now bluetooth
$SUDO rfkill unblock bluetooth || true

echo "== groups for $TARGET_USER (serial / input / hidraw)"
$SUDO usermod -aG dialout,input,plugdev "$TARGET_USER"

echo "== udev rule for the DUT hidraw node"
# VID:PID 1D34:8010 is the ESP32-BLE-Gamepad default HID identity; the test
# suite reads /dev/hidraw* for it, so it needs to be group-readable.
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
