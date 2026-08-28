"""junit XML -> results/summary.md (one row per test file / feature area)."""

import sys
import xml.etree.ElementTree as ET
from collections import defaultdict


def main(junit_path, out_path):
    root = ET.parse(junit_path).getroot()
    cases = root.iter("testcase")
    by_file = defaultdict(lambda: {"pass": 0, "fail": 0, "skip": 0, "fails": []})
    for c in cases:
        f = (c.get("classname") or "").split(".")[0] or "unknown"
        if c.find("failure") is not None or c.find("error") is not None:
            by_file[f]["fail"] += 1
            by_file[f]["fails"].append(c.get("name"))
        elif c.find("skipped") is not None:
            by_file[f]["skip"] += 1
        else:
            by_file[f]["pass"] += 1

    lines = ["# HIL results", "", "| Feature | Pass | Fail | Skip |", "|---|---|---|---|"]
    total_fail = 0
    for f in sorted(by_file):
        s = by_file[f]
        total_fail += s["fail"]
        mark = "✅" if s["fail"] == 0 else "❌"
        lines.append(f"| {mark} {f} | {s['pass']} | {s['fail']} | {s['skip']} |")
    lines.append("")
    for f in sorted(by_file):
        for name in by_file[f]["fails"]:
            lines.append(f"- ❌ `{f}::{name}`")

    with open(out_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
