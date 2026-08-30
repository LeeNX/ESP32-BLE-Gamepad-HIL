"""Environment fingerprint for the results -- so a regression report says
'was green on kernel 6.18.34 / bluez 5.82' and an outlier latency number can
be checked against 'the box was loaded / throttling at the time'.

`static_env()` is collected once per run; `dynamic()` is sampled tight around
the benchmark (before + after).
"""

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
    v = _cmd(["bluetoothctl", "--version"])          # "bluetoothctl: 5.82"
    m = re.search(r"(\d+\.\d+)", v)
    return m.group(1) if m else (v or None)


def _adapter():
    out = _cmd(["bluetoothctl", "show"])
    name = re.search(r"Name:\s*(.+)", out)
    mfr = re.search(r"Manufacturer:.*\((\d+)\)", _cmd(["hciconfig", "-a"]))
    return {"name": name.group(1).strip() if name else None,
            "manufacturer_id": int(mfr.group(1)) if mfr else None}


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
        "python": platform.python_version(),
        "pkgs": {p: _pkg(p) for p in ("pytest", "evdev", "dbus-fast", "pyserial")},
        "hostname": platform.node(),
    }


def _loadavg():
    try:
        return list(os.getloadavg())
    except Exception:
        return None


def _cpu_temp_c():
    for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            return round(int(open(p).read()) / 1000, 1)
        except Exception:
            pass
    v = _cmd(["vcgencmd", "measure_temp"])            # "temp=61.0'C"
    m = re.search(r"([\d.]+)", v)
    return float(m.group(1)) if m else None


def _cpu_freq_mhz():
    try:
        khz = int(open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq").read())
        return round(khz / 1000)
    except Exception:
        return None


def _throttled():
    v = _cmd(["vcgencmd", "get_throttled"])           # "throttled=0x0"
    m = re.search(r"0x[0-9a-fA-F]+", v)
    return m.group(0) if m else None


def dynamic():
    """A one-shot sample of everything that could skew a timing measurement."""
    return {
        "loadavg": _loadavg(),
        "cpu_temp_c": _cpu_temp_c(),
        "cpu_freq_mhz": _cpu_freq_mhz(),
        "throttled": _throttled(),
    }


if __name__ == "__main__":
    import json
    print(json.dumps({"static": static_env(), "dynamic": dynamic()}, indent=2))
