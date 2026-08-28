"""bluetoothctl helpers for pairing the DUT (Just Works / NoInputNoOutput).

The tricky part is the agent: BlueZ needs a registered agent to auto-confirm
even a no-MITM "Just Works" pairing (`bluetoothd: new_auth() No agent
available for request type 2` otherwise), and an agent only lives as long as
the bluetoothctl process that registered it. So pairing runs inside one
long-lived `bluetoothctl` session (`BtCtl`) that holds the agent the whole
time. Read-only queries (`info`, `devices`) still shell out one-shot.

See LinuxHIDTesting.md sections 3-4 for the manual equivalents.
"""

import re
import subprocess
import threading
import time
from collections import deque


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


class BtCtl:
    """A persistent interactive bluetoothctl session holding a NoInputNoOutput
    agent for its whole lifetime."""

    def __init__(self):
        self.p = subprocess.Popen(
            ["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        self._buf = deque(maxlen=4000)
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        for cmd in ("power on", "agent NoInputNoOutput", "default-agent"):
            self.send(cmd)
            time.sleep(0.3)
        time.sleep(1.0)

    def _pump(self):
        for line in self.p.stdout:
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line)
            with self._lock:
                self._buf.append(clean.rstrip("\n"))

    def send(self, cmd):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

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

    def scan_find(self, name_contains, timeout=30):
        self.send("scan on")
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                for mac, nm in known_devices().items():
                    if name_contains in nm:
                        return mac
                if name_contains in self._tail(400):
                    m = re.search(r"Device ([0-9A-F:]{17}) .*" + re.escape(name_contains),
                                  self._tail(400))
                    if m:
                        return m.group(1)
                time.sleep(1.5)
        finally:
            self.send("scan off")
        return None

    def pair(self, mac, timeout=40):
        self.send(f"pair {mac}")
        hit = self.wait_for(
            ["Pairing successful", "Failed to pair", "org.bluez.Error",
             "AlreadyExists"], timeout)
        # AlreadyExists / already paired is fine.
        self.send(f"trust {mac}")
        time.sleep(0.5)
        self.send(f"connect {mac}")
        self.wait_for(["Connection successful", "ServicesResolved: yes",
                       "Failed to connect"], 20)
        return hit

    def connect(self, mac, timeout=20):
        self.send(f"connect {mac}")
        return self.wait_for(["Connection successful", "ServicesResolved: yes",
                              "Failed to connect"], timeout)

    def remove(self, mac):
        self.send(f"remove {mac}")
        time.sleep(1.0)

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

    if mac and is_bonded(mac):
        if not is_connected(mac):
            btctl.connect(mac)
        if is_connected(mac):
            return mac

    if mac is None:
        mac = btctl.scan_find(name_contains)
        if mac is None:
            raise RuntimeError(
                f"no BLE device named ~{name_contains!r} found to pair "
                "(is the board powered and advertising? check `bluetoothctl scan on`)")

    btctl.pair(mac)
    for _ in range(8):
        if is_bonded(mac):
            break
        time.sleep(1)
    else:
        raise RuntimeError(
            f"pairing {mac} did not complete (bluetoothd needs the agent this "
            f"session holds; check `journalctl -u bluetooth`). Last output:\n{btctl._tail(40)}")

    for _ in range(10):
        if is_connected(mac):
            return mac
        btctl.connect(mac)
        time.sleep(1)
    raise RuntimeError(f"{mac} bonded but never connected")
