"""Spike: drive + capture several gamepads at once.

Building toward a parallel *functional* run — one worker thread per wired board,
each running that board's checks against its own SerialDev + evdev node. The
timed benchmark (test_latency.py) stays sequential; only the
timing-insensitive functional checks parallelise.

Start small: one board, one digital button.

    PYTHONPATH=host python3 -m hil.parallel                       # esp32dev, solo
    PYTHONPATH=host python3 -m hil.parallel --boards "esp32dev esp32c3"
    PYTHONPATH=host python3 -m hil.parallel --boards "esp32dev esp32c3" --parallel
"""

import argparse
import concurrent.futures as cf
import sys
import time

from .config import load
from .evdev_utils import Capture, find_gamepad
from .serialdev import SerialDev


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
        raise RuntimeError(f"{board}: DUT never reported CONN 1")
    dev = find_gamepad(name)
    return dut, Capture(dev)


def button_check(board, cfg, btn=1):
    """press+release <btn>, confirm exactly one evdev key down then that key up."""
    dut, cap = _connect(board, cfg)
    dev = cap.dev
    try:
        cap.drain()
        dut.press(btn)
        downs = [c for c, v in cap.key_changes(cap.collect()).items() if v == 1]
        if len(downs) != 1:
            return False, f"press -> {len(downs)} key-down events {downs}"
        code = downs[0]

        cap.drain()
        dut.release(btn)
        ups = [c for c, v in cap.key_changes(cap.collect()).items() if v == 0]
        if ups != [code]:
            return False, f"release -> {ups}, expected [{code}]"
        return True, f"button {btn} -> key {code}, down+up clean"
    finally:
        dev.close()
        dut.close()


def _run_one(board, cfg):
    t = time.perf_counter()
    try:
        ok, detail = button_check(board, cfg)
    except Exception as e:  # noqa: BLE001
        ok, detail = False, repr(e)
    return board, ok, detail, time.perf_counter() - t


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--boards", default="esp32dev", help="space-separated")
    ap.add_argument("--parallel", action="store_true", help="one thread per board")
    args = ap.parse_args(argv)
    cfg = load()
    boards = args.boards.split()

    print(f"boards: {', '.join(boards)}  ({'parallel' if args.parallel else 'sequential'})")
    if args.parallel and len(boards) > 1:
        with cf.ThreadPoolExecutor(max_workers=len(boards)) as ex:
            results = list(ex.map(lambda b: _run_one(b, cfg), boards))
    else:
        results = [_run_one(b, cfg) for b in boards]

    rc = 0
    for board, ok, detail, dt in results:
        print(f"[{board}] {'OK  ' if ok else 'FAIL'} {detail}  ({dt:.1f}s)")
        rc |= 0 if ok else 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
