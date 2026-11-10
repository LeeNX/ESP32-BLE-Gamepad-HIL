#!/usr/bin/env python3
"""TESTER role: look at, check and switch a multi-slot board's OTA app slots (docs/multislot-ota.md) over its
firmware console -- no esptool. tester/flash.py does the switching in runs; this is for a person at the rig.

    tester/slot.py --port <console> list                   # running / next-boot slot, each slot's image + label
    tester/slot.py --port <console> verify <bundle>...     # each bundle's app is in some slot, digest intact
    tester/slot.py --port <console> boot <n> [--wipe]      # restart into slot n (--wipe: erase NVS / bonds too)

`--port` is the console (hil_config [board.*].port). Labels come from tester/flash.py's record of what it put where
(`--state-port`: the esptool port flash.py was given, when that differs -- the C3's native USB). A composed
multi-slot bundle (builder/make_multislot.py) can be passed to verify too: its slots are checked one by one.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "host"))
from hil import multislot as ms  # noqa: E402


def wanted(bundle):
    """[(slot or None, image_sha256, label)] a bundle expects on the chip."""
    m = json.loads((bundle / "manifest.json").read_text())
    if "slots" in m:
        return [(s["slot"], s["image_sha256"], f"{s['board']} {s['profile']}") for s in m["slots"]]
    facts = ms.parse_image((bundle / "firmware.bin").read_bytes(), m["chip"])
    return [
        (None, facts["image_sha256"], f"{m['board']} {m['profile']} {m.get('lib_describe', '')}")
    ]


def show(state, labels):
    print(f"running={state.get('running')} boot={state.get('boot')} count={state.get('count')}")
    for i in sorted(state["slots"]):
        v = state["slots"][i]
        run = "*" if state.get("running") == i else " "
        nxt = "boot" if state.get("boot") == i else "    "
        if not v:
            print(f" {run} {nxt} slot {i}  empty")
            continue
        lab = labels.get(v["sha256"], "")
        print(
            f" {run} {nxt} slot {i}  {v['sha256'][:16]} {v['state']:9} {v['project']} {v['version']}  {lab}"
        )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", required=True, help="the firmware console")
    ap.add_argument(
        "--state-port", default="", help="the port flash.py flashed through, for slot labels"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    v = sub.add_parser("verify")
    v.add_argument("bundles", nargs="+", type=pathlib.Path)
    b = sub.add_parser("boot")
    b.add_argument("slot", type=int)
    b.add_argument(
        "--wipe", action="store_true", help="erase NVS (BLE bonds) too -- for a role change"
    )
    b.add_argument("--timeout", type=float, default=25.0)
    args = ap.parse_args()

    labels = {
        s["sha256"]: s.get("label", "")
        for s in ms.load_state(args.state_port or args.port).values()
    }
    con = ms.Console(args.port)
    try:
        state = con.slots()
        if state is None:
            raise SystemExit(
                "no SLOTS reply -- firmware not running, or one without multi-slot support"
            )

        if args.cmd == "list":
            show(state, labels)
            return 0

        if args.cmd == "verify":
            ok = True
            for bundle in args.bundles:
                for slot, sha, lab in wanted(bundle):
                    where = [i for i, s in state["slots"].items() if s and s["sha256"] == sha]
                    good = (slot in where) if slot is not None else bool(where)
                    ok &= good
                    at = ",".join(map(str, where)) or "-"
                    print(f"{'ok ' if good else 'BAD'} {lab:40} {sha[:16]} in slot {at}")
            return 0 if ok else 1

        if args.slot not in state["slots"] or not state["slots"][args.slot]:
            raise SystemExit(f"slot {args.slot} is empty or doesn't exist")
        good, why = con.boot(args.slot, args.wipe)
        if not good:
            raise SystemExit(f"BOOT {args.slot} -> {why}")
        after = con.wait_running(args.slot, args.timeout)
        if not after or after.get("running") != args.slot:
            raise SystemExit(f"board didn't come up in slot {args.slot}: {after}")
        show(after, labels)
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
