"""Which configured boards are actually usable on this tester.

A board ([board.<name>] in hil_config.toml) is *present* when it is enabled, its
serial port(s) are set (not CHANGE-ME), and the device node(s) exist. Optionally
(--probe) esptool must also be able to talk to the flash port.

tester/test.sh calls `--check <board>` to SKIP a bundle whose board isn't here
instead of failing; CI calls `--present` / `--json` to pick the flash/test set.

    PYTHONPATH=host python3 -m hil.detect                 # human table
    PYTHONPATH=host python3 -m hil.detect --present       # space-separated names
    PYTHONPATH=host python3 -m hil.detect --json
    PYTHONPATH=host python3 -m hil.detect --check esp32c3 # exit 0 present, 1 absent
    PYTHONPATH=host python3 -m hil.detect --probe ...     # + esptool chip-id (resets)
"""

import json
import subprocess
import sys
from pathlib import Path

from hil.config import load

_CHIP = {"esp32dev": "esp32"}


def _reason(board, cfg, probe):
    """Empty string = usable; otherwise why not."""
    b = cfg.get("board", {}).get(board, {})
    if str(b.get("enabled", True)).lower() == "false":
        return "disabled (enabled=false)"
    port = b.get("port", "")
    if not port:
        return "no port in config"
    flash = b.get("flash_port") or port
    for p in (port, flash):
        if "CHANGE-ME" in p:
            return "port not set (CHANGE-ME)"
        if not Path(p).exists():
            return f"no device at {p}"
    if probe:
        chip = _CHIP.get(board, board)
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "esptool",
                "--chip",
                chip,
                "--port",
                flash,
                "--before",
                "default-reset",
                "chip-id",
            ],
            capture_output=True,
        )
        if r.returncode != 0:
            return f"esptool could not talk to {flash}"
    return ""


def scan(probe=False):
    cfg = load()
    present, absent = [], {}
    for board in cfg.get("board", {}):
        why = _reason(board, cfg, probe)
        if why:
            absent[board] = why
        else:
            present.append(board)
    return present, absent


def main(argv):
    mode, check, probe = "table", None, False
    it = iter(argv)
    for a in it:
        if a == "--present":
            mode = "present"
        elif a == "--json":
            mode = "json"
        elif a == "--probe":
            probe = True
        elif a == "--check":
            mode, check = "check", next(it)
        else:
            sys.exit(f"unknown arg: {a}")

    present, absent = scan(probe)

    if mode == "present":
        print(" ".join(present))
    elif mode == "json":
        print(json.dumps({"present": present, "absent": absent}, indent=2))
    elif mode == "check":
        if check in present:
            return 0
        print(absent.get(check, f"unknown board {check!r}"), file=sys.stderr)
        return 1
    else:
        for b in present:
            print(f"  {b:<10} present")
        for b, why in absent.items():
            print(f"  {b:<10} -- {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
