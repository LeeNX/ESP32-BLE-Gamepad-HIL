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

One-shot queries and actions (bonded? connected? trust, disconnect, remove)
go straight to BlueZ's D-Bus objects via `busctl`, addressed under the chosen
adapter's object path -- a one-shot `bluetoothctl info` / `devices` can only
ever see bluetoothd's *default* controller.

**Which adapter.** `HIL_BT_ADAPTER` (env) picks the controller: an hci name
(`hci1`) or, better, its BD address (`18:69:45:F3:4C:D3`) -- the address
survives re-enumeration, the index doesn't. Unset: the only controller if
there's just one, else `hci0` (with a warning). `python -m hil.bluetooth
adapter` prints what that resolves to. See README "Bluetooth adapter".

Manual equivalent: `bluetoothctl` -> `select <addr>` / `scan on` / `pair` /
`trust` / `connect` (answer the pairing prompt `yes`); see README "Setup /
Tester".
"""

import functools
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque

from hil.names import name_matches

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

ADAPTER_ENV = "HIL_BT_ADAPTER"
_BLUEZ = "org.bluez"
_HCI_PATH = re.compile(r"^/org/bluez/(hci\d+)$")
_MAC = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def _run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _busctl_value(out):
    """`busctl get-property` prints `s "18:69:.."` / `b true` -- the value part."""
    v = out.strip().split(" ", 1)[-1]
    return v[1:-1] if v.startswith('"') and v.endswith('"') else v


def _get_prop(path, iface, prop):
    """One D-Bus property as busctl's text value, or None if the object (or the
    property) doesn't exist -- e.g. a device BlueZ has never seen."""
    r = _run(["busctl", "get-property", _BLUEZ, path, iface, prop])
    return _busctl_value(r.stdout) if r.returncode == 0 else None


def adapters():
    """{hciN: BD address} for every controller bluetoothd has registered."""
    r = _run(["busctl", "--list", "tree", _BLUEZ])
    found = {}
    for line in r.stdout.splitlines():
        m = _HCI_PATH.match(line.strip())
        if m:
            found[m.group(1)] = _get_prop(line.strip(), "org.bluez.Adapter1", "Address")
    return found


@functools.lru_cache(maxsize=1)
def adapter():
    """(hci name, BD address) of the controller every helper here uses -- see
    the module docstring. Cached for the process: restart_adapter() clears it,
    since that's the one place a controller can come back under a new index."""
    found = adapters()
    want = os.environ.get(ADAPTER_ENV, "").strip()
    if want:
        for name, addr in found.items():
            if want.lower() in (name.lower(), (addr or "").lower()):
                return name, addr
        kind = "address" if _MAC.match(want) else "name"
        raise RuntimeError(
            f"{ADAPTER_ENV}={want!r} matches no bluetooth controller by {kind} "
            f"(bluetoothd has: {found or 'none'}) -- adapter unplugged or disabled?"
        )
    if not found:
        raise RuntimeError(
            "no bluetooth controller registered with bluetoothd "
            "(adapter attached? bluetooth.service running? `bluetoothctl list`)"
        )
    if len(found) == 1:
        return next(iter(found.items()))
    name = "hci0" if "hci0" in found else sorted(found)[0]
    print(
        f"[bt] WARNING: {len(found)} controllers ({found}) and {ADAPTER_ENV} unset "
        f"-- using {name}; set {ADAPTER_ENV} to the BD address of the one to test with",
        file=sys.stderr,
    )
    return name, found[name]


def adapter_path():
    return f"/org/bluez/{adapter()[0]}"


def device_path(mac):
    return f"{adapter_path()}/dev_{mac.upper().replace(':', '_')}"


def _device_prop(mac, prop):
    return _get_prop(device_path(mac), "org.bluez.Device1", prop)


def is_bonded(mac):
    # Bonded (BlueZ >= 5.67) is the real "keys stored" flag; older BlueZ only
    # has Paired -- either one true means there's a bond to reconnect.
    return "true" in (_device_prop(mac, "Paired"), _device_prop(mac, "Bonded"))


def is_connected(mac):
    return _device_prop(mac, "Connected") == "true"


def _devices():
    """{mac: Device1 properties} for every device under the chosen adapter.
    Raises RuntimeError if BlueZ can't be enumerated -- an empty dict means
    "no devices", never "couldn't ask" (remove_matching relies on that)."""
    r = _run(
        [
            "busctl",
            "--json=short",
            "call",
            _BLUEZ,
            "/",
            "org.freedesktop.DBus.ObjectManager",
            "GetManagedObjects",
        ]
    )
    if r.returncode != 0:
        raise RuntimeError(f"can't enumerate BlueZ devices: {(r.stdout + r.stderr).strip()}")
    prefix = adapter_path() + "/dev_"
    out = {}
    for path, ifaces in json.loads(r.stdout)["data"][0].items():
        dev = ifaces.get("org.bluez.Device1")
        if dev and path.startswith(prefix) and "/" not in path[len(prefix) :]:
            props = {k: v.get("data") for k, v in dev.items()}
            out[props.get("Address", "")] = props
    return out


def is_services_resolved(mac):
    """GATT service discovery is asynchronous and happens *after* the link
    comes up -- BlueZ's HID input plugin (which bridges the HID service into
    the kernel as /dev/input + /dev/hidraw) and the DIS/Battery GATT reads in
    gatt.py both depend on it having finished, not just on Connected: yes.

    Go straight to the org.bluez.Device1 D-Bus property rather than
    `bluetoothctl info`: on bluez 5.82 `info` never prints a ServicesResolved
    line at all (checked live against a fully-resolved device), even though
    the property itself -- what `[CHG] ... ServicesResolved: yes` in an
    interactive session is sourced from -- is right there and correct."""
    return _device_prop(mac, "ServicesResolved") == "true"


def wait_services_resolved(mac, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_services_resolved(mac):
            return True
        time.sleep(0.5)
    return False


def known_devices():
    """{mac: name} -- what `bluetoothctl devices` lists, for this adapter only.
    Alias falls back to the address when a device never sent a name, as
    bluetoothctl's own listing does."""
    return {mac: p.get("Alias") or p.get("Name") or mac for mac, p in _devices().items() if mac}


def _device_call(mac, method, timeout=20):
    r = _run(["busctl", "call", _BLUEZ, device_path(mac), "org.bluez.Device1", method], timeout)
    return (r.stdout + r.stderr).strip()


def _set_trusted(mac, on, timeout=10):
    r = _run(
        [
            "busctl",
            "set-property",
            _BLUEZ,
            device_path(mac),
            "org.bluez.Device1",
            "Trusted",
            "b",
            "true" if on else "false",
        ],
        timeout,
    )
    return (r.stdout + r.stderr).strip()


def disconnect(mac):
    """One-shot: drop the live link but keep the bond, so the next session
    gets a fast reconnect instead of a full re-pair."""
    return _device_call(mac, "Disconnect")


def trust(mac):
    return _set_trusted(mac, True)


def remove(mac):
    """One-shot: forget the device (bond included) -- `bluetoothctl remove`.
    Raises RuntimeError with BlueZ's reason if it refuses."""
    r = _run(
        [
            "busctl",
            "call",
            _BLUEZ,
            adapter_path(),
            "org.bluez.Adapter1",
            "RemoveDevice",
            "o",
            device_path(mac),
        ],
        timeout=20,
    )
    if r.returncode != 0:
        raise RuntimeError(f"RemoveDevice {mac} failed: {(r.stdout + r.stderr).strip()}")


def remove_matching(name_contains):
    """Remove every device under this adapter whose name contains
    `name_contains` (test-matrix.sh / bp32_swap.sh's unbond). Returns the MACs
    removed. Tries every match, then raises RuntimeError naming any that
    failed -- a bond left behind is exactly what the unbond is there to clear."""
    gone, failed = [], []
    for m in [m for m, n in known_devices().items() if name_contains in n]:
        try:
            remove(m)
            gone.append(m)
        except RuntimeError as e:
            failed.append(str(e))
    if failed:
        raise RuntimeError(f"removed {gone or 'none'}; " + "; ".join(failed))
    return gone


def untrust(mac):
    """Stop BlueZ auto-reconnecting this bond in the background. Trusted is
    what makes `_recover_link`'s "BlueZ auto-reconnects a trusted bond" work
    mid-run -- but that same auto-reconnect will silently undo an end-of-run
    disconnect() (and recreate the /dev/input/js* node) unless untrusted
    first. A plain `connect()` at the start of the next run doesn't need
    Trusted, so this is safe to leave off until ensure_paired() re-trusts."""
    return _set_trusted(mac, False)


def connected_devices():
    """MACs with a live link on the adapter right now -- lets a bench sweep
    record how many gamepads were actually on the air, not just how many it
    was driving. Best-effort: any failure just yields an empty list."""
    try:
        return [m for m, p in _devices().items() if m and p.get("Connected")]
    except Exception:
        return []


def _adapter_powered():
    """Is the chosen controller back and powered? Re-resolves it (cache
    cleared) -- the index it comes back under is what matters from here on."""
    adapter.cache_clear()
    try:
        return _get_prop(adapter_path(), "org.bluez.Adapter1", "Powered") == "true"
    except RuntimeError:  # not re-registered yet
        return False


def restart_adapter(timeout=30):
    """Restart bluetoothd (`systemctl restart bluetooth`) and wait for the
    adapter to come back powered.

    Last-resort recovery for a wedged BlueZ/HCI stack that a bluetoothctl-level
    retry can't clear (org.bluez.Error.Failed le-connection-abort-by-local,
    pairing that never bonds -- see hil-rig-ble-flake-sep09). Any BtCtl session
    open across this call is dead afterwards -- bluetoothd restarting drops its
    D-Bus connection -- so callers must open a fresh BtCtl once this returns.

    Requires a NOPASSWD sudoers rule for exactly this command (installed by
    tester/bootstrap-host.sh); raises RuntimeError with a pointer to that if
    it's missing, instead of hanging on a password prompt.
    """
    try:
        subprocess.run(
            ["sudo", "-n", "systemctl", "restart", "bluetooth"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "sudo systemctl restart bluetooth failed -- needs the NOPASSWD "
            "sudoers rule from tester/bootstrap-host.sh on this tester. "
            f"stderr: {e.stderr.strip()}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("sudo systemctl restart bluetooth timed out") from e

    deadline = time.time() + timeout
    while time.time() < deadline:
        active = _run(["systemctl", "is-active", "bluetooth"], timeout=5).stdout.strip()
        if active == "active" and _adapter_powered():
            return
        time.sleep(1)
    raise RuntimeError("bluetooth service restarted but the adapter did not come back up in time")


class BtCtl:
    """A persistent bluetoothctl session: holds a NoInputNoOutput agent, keeps
    discovery running, and auto-confirms pairing prompts."""

    def __init__(self):
        addr = adapter()[1]  # resolve first -- a bad HIL_BT_ADAPTER raises before spawning anything
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
        # Pin this session to the chosen controller first -- every command
        # below (scan, pair, connect, remove) acts on the selected one.
        self.send(f"select {addr}")
        time.sleep(0.3)
        for cmd in ("power on", "agent NoInputNoOutput", "default-agent"):
            self.send(cmd)
            time.sleep(0.3)
        self._scan(True)  # stays on until close() -- see module docstring
        time.sleep(1.5)
        # `select` is silent on success; on failure bluetoothctl prints this and
        # carries on with its default controller -- the wrong radio, quietly.
        if f"Controller {addr} not available" in self._tail(self._buf.maxlen):
            self.close()
            raise RuntimeError(
                f"bluetoothctl can't select controller {addr} -- gone since it was resolved?"
            )

    def _scan(self, on):
        self.send("scan on" if on else "scan off")

    def _ensure_scanning(self):
        """BlueZ discovery can stop on its own; re-arm it if it has."""
        if _get_prop(adapter_path(), "org.bluez.Adapter1", "Discovering") != "true":
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
            try:
                devs = known_devices()
            except RuntimeError:  # bluetoothd busy for a moment -- just look again
                devs = {}
            for mac, nm in devs.items():
                if name_matches(nm, name_contains):
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

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def ensure_paired(btctl, name_contains, known_mac=None, want_fresh=False):
    """Return a bonded+connected MAC for the DUT, pairing if needed.

    want_fresh: drop any existing bond first (the HID descriptor changed, e.g. a
    profile switch -- hosts cache the descriptor against the bond).
    """
    mac = known_mac
    if mac is None:
        for m, n in known_devices().items():
            if name_matches(n, name_contains):
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
            # Re-trust on the reconnect path too, not just fresh pair()s --
            # the previous session's teardown untrusts before disconnecting
            # (see conftest.py bt_mac) so BlueZ doesn't silently reconnect it
            # and recreate the evdev/js node right after cleanup.
            # _recover_link's mid-run auto-reconnect needs Trusted back.
            btctl.send(f"trust {mac}")
            if not wait_services_resolved(mac):
                raise RuntimeError(
                    f"{mac} reconnected but GATT services never resolved -- "
                    "the kernel HID node (evdev/hidraw) and GATT reads "
                    "(battery/DIS) all depend on this"
                )
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

    # A connection already in flight makes `pair` fail with
    # org.bluez.Error.InProgress -- settle the link down first.
    if is_connected(mac):
        btctl.send(f"disconnect {mac}")
        for _ in range(10):
            if not is_connected(mac):
                break
            time.sleep(1)
        time.sleep(2)

    bonded = False
    for attempt in range(3):
        btctl.pair(mac)
        for _ in range(12):
            if is_bonded(mac):
                bonded = True
                break
            time.sleep(1)
        if bonded:
            break
        if attempt < 2:
            btctl.send(f"disconnect {mac}")
            time.sleep(4)  # let an InProgress connect time out before retrying
    if not bonded:
        raise RuntimeError(
            f"pairing {mac} did not complete (bluetoothd needs the agent this "
            f"session holds; check `journalctl -u bluetooth`). Last output:\n{btctl._tail(40)}"
        )

    for _ in range(12):
        if is_connected(mac):
            if not wait_services_resolved(mac):
                raise RuntimeError(
                    f"{mac} connected but GATT services never resolved -- "
                    "the kernel HID node (evdev/hidraw) and GATT reads "
                    "(battery/DIS) all depend on this"
                )
            return mac
        btctl.connect(mac)
        time.sleep(1)
    raise RuntimeError(f"{mac} bonded but never connected")


_USAGE = "usage: python -m hil.bluetooth {adapter | restart-adapter | remove-matching <name>}"


def main(argv):
    """CLI for the shell scripts:

    - `adapter` -- print the controller HIL_BT_ADAPTER resolves to
      (`hci0 18:69:45:F3:4C:D3`); exits 1 with the reason if it can't.
    - `restart-adapter` -- tester/test-all.sh's retry recovery. Only ever
      invoked where the caller already knows the adapter is exclusively
      theirs right now (phase 1 / a solo run) -- see tester/test-all.sh's
      retry_run/round_barrier comments for why a concurrent --by-board
      phase 2 lane must never do this.
    - `remove-matching <name>` -- forget every device whose name contains
      <name> (test-matrix.sh / bp32_swap.sh's unbond)."""
    try:
        if argv == ["adapter"]:
            print(*adapter())
            return 0
        if argv == ["restart-adapter"]:
            restart_adapter()
            return 0
        if len(argv) == 2 and argv[0] == "remove-matching":
            for m in remove_matching(argv[1]):
                print(f"removed {m}")
            return 0
    except RuntimeError as e:
        print(f"hil.bluetooth: {e}", file=sys.stderr)
        return 1
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
