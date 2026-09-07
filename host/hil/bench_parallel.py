"""Multi-gamepad BLE contention experiment: does the bench get worse when
several ESP32 gamepads are bonded to one host adapter and hammered at once?

Runs the short bench sweep (hil.bench.run_sweep, quick=True) against every wired
board twice:

  * solo   -- this board the only one connected      (peers_active = 1)
  * N-up   -- all N boards connected and swept in parallel threads, started
              together on a barrier so the airtime actually overlaps
              (peers_active = N)

then prints a solo-vs-N-up table and drops the raw records + a comparison under
results/parallel/<stamp>/. It is deliberately *not* a pytest test -- the suite's
fixtures are session-scoped and single-DUT; this owns N SerialDevs + N evdev
captures itself.

    PYTHONPATH=host python3 -m hil.bench_parallel                 # detected boards
    PYTHONPATH=host python3 -m hil.bench_parallel --boards "esp32dev esp32c3"
    PYTHONPATH=host python3 -m hil.bench_parallel --no-flash      # reuse what's on the boards
    PYTHONPATH=host python3 -m hil.bench_parallel --keep-peers-connected
    PYTHONPATH=host python3 -m hil.bench_parallel --full          # full sweep, not the short one

Firmware needs nothing special -- the MCU-side BURST command already generates
its own traffic, so one hil_runner bundle per board covers both passes and
nothing is re-flashed between them.
"""

import argparse
import concurrent.futures as cf
import dataclasses
import datetime as dt
import json
import pathlib
import subprocess
import sys
import threading
import time

from hil import bench, bluetooth, detect
from hil.config import load as load_config
from hil.evdev_utils import Capture, find_all_nodes, find_gamepad
from hil.serialdev import SerialDev

REPO = pathlib.Path(__file__).resolve().parents[2]
STATE = pathlib.Path.home() / ".cache" / "esp32-hil" / "state.json"


@dataclasses.dataclass
class Board:
    name: str
    port: str
    flash_port: str
    profile: str
    device_name: str
    bundle: pathlib.Path | None = None
    mac: str | None = None
    dut: SerialDev | None = None
    cap: Capture | None = None


# --- setup -----------------------------------------------------------------
def resolve_boards(cfg, want, bundles_dir):
    present, absent = detect.scan()
    names = want or present
    dev_prefix = cfg["rig"]["device_name"]
    boards = []
    for name in names:
        if name not in present:
            print(f"  skip {name}: {absent.get(name, 'not a configured board')}")
            continue
        b = cfg["board"][name]
        port = b["port"]
        boards.append(
            Board(
                name=name,
                port=port,
                flash_port=b.get("flash_port") or port,
                profile=b.get("profile", "default"),
                device_name=f"{dev_prefix} {name}",
                bundle=_find_bundle(bundles_dir, name, b.get("profile", "default")),
            )
        )
    return boards


def _find_bundle(bundles_dir, board, profile):
    bundles_dir = pathlib.Path(bundles_dir).expanduser()
    hits = []
    for man in bundles_dir.glob("*/manifest.json"):
        try:
            m = json.loads(man.read_text())
        except Exception:
            continue
        if m.get("board") == board and m.get("profile") == profile:
            hits.append(man.parent)
    # newest by mtime -- build.sh stamps a fresh dir each build
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def flash(board):
    if board.bundle is None:
        sys.exit(f"no bundle for {board.name}/{board.profile} -- build one or pass --no-flash")
    cmd = [
        sys.executable,
        str(REPO / "tester" / "flash.py"),
        str(board.bundle),
        "--port",
        board.flash_port,
    ]
    print(f"  flash {board.name}: {board.bundle.name}")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit(f"flashing {board.name} failed")
    time.sleep(2)


def open_dut(board):
    d = SerialDev(board.port)
    d.wait_ready()
    cfg = d.config()
    if cfg.get("profile") != board.profile:
        d.close()
        sys.exit(
            f"{board.name}: firmware profile {cfg.get('profile')!r} != "
            f"expected {board.profile!r} (flash the right bundle, or fix hil_config.toml)"
        )
    d.begin()
    time.sleep(1.0)
    if not d.ping():
        d.close()
        sys.exit(f"{board.name}: firmware stopped answering after BEGIN")
    board.dut = d


def bond(board, btctl):
    state = _load_state()
    prev = state.get(board.device_name, {})
    want_fresh = bool(prev) and prev.get("profile") != board.profile
    mac = bluetooth.ensure_paired(
        btctl, board.device_name, known_mac=prev.get("mac"), want_fresh=want_fresh
    )
    board.dut.wait_connected()
    state[board.device_name] = {"mac": mac, "profile": board.profile}
    _save_state(state)
    board.mac = mac
    print(f"  bond {board.name} -> {mac}")


def _load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


# --- link juggling -------------------------------------------------------
def set_connected(board, btctl, want, *, timeout=25):
    """Bring one board's BLE link up or down and wait for it to settle. The
    bonds are trusted, so BlueZ auto-reconnects a bare `disconnect` within
    a second -- `block`/`unblock` is what actually pins a peer offline."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        up = bluetooth.is_connected(board.mac)
        if up == want:
            return
        if want:
            btctl.unblock(board.mac)
            btctl.send(f"connect {board.mac}")
        else:
            btctl.block(board.mac)
        time.sleep(3)
    raise RuntimeError(f"{board.name}: link would not go {'up' if want else 'down'}")


def acquire_capture(board):
    """(Re)open the gamepad evdev node -- it vanishes across a BLE drop."""
    if board.cap is not None and board.cap.dev is not None:
        try:
            board.cap.dev.close()
        except Exception:
            pass
    dev = find_gamepad(board.device_name, timeout=20)
    board.cap = Capture(dev)


# --- the two passes -----------------------------------------------------
def sweep_one(board, *, quick, peers_active, peer_boards):
    board.dut.reset()
    board.cap.drain()
    cfg = board.dut.config()
    return bench.run_sweep(
        board.dut,
        board.cap,
        cfg,
        board=board.name,
        profile=board.profile,
        quick=quick,
        peers_active=peers_active,
        peer_boards=peer_boards,
    )


def baseline_pass(boards, btctl, *, quick, isolate):
    """Each board swept alone. isolate=True drops the other links first so it
    is a true solo (connection count 1); otherwise the peers stay connected but
    idle and only the traffic differs."""
    out = {}
    for b in boards:
        others = [o for o in boards if o is not b]
        if isolate:
            for o in others:
                set_connected(o, btctl, False)
        set_connected(b, btctl, True)
        b.dut.wait_connected()
        acquire_capture(b)
        print(f"  [solo] {b.name} ...")
        out[b.name] = sweep_one(b, quick=quick, peers_active=1, peer_boards=[])
    if isolate:
        for o in boards:
            set_connected(o, btctl, True)
            o.dut.wait_connected()
    return out


def contention_pass(boards, *, quick):
    """All boards connected; every board swept at the same time in its own
    thread, released together on a barrier so the airtime genuinely overlaps."""
    for b in boards:
        acquire_capture(b)
    names = [b.name for b in boards]
    # timeout so one worker dying before the rendezvous can't deadlock the rest
    barrier = threading.Barrier(len(boards), timeout=180)

    def work(b):
        peer_boards = [n for n in names if n != b.name]
        barrier.wait()
        return b.name, sweep_one(b, quick=quick, peers_active=len(boards), peer_boards=peer_boards)

    print(f"  [{len(boards)}-up] {' '.join(names)} in parallel ...")
    out = {}
    with cf.ThreadPoolExecutor(max_workers=len(boards)) as ex:
        for name, result in ex.map(work, boards):
            out[name] = result
    return out


# --- reporting ---------------------------------------------------------
def _btn(result):
    return result["latency_ms"]["button"]["e2e"]


def _worst_burst(result):
    return min((x["delivered_frac"] for x in result["burst"]), default=None)


def comparison_md(solo, nup, n):
    rows = [
        "| board | p50 solo → N | p90 solo → N | clean Hz solo → N | worst burst frac solo → N |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(nup):
        s, u = solo.get(name), nup[name]
        sb, ub = _btn(s) if s else {}, _btn(u)
        rows.append(
            f"| {name} "
            f"| {sb.get('p50', '-')} → {ub.get('p50', '-')} "
            f"| {sb.get('p90', '-')} → {ub.get('p90', '-')} "
            f"| {(s or {}).get('clean_rate_hz', '-')} → {u.get('clean_rate_hz', '-')} "
            f"| {_worst_burst(s) if s else '-'} → {_worst_burst(u)} |"
        )
    head = f"# BLE contention: solo vs {n}-up\n\nShort sweep. p50/p90 = button e2e latency (ms), lower is better.\n\n"
    return head + "\n".join(rows) + "\n"


# --- driver ------------------------------------------------------------
def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--boards", default="", help="space-separated; default = all detected")
    ap.add_argument("--bundles", default="~/hil-bundles", help="dir of firmware bundles")
    ap.add_argument(
        "--no-flash", action="store_true", help="use the firmware already on the boards"
    )
    ap.add_argument("--full", action="store_true", help="full sweep instead of the short one")
    ap.add_argument("--no-baseline", action="store_true", help="skip the solo pass")
    ap.add_argument(
        "--keep-peers-connected",
        action="store_true",
        help="solo pass leaves the other links up (idle) instead of dropping them",
    )
    ap.add_argument("--results", default=str(REPO / "results"))
    args = ap.parse_args(argv)

    cfg = load_config(REPO)
    quick = not args.full
    boards = resolve_boards(cfg, args.boards.split(), args.bundles)
    if len(boards) < 2:
        sys.exit(f"need >=2 wired boards for a contention run, have {len(boards)}")
    print(f"boards: {', '.join(b.name for b in boards)}  (quick={quick})")

    if not args.no_flash:
        for b in boards:
            flash(b)

    for b in boards:
        open_dut(b)

    btctl = bluetooth.BtCtl()
    try:
        for b in boards:
            bond(b, btctl)

        solo = {}
        if not args.no_baseline:
            solo = baseline_pass(boards, btctl, quick=quick, isolate=not args.keep_peers_connected)

        nup = contention_pass(boards, quick=quick)
    finally:
        # never leave a peer blocked (baseline_pass's isolate step) -- it would
        # stay offline for the next run / the normal HIL suite.
        for b in boards:
            try:
                btctl.unblock(b.mac)
            except Exception:  # noqa: BLE001
                pass
        btctl.close()
        for b in boards:
            if b.dut:
                b.dut.close()
            for node in find_all_nodes(b.device_name):
                node.close()

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = pathlib.Path(args.results) / "parallel" / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    for name, r in solo.items():
        (outdir / f"bench-{name}-solo.json").write_text(json.dumps(r, indent=2) + "\n")
    for name, r in nup.items():
        (outdir / f"bench-{name}-{len(boards)}up.json").write_text(json.dumps(r, indent=2) + "\n")
    md = comparison_md(solo, nup, len(boards))
    (outdir / "comparison.md").write_text(md)
    (outdir / "summary.json").write_text(
        json.dumps(
            {"stamp": stamp, "n": len(boards), "quick": quick, "solo": solo, "nup": nup}, indent=2
        )
        + "\n"
    )

    print("\n" + md)
    print(f"raw records + comparison: {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
