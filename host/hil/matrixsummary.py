"""Markdown summary of an observer-matrix run (tester/test-matrix.sh), for CI's job summary.

    python3 host/hil/matrixsummary.py results/

Reads results/matrix-verdicts.md (the last `## matrix` section: one verdict line per lane, or an ABORT) and the
Bluepad32 lanes' GAP lines from results/matrix/*-bp32-*.log -- inputs Bluepad32 doesn't expose, which aren't failures
but are the observer's findings. Exits 1 if any lane failed or the run aborted, 2 if there's no matrix result at all.
"""

import pathlib
import re
import sys

_VERDICT = re.compile(
    r"^(?P<verdict>PASS|FAIL) observer=(?P<obs>\S+) profile=(?P<profile>\S+) lane=(?P<lane>\S+) "
    r"gamepad=(?P<pad>\S+) rc=\S+ gaps=(?P<gaps>\d+)\s*(?P<rest>.*)$"
)


def last_run(verdicts_md):
    """The lines of the last `## matrix` section."""
    lines = verdicts_md.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("## matrix")]
    return lines[starts[-1] :] if starts else []


def gaps(matrix_dir):
    """{profile: [gap text, ...]} from the Bluepad32 lanes, deduplicated across boards and rotations."""
    found = {}
    for log in sorted(pathlib.Path(matrix_dir).glob("obs-*-bp32-*.log")):
        profile = log.name.split("-")[2]
        for ln in log.read_text(errors="replace").splitlines():
            if ln.startswith("GAP "):
                text = ln[4:].strip()
                if text not in found.setdefault(profile, []):
                    found[profile].append(text)
    return found


def render(results_dir):
    """(markdown, exit code)."""
    results = pathlib.Path(results_dir)
    verdicts = results / "matrix-verdicts.md"
    run = last_run(verdicts.read_text()) if verdicts.exists() else []
    if not run:
        return (
            "## Observer matrix\n\nNo matrix result: it didn't run, or produced nothing (see the step log).\n",
            2,
        )

    out = ["## Observer matrix", "", f"`{run[0][3:].strip()}`", ""]
    rows, aborts, failed = [], [], False
    for ln in run[1:]:
        m = _VERDICT.match(ln)
        if m:
            failed |= m["verdict"] == "FAIL"
            icon = "✅" if m["verdict"] == "PASS" else "❌"
            detail = re.sub(r"\s*\[.*\]$", "", m["rest"])  # the VERSIONS tag
            # test.sh's status line, "== [hh:mm:ss] PASS  board/profile  37 passed,31 skipped  (40s, ...)": keep the counts
            detail = re.sub(r"^== \[[\d:]+\] \w+\s+\S+\s+", "", detail)
            detail = re.sub(r"\s*(=+\s*)?\(\d+s,.*\)$", "", detail).replace("|", "\\|").strip()
            rows.append(
                f"| {icon} | {m['obs']} | {m['profile']} | {m['lane']} | {m['pad']} | {m['gaps']} | {detail} |"
            )
        elif ln.startswith(("ABORT", "SKIP")):
            aborts.append(ln)
            failed |= ln.startswith("ABORT")
    if rows:
        out += [
            "| | Observer | Profile | Lane | Gamepad | GAPs | Result |",
            "|---|---|---|---|---|---|---|",
            *rows,
            "",
        ]
    out += [f"> ⚠️ {a}" for a in aborts] + ([""] if aborts else [])

    found = gaps(results / "matrix")
    if found:
        out += [
            "### Bluepad32 gaps",
            "",
            "Inputs the gamepad sends that Bluepad32 doesn't expose (not failures):",
            "",
        ]
        for profile in sorted(found):
            out += [f"- **{profile}**: " + "; ".join(found[profile])]
        out.append("")
    return "\n".join(out), 1 if failed else 0


def main(argv):
    if len(argv) != 2:
        print("usage: matrixsummary.py <results dir>", file=sys.stderr)
        return 2
    md, rc = render(argv[1])
    print(md)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
