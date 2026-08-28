"""bluetoothctl wrappers for pairing the DUT (Just Works / NoInputNoOutput).

See LinuxHIDTesting.md sections 3-4 for the manual equivalents.
"""

import re
import subprocess
import time


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def _btctl(script, timeout=30):
    """Feed a newline-separated script to an interactive bluetoothctl session."""
    return subprocess.run(
        ["bluetoothctl"], input=script + "\nquit\n",
        capture_output=True, text=True, timeout=timeout,
    )


def power_on():
    _btctl("power on")


def paired_devices():
    out = _run(["bluetoothctl", "devices", "Paired"]).stdout
    devs = {}
    for line in out.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.+)", line.strip())
        if m:
            devs[m.group(1)] = m.group(2)
    return devs


def info(mac):
    return _run(["bluetoothctl", "info", mac]).stdout


def is_bonded(mac):
    return "Bonded: yes" in info(mac)


def is_connected(mac):
    return "Connected: yes" in info(mac)


def scan_for(name_contains, timeout=30):
    """Run a timed scan, return the MAC of the first device whose name matches."""
    # `--timeout N scan on` blocks for N seconds discovering, then exits.
    proc = subprocess.Popen(
        ["bluetoothctl", "--timeout", str(timeout), "scan", "on"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            out = _run(["bluetoothctl", "devices"]).stdout
            for line in out.splitlines():
                m = re.match(r"Device ([0-9A-F:]{17}) (.+)", line.strip())
                if m and name_contains in m.group(2):
                    return m.group(1)
            time.sleep(2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return None


def remove(mac):
    _btctl(f"remove {mac}")


def pair(mac):
    script = "\n".join([
        "agent NoInputNoOutput",
        "default-agent",
        f"pair {mac}",
        f"trust {mac}",
        f"connect {mac}",
    ])
    return _btctl(script, timeout=45)


def ensure_paired(name_contains, known_mac=None, want_fresh=False):
    """Return a bonded+connected MAC for the DUT, pairing if needed.

    want_fresh: drop any existing bond first (used when the HID descriptor
    changed, e.g. a profile switch -- hosts cache the descriptor).
    """
    power_on()

    mac = known_mac
    if mac is None:
        for m, n in paired_devices().items():
            if name_contains in n:
                mac = m
                break

    if mac and want_fresh:
        remove(mac)
        mac = None

    if mac and is_bonded(mac):
        if not is_connected(mac):
            _btctl(f"connect {mac}", timeout=20)
        if is_connected(mac):
            return mac

    if mac is None:
        mac = scan_for(name_contains)
        if mac is None:
            raise RuntimeError(
                f"could not find a BLE device named ~{name_contains!r} to pair"
            )

    res = pair(mac)
    if not is_bonded(mac):
        raise RuntimeError(
            f"pairing {mac} failed:\n{res.stdout[-2000:]}"
        )
    for _ in range(10):
        if is_connected(mac):
            return mac
        _btctl(f"connect {mac}", timeout=15)
        time.sleep(1)
    raise RuntimeError(f"{mac} bonded but never connected")
