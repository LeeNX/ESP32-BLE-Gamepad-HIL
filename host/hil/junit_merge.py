"""Merge a phase1 (connectivity-check) junit file into a phase2 (functional)
junit file -- used by tester/test-all.sh --by-board's phase1_turn(), which
splits one bundle's run into a serialized flash+pair+test_connection.py pass
and a parallel everything-else pass, then needs the two reports combined back
into the single <board>/<profile> report summarize.py and the CI gate expect.

    python -m hil.junit_merge <phase1.xml> <phase2.xml> <out.xml>

If <phase1.xml> doesn't exist (the board was skipped before phase 1 ran),
<phase2.xml> is copied to <out.xml> unchanged.
"""

import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_SUM_ATTRS = ("tests", "failures", "errors", "skipped")


def _suite(root):
    return root if root.tag == "testsuite" else root.find("testsuite")


def main(argv):
    if len(argv) != 3:
        print("usage: junit_merge.py <phase1.xml> <phase2.xml> <out.xml>", file=sys.stderr)
        return 2
    phase1, phase2, out = argv

    if not Path(phase1).exists():
        if Path(phase2).resolve() != Path(out).resolve():
            shutil.copyfile(phase2, out)
        return 0

    a_root, b_root = ET.parse(phase1).getroot(), ET.parse(phase2).getroot()
    a, b = _suite(a_root), _suite(b_root)
    for case in list(a):
        b.append(case)
    for attr in _SUM_ATTRS:
        b.set(attr, str(int(b.get(attr) or 0) + int(a.get(attr) or 0)))
    b.set("time", str(float(b.get("time") or 0) + float(a.get("time") or 0)))
    # keep phase2's root shape (pytest's default <testsuites><testsuite>.../>,
    # or a bare <testsuite> for an older pytest) so downstream parsers
    # (dorny/test-reporter, summarize.py) see the same structure either way.
    ET.ElementTree(b_root).write(out, encoding="utf-8", xml_declaration=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
