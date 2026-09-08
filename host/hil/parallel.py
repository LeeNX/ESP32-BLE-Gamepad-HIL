"""Spike: drive + capture several gamepads at once.

Building toward a parallel *functional* run — one worker thread per wired board,
each running that board's checks against its own SerialDev + evdev node. The
timed benchmark (test_latency.py) stays sequential; only the
timing-insensitive functional checks parallelise.

    PYTHONPATH=host python3 -m hil.parallel                        # esp32dev once
    PYTHONPATH=host python3 -m hil.parallel --boards "esp32dev esp32c3" --parallel
    PYTHONPATH=host python3 -m hil.parallel --boards "esp32dev esp32c3 esp32s3" \
        --parallel --loop 20                                       # soak / monitor

Spike result (rp3b-ble-hil, 3 boards on one BLE adapter, 2026-09-08): a digital
button press+release is clean on every board with all three driven + captured at
once, no flakes. 3 boards: 9.2 s sequential -> 3.6 s parallel. Concurrent evdev
capture and independent serial channels are fine; the bench_parallel trouble was
the block/unblock isolation, not concurrency itself.

Uses evdev (`/dev/input/event*`) via find_gamepad() -- not the legacy joystick
`/dev/input/js*` nodes (nothing in the suite touches those).
"""

import argparse
import concurrent.futures as cf
import pathlib
import sys
import time
from collections import Counter

from evdev import ecodes

from .config import load
from .evdev_utils import HAT_ABS_CODES, Capture, find_gamepad
from .serialdev import SerialDev

GOLDEN_DIR = pathlib.Path(__file__).resolve().parents[2] / "firmware" / "golden"
_ALL_HAT_CODES = set(range(ecodes.ABS_HAT0X, ecodes.ABS_HAT3Y + 1))


def _connect(board, cfg):
    """Open the hil_runner serial channel and confirm the BLE link is up.
    Assumes the bond already exists (normal rig state) — no pairing here."""
    b = cfg["board"][board]
    name = f"{cfg['rig']['device_name']} {board}"
    dut = SerialDev(b["port"])
    dut.wait_ready()
    dut.begin()
    for _ in range(20):
        if dut.connected():
            break
        time.sleep(0.5)
    else:
        dut.close()
        raise RuntimeError("DUT never reported CONN 1")
    return dut, Capture(find_gamepad(name))


# --- individual checks: (dut, cap, dcfg) -> (ok, detail) -------------------
def check_buttons(dut, cap, dcfg):
    n = min(dcfg["buttons"], 32)  # strict block; keeps a soak iteration short
    bad = []
    for i in range(1, n + 1):
        cap.drain()
        dut.press(i)
        downs = [c for c, v in cap.key_changes(cap.collect()).items() if v == 1]
        cap.drain()
        dut.release(i)
        ups = [c for c, v in cap.key_changes(cap.collect()).items() if v == 0]
        if len(downs) != 1 or ups != downs:
            bad.append(i)
    return not bad, f"{n - len(bad)}/{n} clean" + (f", bad {bad}" if bad else "")


def check_axis(dut, cap, dcfg):
    axes = dcfg.get("axes") or []
    if not axes:
        return True, "no axes (skip)"
    tok = axes[0]
    lo, hi = dcfg["axesMin"], dcfg["axesMax"]
    dut.axis(tok, lo)
    cap.collect(settle=0.15)
    cap.drain()
    dut.axis(tok, lo + (hi - lo) * 3 // 4)
    moved = {c for c in cap.abs_changes(cap.collect()) if c not in HAT_ABS_CODES}
    dut.axis(tok, lo)
    return len(moved) == 1, f"{tok} -> {sorted(moved) or 'nothing'}"


def check_hat(dut, cap, dcfg):
    nh = dcfg.get("hats") or 0
    if not nh:
        return True, "no hats (skip)"
    dut.hat(nh, 0)  # firmware emits hats reversed; the last index is the live one
    cap.collect(settle=0.15)
    cap.drain()
    dut.hat(nh, 5)  # DPAD down
    moved = {c for c in cap.abs_changes(cap.collect()) if c in _ALL_HAT_CODES}
    dut.hat(nh, 0)
    ok = bool(moved) and moved <= {ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y}
    return ok, f"HAT {nh} down -> {sorted(moved) or 'nothing'}"


def check_descriptor(dut, cap, dcfg):
    golden = GOLDEN_DIR / f"{dcfg['profile']}.hiddesc"
    if not golden.exists():
        return True, f"no golden for {dcfg['profile']!r} (skip)"
    fw = dut.report_descriptor()
    want = bytes.fromhex(golden.read_text().strip())
    return fw == want, f"{len(fw)}B {'== golden' if fw == want else '!= golden'}"


CHECKS = [
    ("buttons", check_buttons),
    ("axis", check_axis),
    ("hat", check_hat),
    ("descriptor", check_descriptor),
]


def check_board(board, cfg):
    """Run every check against one board. Returns [(check, ok, detail), ...]."""
    dut, cap = _connect(board, cfg)
    dcfg = dut.config()
    try:
        out = []
        for name, fn in CHECKS:
            try:
                ok, detail = fn(dut, cap, dcfg)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, repr(e)
            out.append((name, ok, detail))
        return out
    finally:
        cap.dev.close()
        dut.close()


def _run_board(board, cfg):
    t = time.perf_counter()
    try:
        checks = check_board(board, cfg)
    except Exception as e:  # noqa: BLE001
        checks = [("connect", False, repr(e))]
    return board, checks, time.perf_counter() - t


def _iteration(boards, cfg, parallel):
    if parallel and len(boards) > 1:
        with cf.ThreadPoolExecutor(max_workers=len(boards)) as ex:
            return list(ex.map(lambda b: _run_board(b, cfg), boards))
    return [_run_board(b, cfg) for b in boards]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--boards", default="esp32dev", help="space-separated")
    ap.add_argument("--parallel", action="store_true", help="one thread per board")
    ap.add_argument("--loop", type=int, default=1, help="run N iterations (soak/monitor)")
    args = ap.parse_args(argv)
    cfg = load()
    boards = args.boards.split()
    print(f"boards: {', '.join(boards)}  ({'parallel' if args.parallel else 'sequential'})")

    tally = Counter()  # (board, check) -> failures
    runs = Counter()  # (board, check) -> attempts
    worst_rc = 0
    for it in range(1, args.loop + 1):
        t0 = time.perf_counter()
        results = _iteration(boards, cfg, args.parallel)
        span = time.perf_counter() - t0
        it_bad = 0
        for board, checks, dt in results:
            for name, ok, detail in checks:
                runs[(board, name)] += 1
                if not ok:
                    tally[(board, name)] += 1
                    it_bad += 1
                    worst_rc = 1
                    print(f"  iter {it} [{board}] FAIL {name}: {detail}")
        stamp = f"iter {it}/{args.loop}" if args.loop > 1 else "done"
        print(f"{stamp}  {span:.1f}s  " + ("all clean" if not it_bad else f"{it_bad} FAIL"))

    if args.loop > 1:
        print("\n--- soak summary ---")
        for key in sorted(runs):
            b, n = key
            fails = tally[key]
            mark = "OK  " if not fails else f"FAIL {fails}/{runs[key]}"
            print(f"  {b:9} {n:11} {mark}")
    return worst_rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
