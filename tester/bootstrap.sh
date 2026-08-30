#!/usr/bin/env bash
# TESTER role, UNPRIVILEGED half. Run as the CI/test user -- NO sudo, NO root.
# Idempotent; re-run after a tester/requirements.txt change (e.g. the dbus-fast add).
#
#   tester/bootstrap.sh                 # venv at ~/.venvs/hil, config stub, health check
#   HIL_VENV=/opt/hil tester/bootstrap.sh
#   tester/bootstrap.sh --with-host     # also run the privileged half via sudo (dev box)
#   tester/bootstrap.sh --skip-preflight
#
# The privileged half (apt deps, bluetooth/rfkill, dialout/input/plugdev groups,
# the udev rule) is a separate script an admin runs once:
#   sudo tester/bootstrap-host.sh --user <this-user>
#
# Still manual (hardware / secrets): attach the ESP32(s) on a powered hub + a
# BLE adapter; set the real serial port(s) in hil_config.local.toml; add the CI
# ssh key to ~/.ssh/authorized_keys.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$(pwd)
VENV=${HIL_VENV:-$HOME/.venvs/hil}

WITH_HOST=0
SKIP_PREFLIGHT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-host) WITH_HOST=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ $EUID -eq 0 ]]; then
  echo "run as the CI/test user, not root -- the privileged half is tester/bootstrap-host.sh" >&2
  exit 1
fi

if [[ $WITH_HOST == 1 ]]; then
  echo "== privileged half (sudo tester/bootstrap-host.sh --user $USER)"
  sudo "$REPO/tester/bootstrap-host.sh" --user "$USER"
fi

# --- preflight: is the privileged half done? ------------------------------
if [[ $SKIP_PREFLIGHT == 0 ]]; then
  missing=()
  command -v bluetoothctl >/dev/null 2>&1 || missing+=("bluez (bluetoothctl)")
  python3 -c 'import venv' 2>/dev/null || missing+=("python3-venv")
  groups_have=$(id -nG "$USER")
  for g in dialout input plugdev; do
    case " $groups_have " in *" $g "*) ;; *) missing+=("group:$g") ;; esac
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    cat >&2 <<EOF
== preflight failed -- the privileged half has not run for '$USER':
     ${missing[*]}
   have an admin run:  sudo tester/bootstrap-host.sh --user $USER
   then log out/in (for the group adds) and re-run this script.
   (bypass with --skip-preflight if you know better; --with-host to do it now)
EOF
    exit 1
  fi
fi

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
# Fill in the real serial port(s), then this node is ready.
[board.esp32dev]
port = "/dev/serial/by-id/CHANGE-ME"   # ls -l /dev/serial/by-id/

[board.esp32c3]
port = "/dev/serial/by-id/CHANGE-ME"        # UART0 bridge (see README "ESP32-C3 serial bridge")
# flash_port = "/dev/serial/by-id/CHANGE-ME" # native USB-C, if different from port
EOF
  echo "   wrote a stub -- set the real port(s)"
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
    import dbus_fast  # noqa: F401
    print("   dbus-fast: ok")
except Exception as e:
    print("   dbus-fast:", e, " <- GATT tests (test_device_info/test_battery) will skip")
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
  1. if you have not since the host setup: log out/in (or reboot) for the
     dialout/input/plugdev group adds
  2. attach the ESP32(s) on a powered hub + a BLE adapter
  3. if the "no stub needed" line above did not print, set the real
     [board.<b>].port in $REPO/hil_config.local.toml  ($ ls -l /dev/serial/by-id/)
  4. smoke test:  $VENV/bin/pytest --board esp32dev --no-flash --port <port> -k connection
  5. for CI: add the CI ssh key to ~/.ssh/authorized_keys and set the
     HIL_PI_HOST / HIL_PI_USER / HIL_PI_SSH_KEY repo secrets
EOF
