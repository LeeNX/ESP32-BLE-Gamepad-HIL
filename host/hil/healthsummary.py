"""Rig health for the job summary: the tester host's state at the start and end of a run.

    python3 host/hil/healthsummary.py results/          # -> stdout (Markdown)

Reads results/rig-health-start.json and results/rig-health-end.json (`hil.sysinfo --dynamic-only`
samples that hil.yml takes under the rig lock, around the suite + matrix). Prints nothing if neither
exists (a rig commit from before these samples).

Mostly here for the dwc_otg count: on the Raspberry Pi 3B+ every USB port and the Ethernet share one
dwc_otg controller, and a bus that's degrading since the last reboot shows up as
`dwc_otg_hcd_urb_dequeue ... Timed out` warnings well before it fails a run. "+N this run" is the
number to watch -- a quiet run adds ~0, a flash that upsets the bus adds a burst. Uptime says how long
the count has been accumulating. A burst of at least [rig] health_dwc_burst (the tester's value,
recorded in the sample; else this checkout's hil_config.toml), a non-zero `vcgencmd get_throttled`
or an undervoltage alarm at the end renders as a warning, and so does a count that went down (the
host rebooted, or the kernel log wrapped).

Off a Pi (mimir, an x86 PC, is the reference rig since 2026-11-11) there's no dwc_otg controller and no
vcgencmd/rpi_volt: sysinfo samples those as None and their rows are left out. What matters there is the
Bluetooth controller: `hciN: hardware error` lines in this boot's kernel log (the journal, else dmesg), one per wedge (the controller stops
taking commands and the kernel resets it, dropping every BLE link). Any new one during a run is a warning.
"""

import datetime
import json
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

REPO = Path(__file__).resolve().parents[2]


def _load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _burst():
    try:
        cfg = tomllib.loads((REPO / "hil_config.toml").read_text())
        return int(cfg.get("rig", {}).get("health_dwc_burst", 12))
    except Exception:
        return 12


def _duration(s):
    if s is None:
        return "?"
    d, rem = divmod(int(s), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def _boot_time(sample):
    try:
        at = datetime.datetime.fromisoformat(sample["sampled_at"])
        return at - datetime.timedelta(seconds=sample["uptime_s"])
    except (KeyError, TypeError, ValueError):
        return None


def _booted(sample):
    t = _boot_time(sample)
    return t.strftime("%Y-%m-%d %H:%M UTC") if t else None


def _rebooted(start, end):
    """Did the host boot between the samples? The kernel's boot_id says for sure; samples without
    it compare boot times (sampled_at - uptime), with slack for rounding and clock steps."""
    if not (start and end):
        return False
    if start.get("boot_id") and end.get("boot_id"):
        return start["boot_id"] != end["boot_id"]
    t_s, t_e = _boot_time(start), _boot_time(end)
    return bool(t_s and t_e) and abs((t_e - t_s).total_seconds()) > 120


def _fmt(v, unit=""):
    if v is None:
        return "?"
    if isinstance(v, bool):
        return "**yes**" if v else "no"
    if isinstance(v, list):
        return " / ".join(f"{x:.2f}" for x in v)
    return f"{v}{unit}"


def _delta(n, reset):
    if reset:
        return f" ({reset}: no delta)"
    if n is None:
        return ""
    return f" (**+{n}** this run)" if n else " (+0 this run)"


def render(start, end):
    first, when = (start, "start") if start else (end, "end")
    lines = ["### Rig health", ""]
    booted = _booted(first)
    lines += [
        f"Up {_duration(first.get('uptime_s'))}"
        + (f" (booted {booted})" if booted else "")
        + f" at the {when} of the run.",
        "",
        "| | start | end |",
        "|---|---|---|",
    ]

    def row(label, key, unit="", end_extra=""):
        # a field neither sample has (None = not this platform: no dwc_otg/vcgencmd off a Pi,
        # or an older sample from before the field existed) gets no row at all
        if all((x or {}).get(key) is None for x in (start, end)):
            return
        s = _fmt((start or {}).get(key), unit) if start else "—"
        e = _fmt((end or {}).get(key), unit) if end else "—"
        lines.append(f"| {label} | {s} | {e}{end_extra} |")

    lines.append(
        f"| uptime | {_duration(start.get('uptime_s')) if start else '—'} "
        f"| {_duration(end.get('uptime_s')) if end else '—'} |"
    )
    # Both counters are kernel-log lines since boot: they go down if the host rebooted between the
    # samples (_rebooted) or the kernel ring buffer wrapped. Either way there's no usable delta.
    rebooted = _rebooted(start, end)

    def counter(key):
        a, b = (start or {}).get(key), (end or {}).get(key)
        delta = b - a if isinstance(a, int) and isinstance(b, int) else None
        reset = (
            "host rebooted"
            if rebooted
            else "kernel log wrapped"
            if delta is not None and delta < 0
            else None
        )
        return (None if reset else delta), reset

    delta, reset = counter("dwc_otg_warnings")
    row("dwc_otg timeouts since boot", "dwc_otg_warnings", end_extra=_delta(delta, reset))
    bt_delta, bt_reset = counter("bt_hw_errors")
    row(
        "BT controller hardware errors since boot",
        "bt_hw_errors",
        end_extra=_delta(bt_delta, bt_reset),
    )
    row("CPU temp", "cpu_temp_c", " °C")
    row("load (1/5/15 min)", "loadavg")
    row("throttled", "throttled")
    row("undervoltage now", "undervoltage_now")

    warnings = []
    if rebooted:
        warnings.append(
            "the tester host rebooted during the run -- a crash or power loss,"
            " not a test failure; check its journal"
        )
    elif reset or bt_reset:
        warnings.append(
            "a kernel-log count went down (the log wrapped): a very noisy host, this run's delta is unknown"
        )
    # the tester's own threshold (its hil_config.local.toml applies there, not here); older samples lack it
    burst = next(
        (x["dwc_otg_burst"] for x in (end, start) if x and isinstance(x.get("dwc_otg_burst"), int)),
        None,
    )
    if burst is None:  # not `or`: a configured 0 is a threshold too
        burst = _burst()
    if delta is not None and delta >= burst:
        warnings.append(
            f"{delta} new dwc_otg timeouts during the run (>= {burst}): the USB bus is struggling"
        )
    if bt_delta:
        warnings.append(
            f"{bt_delta} Bluetooth controller hardware error(s) during the run: the adapter wedged and"
            " dropped every BLE link -- failures around then are likely the tester's adapter, not the"
            " firmware (`journalctl -k --case-sensitive=false -g 'hci[0-9]+: hardware error'` on the tester for the times)"
        )
    if end and end.get("throttled") not in (None, "0x0"):
        warnings.append(
            f"throttled={end['throttled']} (vcgencmd get_throttled: under-voltage/throttling since boot)"
        )
    if end and end.get("undervoltage_now"):
        warnings.append("undervoltage alarm at the end of the run")
    if warnings:
        lines += ["", "> [!WARNING]"] + [f"> {w}  " for w in warnings]
    lines.append("")
    return "\n".join(lines)


def main(argv):
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    results = Path(argv[0])
    start = _load(results / "rig-health-start.json")
    end = _load(results / "rig-health-end.json")
    if start or end:
        print(render(start, end))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
