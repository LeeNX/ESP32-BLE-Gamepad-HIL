"""The BLE SInput HID device (2E8A:10C6) on the tester and which kernel driver holds it: hid-generic, or the
LeeNX/linux-hid-sinput driver (`sinput`), switched through the root-owned wrapper tester/bootstrap-sinput.sh
installs (`sudo -n hil-sinput-driver load|unload|...`). Shared by tester/sinput_hil.py and tester/sdl3_compare.py.
"""

import glob
import os
import subprocess
import time


def driver_ctl(action):
    """`hil-sinput-driver <action>` -> (ok, output)."""
    p = subprocess.run(
        ["sudo", "-n", "hil-sinput-driver", action], capture_output=True, text=True, timeout=60
    )
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def hid_dev():
    """sysfs HID device dir of the (single) BLE SInput device, or None."""
    devs = sorted(glob.glob("/sys/bus/hid/devices/0005:2E8A:10C6.*"))
    return devs[-1] if devs else None


def bound_driver():
    d = hid_dev()
    if not d or not os.path.exists(f"{d}/driver"):
        return None
    return os.path.basename(os.path.realpath(f"{d}/driver"))


def wait_driver(name, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if bound_driver() == name:
            return True
        time.sleep(0.2)
    return False


def module_loaded():
    return os.path.isdir("/sys/module/sinput")
