"""Roll one or more pytest junit files into a Markdown report.

    python -m hil.summarize results/junit-*.xml                 # -> stdout
    python -m hil.summarize results/junit-*.xml --out results/summary.md

Board and profile come from the filename (`junit-<board>-<profile>.xml`, the
name tester/test.sh writes). The report has a bundle matrix, a feature-area
rollup across every bundle, per-MCU test time, the failures inline, and only the
skips that look like a real gap (missing golden, unreadable hidraw, absent dep)
-- the routine "this profile has no rz axis" skips are counted, not listed.

Times are the pytest phase from each junit `<testsuite time>` -- the flash and
the first pair (in tester/test.sh, before pytest) are not in it.

Exit status is 1 if any test failed, else 0.
"""

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

# skip reasons that are the suite working as intended -- counted, never listed
ROUTINE_SKIP = (
    "not in this profile",
    "profile has no",
    "no hats",
    "unsigned-axes profile",
    "not the minimal profile",
    "not the maxbtn profile",
    "single-hat profile",
    "benchmark is opt-in",
    "profile has <=80 buttons",
    "no Feature Report",
    "no Output Report",
    "profile has no special buttons",
)


def _area(classname):
    # "host.tests.test_feature_report" / "test_feature_report" -> "feature_report"
    mod = (classname or "").split(".")[-1]
    return mod[5:] if mod.startswith("test_") else mod or "?"


def _bundle(path):
    # junit-<board>-<profile>.xml ; board has no dash, profile may ("signed-axes")
    stem = Path(path).stem
    parts = stem.split("-")
    if len(parts) >= 3 and parts[0] == "junit":
        return parts[1], "-".join(parts[2:])
    return stem, "?"


def _first_line(text):
    lines = (text or "").strip().splitlines()
    return lines[0] if lines else ""


def _dur(seconds):
    s = int(round(seconds))
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _suite_time(root):
    # pytest writes <testsuites><testsuite time="..">; fall back to summing cases
    node = root if root.tag == "testsuite" else root.find("testsuite")
    if node is not None and node.get("time"):
        try:
            return float(node.get("time"))
        except ValueError:
            pass
    return sum(float(c.get("time") or 0) for c in root.iter("testcase"))


def _classify(case):
    """-> ('pass' | 'fail' | 'skip' | 'xfail', message)."""
    node = case.find("failure")
    if node is None:
        node = case.find("error")
    if node is not None:
        return "fail", _first_line(node.get("message") or node.text)
    sk = case.find("skipped")
    if sk is not None:
        if (sk.get("type") or "").endswith("xfail"):
            return "xfail", ""
        return "skip", _first_line(sk.get("message") or sk.text)
    return "pass", ""


def collect(paths):
    bundles = {}  # (board, profile) -> counts
    areas = defaultdict(lambda: {"pass": 0, "fail": 0, "skip": 0})
    failures = []  # (board, profile, area, name, msg)
    gap_skips = []  # (board, profile, area, name, reason)

    for p in sorted(paths):
        board, profile = _bundle(p)
        b = bundles.setdefault(
            (board, profile), {"pass": 0, "fail": 0, "skip": 0, "xfail": 0, "time": 0.0}
        )
        try:
            root = ET.parse(p).getroot()
        except (ET.ParseError, OSError) as e:
            b["fail"] += 1
            failures.append((board, profile, "-", f"(junit unreadable: {type(e).__name__})", p))
            continue
        b["time"] += _suite_time(root)
        for case in root.iter("testcase"):
            kind, msg = _classify(case)
            area = _area(case.get("classname"))
            name = case.get("name", "?")
            b[kind] += 1
            if kind in ("pass", "fail", "skip"):
                areas[area][kind] += 1
            if kind == "fail":
                failures.append((board, profile, area, name, msg))
            elif kind == "skip" and not any(s in msg for s in ROUTINE_SKIP):
                gap_skips.append((board, profile, area, name, msg))
    return bundles, areas, failures, gap_skips


def render(bundles, areas, failures, gap_skips):
    tot = {k: sum(b[k] for b in bundles.values()) for k in ("pass", "fail", "skip", "xfail")}
    head = f"{tot['pass']} passed · {tot['fail']} failed · {tot['skip']} skipped"
    if tot["xfail"]:
        head += f" · {tot['xfail']} xfailed"
    n = len(bundles)
    out = [f"## HIL — {n} bundle{'' if n == 1 else 's'} · {head}", ""]

    out += [
        "| board / profile | pass | fail | skip | xfail | test time |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for (board, profile), b in sorted(bundles.items()):
        mark = "" if b["fail"] == 0 else "❌ "
        out.append(
            f"| {mark}{board} / {profile} | {b['pass']} | {b['fail']} | {b['skip']} | "
            f"{b['xfail']} | {_dur(b['time'])} |"
        )
    out.append("")

    by_board = defaultdict(lambda: {"n": 0, "time": 0.0})
    for (board, _profile), b in bundles.items():
        by_board[board]["n"] += 1
        by_board[board]["time"] += b["time"]
    if len(by_board) > 1 or next(iter(by_board.values()))["n"] > 1:
        out += ["### Test time per MCU", "", "| MCU | bundles | test time |", "|---|---:|---:|"]
        for board in sorted(by_board):
            v = by_board[board]
            out.append(f"| {board} | {v['n']} | {_dur(v['time'])} |")
        out.append("")

    out += ["### By feature area", "", "| area | pass | fail | skip |", "|---|---:|---:|---:|"]
    for area in sorted(areas):
        a = areas[area]
        mark = "✅" if a["fail"] == 0 else "❌"
        out.append(f"| {mark} {area} | {a['pass']} | {a['fail']} | {a['skip']} |")
    out.append("")

    if failures:
        out += ["### Failures", ""]
        for board, profile, area, name, msg in failures:
            out.append(f"- ❌ `{board}/{profile}` · `{area}::{name}`")
            if msg:
                out.append(f"  > {msg}")
        out.append("")

    if gap_skips:
        out += ["### Skipped — worth a look", ""]
        for board, profile, area, name, reason in gap_skips:
            out.append(f"- `{board}/{profile}` · `{area}::{name}` — {reason}")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def main(argv):
    args = [a for a in argv if a != "--out"]
    out_file = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv):
            print("summarize.py: --out needs a file path", file=sys.stderr)
            return 2
        out_file = argv[i + 1]
        args = argv[:i] + argv[i + 2 :]
    paths = [a for a in args if not a.startswith("-")]
    if not paths:
        print("usage: summarize.py <junit.xml>... [--out FILE]", file=sys.stderr)
        return 2

    bundles, areas, failures, gap_skips = collect(paths)
    report = render(bundles, areas, failures, gap_skips)
    sys.stdout.write(report)
    if out_file:
        Path(out_file).write_text(report)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
