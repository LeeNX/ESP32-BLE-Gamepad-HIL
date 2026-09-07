#!/usr/bin/env bash
# What's running on the HIL rig right now (or the last run's verdict).
#   ssh <tester> ESP32-BLE-Gamepad-HIL/tester/rig-status.sh
exec python3 "$(dirname "$0")/../host/hil/riglock.py" status
