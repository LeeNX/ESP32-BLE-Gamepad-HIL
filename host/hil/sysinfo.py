"""Environment fingerprint for the results -- so a regression report says
'was green on kernel 6.18.34 / bluez 5.82' and an outlier latency number can
be checked against 'the box was loaded / throttling at the time'.

`static_env()` is collected once per run; `dynamic()` is sampled tight around
the benchmark (before + after).
"""

import datetime
import functools
import glob
import os
import platform
import re
import subprocess


def _cmd(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=8).stdout.strip()
    except Exception:
        return ""


def _pkg(name):
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return None


def _os_release():
    try:
        return platform.freedesktop_os_release().get("PRETTY_NAME")
    except Exception:
        for ln in _cmd(["cat", "/etc/os-release"]).splitlines():
            if ln.startswith("PRETTY_NAME="):
                return ln.split("=", 1)[1].strip('"')
    return None


def _cpu_model():
    try:
        for ln in open("/proc/cpuinfo"):
            if ln.startswith(("model name", "Model")):
                return ln.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or None


def _bluez_version():
    v = _cmd(["bluetoothctl", "--version"])  # "bluetoothctl: 5.82"
    m = re.search(r"(\d+\.\d+)", v)
    return m.group(1) if m else (v or None)


def _adapter():
    out = _cmd(["bluetoothctl", "show"])
    name = re.search(r"Name:\s*(.+)", out)
    mfr = re.search(r"Manufacturer:.*\((\d+)\)", _cmd(["hciconfig", "-a"]))
    return {
        "name": name.group(1).strip() if name else None,
        "manufacturer_id": int(mfr.group(1)) if mfr else None,
    }


def _usb_topology():
    """Raw `lsusb -t` -- what's plugged into what, and at what negotiated
    speed. Grew out of the dwc_otg USB-bus crash investigation
    (hil-rig-usb-bus-crash-sep11 memory): a hub's "Self Powered" descriptor
    bit is self-reported (a hub can claim it without an adapter actually
    plugged in), so this is supporting evidence for "is the board hub
    powered", not independent proof -- `lsusb -v` on the hub-class devices
    for the Self Powered / MaxPower fields is the fuller check, done by hand
    when that question comes up rather than parsed here every run."""
    return _cmd(["lsusb", "-t"]) or None


@functools.lru_cache(maxsize=1)
def static_env():
    return {
        "distro": _os_release(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "cpu": _cpu_model(),
        "nproc": os.cpu_count(),
        "bluez": _bluez_version(),
        "adapter": _adapter(),
        "usb_topology": _usb_topology(),
        "python": platform.python_version(),
        "pkgs": {p: _pkg(p) for p in ("pytest", "evdev", "dbus-fast", "pyserial")},
        "hostname": platform.node(),
    }


def _loadavg():
    try:
        return list(os.getloadavg())
    except Exception:
        return None


# The zone that is the CPU itself: x86 package sensor (coretemp), the Pi's SoC sensor. ACPI zones
# (acpitz) on a PC are board/chassis sensors that sit ~25 degC below the CPU -- the fallback only.
_CPU_ZONES = ("x86_pkg_temp", "cpu-thermal", "cpu_thermal", "soc_thermal")


def _cpu_temp_c():
    zones = []
    for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
        try:
            zones.append((open(f"{z}/type").read().strip(), f"{z}/temp"))
        except Exception:
            pass
    zones.sort(key=lambda t: t[0] not in _CPU_ZONES)  # stable: CPU zones first, else glob order
    for _, p in zones:
        try:
            return round(int(open(p).read()) / 1000, 1)
        except Exception:
            pass
    v = _cmd(["vcgencmd", "measure_temp"])  # "temp=61.0'C"
    m = re.search(r"([\d.]+)", v)
    return float(m.group(1)) if m else None


def _cpu_freq_mhz():
    try:
        khz = int(open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").read())
        return round(khz / 1000)
    except Exception:
        return None


def _throttled():
    v = _cmd(["vcgencmd", "get_throttled"])  # "throttled=0x0"
    m = re.search(r"0x[0-9a-fA-F]+", v)
    if m:
        return m.group(0)
    return None


def _undervoltage_now():
    """rpi_volt hwmon alarm -- unlike vcgencmd, readable with no sudo/group."""
    for name_path in glob.glob("/sys/class/hwmon/hwmon*/name"):
        try:
            if open(name_path).read().strip() != "rpi_volt":
                continue
            alarm = os.path.join(os.path.dirname(name_path), "in0_lcrit_alarm")
            return open(alarm).read().strip() == "1"
        except Exception:
            pass
    return None


def _uptime_s():
    try:
        return round(float(open("/proc/uptime").read().split()[0]))
    except Exception:
        return None


def _boot_id():
    """Changes on every boot -- how a report tells a reboot between two samples for sure."""
    try:
        return open("/proc/sys/kernel/random/boot_id").read().strip()
    except Exception:
        return None


def _dwc_warnings():
    """dwc_otg dequeue timeouts in the kernel log since boot (hil.usbhealth's count), or None -- also
    None on a host with no dwc_otg controller (anything but a Pi <= 3), where the count is always a
    meaningless 0. A non-zero count is always reported."""
    try:
        from hil.usbhealth import dwc_warnings

        n = dwc_warnings()
    except Exception:
        return None
    if n == 0 and not os.path.isdir("/sys/bus/platform/drivers/dwc_otg"):
        return None
    return n


# One line per Bluetooth controller wedge: the controller stops taking HCI commands, the kernel resets
# it and every LE link on it drops. mimir's Intel 8087:07da does it during esp32dev pairing
# (hil-phase2-link-drop memory); a run's failures in that window are the adapter, not the firmware.
# The kernel logs it as "hciN: hardware error 0x.." (hci_core, as on mimir) or, when the driver installs
# its own handler (btintel on newer Intel parts), "hciN: Hardware error 0x.." -- one or the other per
# event, never both, so matching either case can't double-count. ("Injecting HCI hardware error event"
# and "Controller not accepting commands" accompany it but don't match.)
_BT_HW_ERROR = re.compile(r"\bhci\d+: hardware error\b", re.IGNORECASE)


def count_bt_hw_errors(log):
    """Bluetooth controller hardware-error lines in kernel-log text."""
    return sum(bool(_BT_HW_ERROR.search(ln)) for ln in log.splitlines())


def _bt_hw_errors():
    """Bluetooth controller hardware errors in the kernel log since boot, or None if it can't be read.

    From the journal (this boot's kernel messages) when it's readable, else dmesg. The dmesg ring buffer is too small
    to hold a whole boot on a busy host: on mimir, Wi-Fi deauth churn pushed old lines out, so the count dropped
    between a run's two samples (9 -> 8) and the run had no delta. Reading the journal needs the systemd-journal
    group (or root); without it journalctl prints nothing from the kernel, which falls through to dmesg.
    """
    for args in (["journalctl", "-k", "-b", "-q", "--no-pager", "-o", "cat"], ["dmesg"]):
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=8)
        except Exception:
            continue
        # a kernel log since boot is never empty: empty output means no access, not "no errors"
        if r.returncode == 0 and r.stdout.strip():
            return count_bt_hw_errors(r.stdout)
    return None


def _dwc_burst():
    """The tester's effective [rig] health_dwc_burst (hil_config.local.toml applied), or None --
    recorded so a report rendered elsewhere (the CI runner) warns at the tester's threshold."""
    try:
        from hil.config import load

        return int(load().get("rig", {}).get("health_dwc_burst", 12))
    except Exception:
        return None


def dynamic():
    """A one-shot sample of everything that could skew a timing measurement, plus the host's
    uptime, its dwc_otg warning count (a Pi USB bus degrading since the last reboot) and its
    Bluetooth controller hardware-error count. Pi-only fields are None elsewhere."""
    return {
        "sampled_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "uptime_s": _uptime_s(),
        "boot_id": _boot_id(),
        "dwc_otg_warnings": _dwc_warnings(),
        "dwc_otg_burst": _dwc_burst(),
        "bt_hw_errors": _bt_hw_errors(),
        "loadavg": _loadavg(),
        "cpu_temp_c": _cpu_temp_c(),
        "cpu_freq_mhz": _cpu_freq_mhz(),
        "throttled": _throttled(),
        "undervoltage_now": _undervoltage_now(),
    }


if __name__ == "__main__":
    import json
    import sys

    if "--dynamic-only" in sys.argv:
        print(json.dumps(dynamic(), indent=2))
    else:
        print(json.dumps({"static": static_env(), "dynamic": dynamic()}, indent=2))
