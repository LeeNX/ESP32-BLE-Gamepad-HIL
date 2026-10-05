"""USB health of the tester host, so a run can stop before a wedged USB bus turns into a page of bogus failures.

On the reference Raspberry Pi 3B+ every USB port and the Ethernet share one dwc_otg controller. When it wedges
(parallel flashing has done it: 2026-09-11, 2026-10-04) the kernel logs `dwc_otg_hcd_urb_dequeue ... Timed out`
warnings, the Ethernet loses carrier and USB serial devices drop off or return EIO. Checks:

  - no new dwc_otg dequeue timeouts since the baseline (`baseline` records the current count);
  - every configured board's port / flash_port device node exists;
  - the [rig] health_iface interface (default eth0; "" to skip) has carrier, if it exists.

  python3 -m hil.usbhealth baseline        # remember the current dwc_otg warning count
  python3 -m hil.usbhealth check           # exit 0 healthy, 1 unhealthy (one-line reason on stdout)

The baseline lives in $XDG_CACHE_HOME/esp32-hil/usb-baseline. Reading the kernel log needs dmesg access
(kernel.dmesg_restrict=0, the Raspberry Pi OS default).
"""

import os
import pathlib
import subprocess
import sys

from hil.config import load

CACHE = (
    pathlib.Path(os.environ.get("XDG_CACHE_HOME") or (pathlib.Path.home() / ".cache")) / "esp32-hil"
)
BASELINE = CACHE / "usb-baseline"
DWC_WARNING = "dwc_otg_hcd_urb_dequeue"


def dwc_warnings():
    """Count of dwc_otg dequeue-timeout warnings in the kernel log, or None if it can't be read."""
    r = subprocess.run(["dmesg"], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return sum(DWC_WARNING in line for line in r.stdout.splitlines())


def expected_devices(cfg):
    devs = []
    for b in cfg.get("board", {}).values():
        if str(b.get("enabled", True)).lower() == "false":
            continue
        for key in ("port", "flash_port"):
            p = b.get(key, "")
            if p and "CHANGE-ME" not in p and p not in devs:
                devs.append(p)
    return devs


def check(cfg):
    """List of problems; empty = healthy."""
    problems = []
    count = dwc_warnings()
    if count is None:
        problems.append("can't read the kernel log (dmesg)")
    else:
        base = int(BASELINE.read_text()) if BASELINE.exists() else count
        if count > base:
            problems.append(f"{count - base} new dwc_otg dequeue timeouts")
    missing = [d for d in expected_devices(cfg) if not pathlib.Path(d).exists()]
    if missing:
        problems.append("missing " + ", ".join(pathlib.Path(d).name for d in missing))
    iface = cfg.get("rig", {}).get("health_iface", "eth0")
    carrier = pathlib.Path(f"/sys/class/net/{iface}/carrier") if iface else None
    if carrier and carrier.parent.exists():
        try:
            up = carrier.read_text().strip() == "1"
        except OSError:  # reading carrier of an administratively down link raises EINVAL
            up = False
        if not up:
            problems.append(f"{iface} has no carrier")
    return problems


def main(argv):
    cmd = argv[0] if argv else "check"
    if cmd == "baseline":
        count = dwc_warnings()
        if count is None:
            print("usb: can't read the kernel log (dmesg)")
            return 1
        CACHE.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(f"{count}\n")
        print(f"usb: baseline {count} dwc_otg warnings")
        return 0
    if cmd == "check":
        problems = check(load())
        print("usb: OK" if not problems else "usb: UNHEALTHY: " + "; ".join(problems))
        return 1 if problems else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
