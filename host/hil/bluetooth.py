"""bluetoothctl helpers for pairing the DUT (Just Works / NoInputNoOutput).

Two things make this fiddly, both handled by one long-lived `bluetoothctl`
session (`BtCtl`):

1. **The agent.** BlueZ needs a registered agent to confirm even a no-MITM
   "Just Works" pairing, and an agent only lives as long as the bluetoothctl
   that registered it. BlueZ 5.82's `NoInputNoOutput` agent still *prompts*
   `[agent] Accept pairing (yes/no):` for the device's `RequestAuthorization`
   during bonding -- so the reader thread auto-answers any `(yes/no)` prompt
   with `yes`.
2. **Device purging.** BlueZ 5.82 drops every discovered-but-unconnected device
   from its object tree the moment discovery stops (`[DEL]` storm), so a `pair`
   issued after `scan off` fails with "not available". `BtCtl` therefore keeps
   discovery running for its whole lifetime and only stops it in `close()`.

Read-only queries (`info`, `devices`) still shell out one-shot.

Manual equivalent: `bluetoothctl` -> `scan on` / `pair` / `trust` / `connect`
(answer the pairing prompt `yes`); see README "Setup / Tester".
"""

import os
import re
import subprocess
import threading
import time
from collections import deque

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def info(mac):
    return _run(["bluetoothctl", "info", mac]).stdout


def is_bonded(mac):
    t = info(mac)
    return "Paired: yes" in t or "Bonded: yes" in t


def is_connected(mac):
    return "Connected: yes" in info(mac)


def known_devices():
    devs = {}
    for line in _run(["bluetoothctl", "devices"]).stdout.splitlines():
        m = re.match(r"Device ([0-9A-F:]{17}) (.+)", line.strip())
        if m:
            devs[m.group(1)] = m.group(2)
    return devs


def connected_devices():
    """MACs with a live link on the adapter right now (BlueZ >= 5.65's
    `devices Connected`) -- lets a bench sweep record how many gamepads were
    actually on the air, not just how many it was driving. Best-effort: an
    older bluetoothctl that rejects the filter just yields an empty list."""
    try:
        out = _run(["bluetoothctl", "devices", "Connected"]).stdout
    except Exception:
        return []
    return re.findall(r"Device ([0-9A-F:]{17})", out)


class BtCtl:
    """A persistent bluetoothctl session: holds a NoInputNoOutput agent, keeps
    discovery running, and auto-confirms pairing prompts."""

    def __init__(self):
        self.p = subprocess.Popen(
            ["bluetoothctl"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        self._buf = deque(maxlen=8000)
        self._lock = threading.Lock()
        self._io = threading.Lock()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        for cmd in ("power on", "agent NoInputNoOutput", "default-agent"):
            self.send(cmd)
            time.sleep(0.3)
        self._scan(True)  # stays on until close() -- see module docstring
        time.sleep(1.5)

    def _scan(self, on):
        self.send("scan on" if on else "scan off")

    def _ensure_scanning(self):
        """BlueZ discovery can stop on its own; re-arm it if it has."""
        out = _run(["bluetoothctl", "show"]).stdout
        if "Discovering: yes" not in out:
            self._scan(True)
            time.sleep(2)

    def _pump(self):
        """Read raw bytes (agent prompts have no trailing newline, so line
        iteration would stall), buffer cleaned lines, auto-answer yes/no."""
        pending = ""
        while True:
            try:
                chunk = os.read(self.p.stdout.fileno(), 4096)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            text = _ANSI.sub("", chunk.decode("utf-8", "replace"))
            if "(yes/no)" in text:
                self._write("yes")
            pending += text
            lines = pending.split("\n")
            pending = lines.pop()
            with self._lock:
                self._buf.extend(s.rstrip("\r") for s in lines)

    def _write(self, cmd):
        with self._io:
            try:
                self.p.stdin.write((cmd + "\n").encode())
                self.p.stdin.flush()
            except (OSError, ValueError):
                pass

    def send(self, cmd):
        self._write(cmd)

    def _tail(self, n=200):
        with self._lock:
            return "\n".join(list(self._buf)[-n:])

    def wait_for(self, needles, timeout):
        """Return the first needle seen in new output, or None on timeout."""
        with self._lock:
            start_len = len(self._buf)
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                recent = "\n".join(list(self._buf)[start_len:])
            for nd in needles:
                if nd in recent:
                    return nd
            time.sleep(0.3)
        return None

    def scan_find(self, name_contains, timeout=70):
        """Wait until `bluetoothctl devices` lists a match, re-arming discovery
        as needed. Only trust the devices list, never scrollback."""
        deadline = time.time() + timeout
        last_kick = 0.0
        while time.time() < deadline:
            for mac, nm in known_devices().items():
                if name_contains in nm:
                    return mac
            if time.time() - last_kick > 10:
                self._ensure_scanning()
                last_kick = time.time()
            time.sleep(1.5)
        return None

    def pair(self, mac, timeout=45):
        """Bond + connect. Discovery stays on so the device object survives;
        the reader thread answers the pairing prompt."""
        hit = None
        for attempt in range(4):
            self.send(f"pair {mac}")
            hit = self.wait_for(
                [
                    "Pairing successful",
                    "Failed to pair",
                    "org.bluez.Error",
                    "AlreadyExists",
                    "not available",
                ],
                timeout,
            )
            if hit in ("Pairing successful", "AlreadyExists"):
                break
            if is_bonded(mac):
                hit = "Pairing successful"
                break
            time.sleep(3)  # scan is still on -- let the device re-resolve
        self.send(f"trust {mac}")
        time.sleep(0.5)
        self.send(f"connect {mac}")
        self.wait_for(["Connection successful", "ServicesResolved: yes", "Failed to connect"], 20)
        return hit

    def connect(self, mac, timeout=20):
        self.send(f"connect {mac}")
        return self.wait_for(
            ["Connection successful", "ServicesResolved: yes", "Failed to connect"], timeout
        )

    def remove(self, mac):
        self.send(f"disconnect {mac}")
        time.sleep(1.5)
        self.send(f"remove {mac}")
        time.sleep(3.0)  # let bluetoothd drop the bond
        # RemoveDevice suppresses re-discovery briefly -- kick the scan so the
        # (still-advertising) board reappears in the object tree.
        self._scan(False)
        time.sleep(0.5)
        self._scan(True)
        time.sleep(2.0)

    def close(self):
        try:
            self.send("scan off")
            self.send("quit")
            self.p.wait(timeout=5)
        except Exception:
            self.p.kill()


def ensure_paired(btctl, name_contains, known_mac=None, want_fresh=False):
    """Return a bonded+connected MAC for the DUT, pairing if needed.

    want_fresh: drop any existing bond first (the HID descriptor changed, e.g. a
    profile switch -- hosts cache the descriptor against the bond).
    """
    mac = known_mac
    if mac is None:
        for m, n in known_devices().items():
            if name_contains in n:
                mac = m
                break

    if mac and want_fresh:
        btctl.remove(mac)
        mac = None

    # A bond we have no state record for (known_mac is None) is untrusted -- it
    # may be from a different profile / HID descriptor. Drop it and re-pair.
    if mac and known_mac is None and is_bonded(mac):
        btctl.remove(mac)
        mac = None

    if mac and is_bonded(mac):
        if not is_connected(mac):
            btctl.connect(mac)
        if is_connected(mac):
            return mac

    if mac is None:
        mac = btctl.scan_find(name_contains)
        if mac is None:
            # one hard reset of discovery, then a longer look
            btctl._scan(False)
            time.sleep(2)
            btctl._scan(True)
            mac = btctl.scan_find(name_contains, timeout=90)
        if mac is None:
            raise RuntimeError(
                f"no BLE device named ~{name_contains!r} found to pair "
                "(is the board powered and advertising? check `bluetoothctl scan on`)"
            )

    btctl.pair(mac)
    for _ in range(12):
        if is_bonded(mac):
            break
        time.sleep(1)
    else:
        raise RuntimeError(
            f"pairing {mac} did not complete (bluetoothd needs the agent this "
            f"session holds; check `journalctl -u bluetooth`). Last output:\n{btctl._tail(40)}"
        )

    for _ in range(12):
        if is_connected(mac):
            return mac
        btctl.connect(mac)
        time.sleep(1)
    raise RuntimeError(f"{mac} bonded but never connected")
