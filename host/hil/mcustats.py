"""MCU health over a run: on-die temperature, CPU and Bluetooth load, heap, stack, reset reason.

The firmware side is hil_runner's `STATS?` and `TASKS?` (firmware/src/hil_runner.cpp) and the Bluepad32
observer's `stats?` (firmware/observer/main/observer.c). conftest.py samples `STATS?` at fixed points in a
pytest session (board opened, paired, before every test, end) into results/mcu-<board>-<profile>.csv, and
test_mcu_thermal.py adds a quiet / busy / cool-down ramp. Readings are for trends, not absolutes: the
classic ESP32's sensor is uncalibrated and moves in 0.56 C steps; the C3/S3 read within ~2 C.

    python3 host/hil/mcustats.py results/      # Markdown for the job summary (nothing if no CSVs)
"""

import csv
import sys
import time
from pathlib import Path

# Columns of results/mcu-*.csv, in order. Values the firmware reports as "na" are left empty.
FIELDS = (
    "wall",  # host time.time() when sampled
    "board",
    "profile",
    "phase",  # boot | test | thermal-start | thermal-quiet | thermal-busy | thermal-cool | end | bp32-start | bp32-end
    "test",  # pytest node name for phase=test, else ""
    "up_ms",
    "reset",
    "conn",
    "temp_c",
    "cpu0",
    "cpu1",
    "bt",
    "heap",
    "heap_min",
    "heap_big",
    "stack_loop",
    "stack_ble",
    "reports",
    "took_us",
)

_INT_KEYS = {
    "up",
    "conn",
    "conns",
    "states",
    "heap",
    "heap_min",
    "heap_big",
    "stack_loop",
    "stack_ble",
    "reports",
    "took_us",
}
_FLOAT_KEYS = {"temp", "cpu0", "cpu1", "bt"}

# STATS? reset= values (hil_runner.cpp resetReasonName()).
RESET_REASONS = frozenset(
    "poweron ext sw panic int_wdt task_wdt wdt deepsleep brownout sdio usb jtag efuse pwr_glitch cpu_lockup unknown".split()
)


def _num(key, val):
    if val in ("na", ""):
        return None
    try:
        if key in _INT_KEYS:
            return int(val)
        if key in _FLOAT_KEYS:
            return float(val)
    except ValueError:
        return None
    return val


def parse_stats(line):
    """`STATS up=... temp=... cpu0=...` -> dict; "na" -> None, unknown keys kept as strings.
    Returns {} for anything that isn't a STATS line."""
    parts = line.split()
    if parts[:2] == ["HIL", "stats"]:  # the Bluepad32 observer's spelling
        parts = parts[1:]
    if not parts or parts[0].upper() != "STATS":
        return {}
    out = {}
    for tok in parts[1:]:
        key, sep, val = tok.partition("=")
        if sep:
            out[key] = _num(key, val)
    return out


def parse_tasks(line):
    """`TASKS span_ms=<n> <name>:<cpu%>:<min free stack> ...` -> (span_ms, [(name, cpu, stack), ...]).
    Returns (None, []) for anything that isn't a TASKS line."""
    parts = line.split()
    if parts[:2] == ["HIL", "tasks"]:
        parts = parts[1:]
    if not parts or parts[0].upper() != "TASKS":
        return None, []
    span = None
    tasks = []
    for tok in parts[1:]:
        if tok.startswith("span_ms="):
            span = _num("up", tok.split("=", 1)[1])
            continue
        name, _, rest = tok.partition(":")
        cpu, _, stack = rest.partition(":")
        try:
            tasks.append((name, float(cpu), int(stack)))
        except ValueError:
            continue
    return span, tasks


def row_from_stats(stats, board, profile, phase, test=""):
    return {
        "wall": f"{time.time():.3f}",
        "board": board,
        "profile": profile,
        "phase": phase,
        "test": test,
        "up_ms": stats.get("up"),
        "reset": stats.get("reset"),
        # The observer reports conns= (devices connected) and states= (state lines printed) where hil_runner has
        # conn= and reports=: same columns.
        "conn": stats.get("conn", stats.get("conns")),
        "temp_c": stats.get("temp"),
        "cpu0": stats.get("cpu0"),
        "cpu1": stats.get("cpu1"),
        "bt": stats.get("bt"),
        "heap": stats.get("heap"),
        "heap_min": stats.get("heap_min"),
        "heap_big": stats.get("heap_big"),
        "stack_loop": stats.get("stack_loop"),
        "stack_ble": stats.get("stack_ble"),
        "reports": stats.get("reports", stats.get("states")),
        "took_us": stats.get("took_us"),
    }


class McuLog:
    """Appends STATS? samples for one board+profile to a CSV. Every method swallows errors: sampling is
    diagnostics and must never fail or slow down a test that's otherwise fine."""

    def __init__(self, path, board, profile):
        self.path = Path(path)
        self.board = board
        self.profile = profile
        self.enabled = True

    def sample(self, dut, phase, test=""):
        """One `STATS?` -> one CSV row. Returns the parsed dict ({} if nothing was recorded)."""
        if not self.enabled:
            return {}
        try:
            stats = parse_stats(dut.command("STATS?", timeout=2.0, retries=0))
        except Exception:  # noqa: BLE001 -- a dropped reply is a missing sample, nothing more
            self._resync(dut)
            if phase in SESSION_START:
                # Keep the session boundary even without numbers, or the summary would compare this session's
                # uptimes with the previous one's and report a restart that was only the port reopening.
                self.write(row_from_stats({}, self.board, self.profile, phase, test))
            return {}
        if not stats:
            # ERR unknown: firmware from before STATS? existed. Stop asking for the rest of the session.
            self.enabled = False
            return {}
        self.write(row_from_stats(stats, self.board, self.profile, phase, test))
        return stats

    @staticmethod
    def _resync(dut):
        """After a query that timed out, its reply may still arrive -- and SerialDev's next command would take that
        late STATS/TASKS line as its own reply. A PING that only accepts PONG reads past it (replies come in order).
        Best effort: failing here is the next command's problem, not this sample's."""
        try:
            dut.command("PING", timeout=2.0, prefixes=("PONG",), retries=0)
        except Exception:  # noqa: BLE001
            pass

    def write(self, row):
        try:
            new = not self.path.exists()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in FIELDS})
        except OSError:
            pass

    def tasks(self, dut):
        """`TASKS?` -> appended to the sibling .tasks.txt (one line per call, prefixed with the time)."""
        if not self.enabled:
            return
        try:
            line = dut.command("TASKS?", timeout=2.0, retries=0)
        except Exception:  # noqa: BLE001
            self._resync(dut)
            return
        if not line.startswith("TASKS"):
            return
        try:
            with self.path.with_suffix(".tasks.txt").open("a") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")
        except OSError:
            pass


# --- job summary ---------------------------------------------------------------------------------------


# Phases whose CPU window starts after boot (a session's first sample covers power-up and BLE start-up).
NOT_BOOT = {
    "test",
    "thermal-start",
    "thermal-quiet",
    "thermal-busy",
    "thermal-cool",
    "end",
    "bp32-end",
}
SESSION_START = {"boot", "bp32-start"}


def _floats(rows, key, phases=None):
    out = []
    for r in rows:
        if phases and r["phase"] not in phases:
            continue
        try:
            out.append(float(r[key]))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def _ints(rows, key):
    return [int(v) for v in _floats(rows, key)]


def _f(v, fmt="{:.1f}"):
    return "–" if v is None else fmt.format(v)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def load(results):
    """All rows of results/mcu-*.csv, grouped by (board, profile), in file order."""
    groups = {}
    for path in sorted(Path(results).glob("mcu-*.csv")):
        try:
            with path.open(newline="") as f:
                for r in csv.DictReader(f):
                    r["_file"] = path.name
                    groups.setdefault((r.get("board", "?"), r.get("profile", "?")), []).append(r)
        except OSError:
            continue
    return groups


def summarize_group(rows):
    """The numbers for one board+profile's row of the summary table."""
    temps = _floats(rows, "temp_c")
    first_temp = temps[0] if temps else None
    quiet = _floats(rows, "temp_c", {"thermal-quiet"})
    busy = _floats(rows, "temp_c", {"thermal-busy"})
    heap_min = _ints(rows, "heap_min")
    resets = sorted({r["reset"] for r in rows if r.get("reset")})
    # up_ms going backwards inside one session means the board restarted mid-run. A new session (a retry, a second
    # run appending to the same file, another file such as a Bluepad32 lane's) starts with a SESSION_START row or a
    # new file, and opening the port may reset the board then, so only compare within a session.
    restarts = 0
    prev = None
    prev_file = None
    for r in rows:
        # Boundary first: a session-start row with no numbers (its sample failed) still starts a session.
        if r.get("phase") in SESSION_START or r.get("_file") != prev_file:
            prev = None
        prev_file = r.get("_file")
        try:
            up = int(r["up_ms"])
        except (KeyError, ValueError, TypeError):
            continue
        if prev is not None and up < prev:
            restarts += 1
        prev = up
    return {
        "samples": len(rows),
        "temp_first": first_temp,
        "temp_max": max(temps) if temps else None,
        "temp_last": temps[-1] if temps else None,
        "quiet": _mean(quiet),
        "busy": _mean(busy),
        # The boot sample's window runs from power-up, BLE stack start-up included: leave it out of the peak.
        "bt_max": max(_floats(rows, "bt", NOT_BOOT), default=None),
        "heap_min": min(heap_min) if heap_min else None,
        "stack_ble_min": min(_ints(rows, "stack_ble"), default=None),
        "resets": ",".join(resets) or "–",
        "restarts": restarts,
    }


def render(groups):
    if not groups:
        return ""
    stats = {key: summarize_group(rows) for key, rows in sorted(groups.items())}
    # Quiet/busy only exist when test_mcu_thermal ran (`--bench`). Without it the column would be all dashes, which
    # reads like missing data, so it's left out and the note says how to get it.
    thermal = any(s["quiet"] is not None or s["busy"] is not None for s in stats.values())
    out = [
        "### MCU health",
        "",
        "On-die temperature and load per board, sampled by `STATS?` through each pytest session"
        " (`results/mcu-*.csv`). Trends only: the classic esp32 sensor is uncalibrated, in 0.56 °C steps.",
        "Quiet/busy are `test_mcu_thermal` phases (connected and idle vs. sending reports flat out; `--bench` only)."
        if thermal
        else "No quiet/busy thermal phases in this run: `test_mcu_thermal` runs with `--bench` only.",
        "`bt` = the Bluetooth tasks' peak share of one core (after boot). Per-core CPU is in the CSV, but the test"
        " firmware's loop polls serial without sleeping, so its core always reads ~100%.",
        "",
        "| board | profile | °C first → max → last |"
        + (" quiet / busy °C |" if thermal else "")
        + " peak bt % | min free heap | min BLE stack | reset |",
        "|---|---|---|" + ("---|" if thermal else "") + "---|---|---|---|",
    ]
    warn = []
    for (board, profile), s in stats.items():
        reset = s["resets"]
        if s["restarts"]:
            reset += f" ⚠️ {s['restarts']} restart(s) mid-run"
            warn.append(
                f"{board}/{profile} restarted {s['restarts']}× mid-run (reset reason: {s['resets']})"
            )
        elif any(r in s["resets"] for r in ("brownout", "panic", "wdt")):
            warn.append(f"{board}/{profile} last reset reason: {s['resets']}")
        out.append(
            f"| {board} | {profile} | {_f(s['temp_first'])} → {_f(s['temp_max'])} → {_f(s['temp_last'])}"
            + (f" | {_f(s['quiet'])} / {_f(s['busy'])}" if thermal else "")
            + f" | {_f(s['bt_max'])}"
            f" | {_f(s['heap_min'], '{:,}')} | {_f(s['stack_ble_min'], '{:,}')} | {reset} |"
        )
    if warn:
        out += [""] + [f"> ⚠️ {w}" for w in warn]
    return "\n".join(out) + "\n"


def main(argv):
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.write(render(load(argv[0])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
