"""Rig health for the job summary: the tester host's state at the start and end of a run.

    python3 host/hil/healthsummary.py results/          # -> stdout (Markdown)

Reads results/rig-health-start.json and results/rig-health-end.json (`hil.sysinfo --dynamic-only`
samples that hil.yml takes under the rig lock, around the suite + matrix). Prints nothing if neither
exists (a rig commit from before these samples).

Mostly here for the dwc_otg count: on the Raspberry Pi 3B+ every USB port and the Ethernet share one
dwc_otg controller, and a bus that's degrading since the last reboot shows up as
`dwc_otg_hcd_urb_dequeue ... Timed out` warnings well before it fails a run. "+N this run" is the
number to watch -- a quiet run adds ~0, a flash that upsets the bus adds a burst. Uptime says how long
the count has been accumulating. A burst of at least [rig] health_dwc_burst, a non-zero
`vcgencmd get_throttled` or an undervoltage alarm at the end renders as a warning.
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


def _booted(sample):
    try:
        at = datetime.datetime.fromisoformat(sample["sampled_at"])
        return (at - datetime.timedelta(seconds=sample["uptime_s"])).strftime("%Y-%m-%d %H:%M UTC")
    except (KeyError, TypeError, ValueError):
        return None


def _fmt(v, unit=""):
    if v is None:
        return "?"
    if isinstance(v, bool):
        return "**yes**" if v else "no"
    if isinstance(v, list):
        return " / ".join(f"{x:.2f}" for x in v)
    return f"{v}{unit}"


def _delta(n):
    if n is None:
        return ""
    return f" (**+{n}** this run)" if n else " (+0 this run)"


def render(start, end):
    first = start or end
    lines = ["### Rig health", ""]
    booted = _booted(first)
    lines += [
        f"Up {_duration(first.get('uptime_s'))}"
        + (f" (booted {booted})" if booted else "")
        + " at the start.",
        "",
        "| | start | end |",
        "|---|---|---|",
    ]

    def row(label, key, unit="", end_extra=""):
        s = _fmt((start or {}).get(key), unit) if start else "—"
        e = _fmt((end or {}).get(key), unit) if end else "—"
        lines.append(f"| {label} | {s} | {e}{end_extra} |")

    lines.append(
        f"| uptime | {_duration(start.get('uptime_s')) if start else '—'} "
        f"| {_duration(end.get('uptime_s')) if end else '—'} |"
    )
    dwc_s = (start or {}).get("dwc_otg_warnings")
    dwc_e = (end or {}).get("dwc_otg_warnings")
    delta = dwc_e - dwc_s if isinstance(dwc_s, int) and isinstance(dwc_e, int) else None
    row("dwc_otg timeouts since boot", "dwc_otg_warnings", end_extra=_delta(delta))
    row("CPU temp", "cpu_temp_c", " °C")
    row("load (1/5/15 min)", "loadavg")
    row("throttled", "throttled")
    row("undervoltage now", "undervoltage_now")

    warnings = []
    burst = _burst()
    if delta is not None and delta >= burst:
        warnings.append(
            f"{delta} new dwc_otg timeouts during the run (>= {burst}): the USB bus is struggling"
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
