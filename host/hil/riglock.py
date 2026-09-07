"""Rig run-status file: what's on the HIL rig right now, for humans and tooling.

Companion to the flock in tester/rig-lock.sh -- the lock is the mutual exclusion,
this is the "who/what/where" readout. Stdlib only and no `hil.*` imports on
purpose: tester/rig-status.sh runs it by path (`python3 host/hil/riglock.py ...`)
with no venv and no PYTHONPATH, and the rig has no `jq`.

    python3 host/hil/riglock.py write busy       # metadata from HIL_RUN_* env
    python3 host/hil/riglock.py write idle 0      # fold the run into `last`, rc=0
    python3 host/hil/riglock.py status            # human readout (rig-status.sh)

State file: $XDG_CACHE_HOME/esp32-hil/rig-status.json (default ~/.cache/...).
"""

import datetime as dt
import json
import os
import pathlib
import socket
import subprocess
import sys

CACHE = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or (pathlib.Path.home() / ".cache"))
STATUS = CACHE / "esp32-hil" / "rig-status.json"


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _git_commit():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return ""


def _run_meta():
    """The current run's identity, from the environment tester/rig-lock.sh sets."""
    who = os.environ.get("HIL_RUN_WHO") or f"{os.environ.get('USER', '?')}@{socket.gethostname()}"
    return {
        "who": who,
        "what": os.environ.get("HIL_RUN_WHAT", ""),
        "url": os.environ.get("HIL_RUN_URL", ""),
        "commit": _git_commit(),
        "pid": int(os.environ.get("HIL_RUN_PID") or os.getppid()),
        "host": socket.gethostname(),
        "started": _now(),
    }


def _load():
    try:
        return json.loads(STATUS.read_text())
    except Exception:
        return {}


def _save(data):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(STATUS)


def write(state, rc=None):
    data = _load()
    if state == "busy":
        # keep the first busy write's start time if the same run re-announces
        prev = data.get("run") or {}
        run = _run_meta()
        if prev.get("pid") == run["pid"] and prev.get("started"):
            run["started"] = prev["started"]
        data["run"] = run
        data["state"] = "busy"
    else:  # idle
        run = data.get("run") or _run_meta()
        run["finished"] = _now()
        run["rc"] = int(rc) if rc is not None else None
        data["last"] = run
        data.pop("run", None)
        data["state"] = "idle"
    _save(data)


def _parse(ts):
    try:
        return dt.datetime.fromisoformat(ts)
    except Exception:
        return None


def _dur(seconds):
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _pid_alive(run):
    if run.get("host") and run["host"] != socket.gethostname():
        return None  # can't tell from here
    try:
        os.kill(int(run["pid"]), 0)
        return True
    except (ProcessLookupError, ValueError, TypeError):
        return False
    except PermissionError:
        return True


def _fmt_run(run):
    out = []
    if run.get("what"):
        out.append(f"  what:    {run['what']}")
    if run.get("commit"):
        out.append(f"  commit:  {run['commit']}")
    if run.get("url"):
        out.append(f"  url:     {run['url']}")
    return out


def render():
    data = _load()
    if data.get("state") == "busy" and data.get("run"):
        run = data["run"]
        started = _parse(run.get("started", ""))
        elapsed = ""
        if started:
            elapsed = f" ({_dur((dt.datetime.now(dt.timezone.utc) - started).total_seconds())})"
        lines = [
            f"RIG BUSY  since {run.get('started', '?')}{elapsed}",
            f"  who:     {run.get('who')}",
        ]
        lines += _fmt_run(run)
        alive = _pid_alive(run)
        tag = {True: "alive", False: "DEAD -- stale lock/status?", None: "different host"}[alive]
        lines.append(f"  pid:     {run.get('pid')} ({tag})")
        print("\n".join(lines))
        return 0

    last = data.get("last")
    if not last:
        print("RIG IDLE  (no run recorded yet)")
        return 0
    rc = last.get("rc")
    verdict = "PASS" if rc == 0 else (f"FAIL rc={rc}" if rc is not None else "unknown")
    print(
        f"RIG IDLE  (last run: {last.get('who')}, finished {last.get('finished', '?')}, {verdict})"
    )
    for line in _fmt_run(last):
        print(line)
    return 0


def main(argv):
    if not argv:
        return render()
    cmd = argv[0]
    if cmd == "status":
        return render()
    if cmd == "write" and len(argv) >= 2:
        write(argv[1], argv[2] if len(argv) > 2 else None)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
