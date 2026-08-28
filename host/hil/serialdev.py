"""Line-protocol client for the hil_runner firmware (see firmware/src/hil_runner.cpp)."""

import time

import serial

# First token of every command reply. The boot banner ("HIL hil_runner ready
# ...") is deliberately not here so it gets skipped like any other stray line.
REPLY_PREFIXES = ("OK", "ERR", "PONG", "CONN", "ID", "CONFIG")


class SerialError(RuntimeError):
    pass


class SerialDev:
    def __init__(self, port, baud=115200, boot_wait=2.0):
        self.ser = serial.Serial(port, baud, timeout=0.2)
        # Opening the port resets the ESP32; give it time to boot before we talk.
        time.sleep(boot_wait)
        self.ser.reset_input_buffer()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    # --- raw I/O -----------------------------------------------------------
    def drain(self):
        self.ser.reset_input_buffer()

    def _readline(self, timeout):
        deadline = time.time() + timeout
        buf = bytearray()
        while time.time() < deadline:
            b = self.ser.read(1)
            if not b:
                continue
            if b == b"\n":
                return buf.decode("ascii", "replace").strip()
            if b != b"\r":
                buf += b
        raise SerialError("timeout waiting for a reply line")

    def command(self, cmd, timeout=6.0, prefixes=REPLY_PREFIXES):
        """Send one command line, return the first reply line matching a known
        prefix. Stray lines (boot banner, debug) are skipped."""
        self.ser.write((cmd + "\n").encode("ascii"))
        self.ser.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self._readline(timeout=max(0.1, deadline - time.time()))
            if line.split(" ", 1)[0] in prefixes:
                return line
        raise SerialError(f"no reply to {cmd!r}")

    def ok(self, cmd, **kw):
        r = self.command(cmd, **kw)
        if r != "OK":
            raise SerialError(f"{cmd!r} -> {r!r} (expected OK)")
        return r

    # --- protocol convenience -------------------------------------------------
    def ping(self):
        return self.command("PING") == "PONG"

    def wait_ready(self, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.ping():
                    return
            except SerialError:
                pass
        raise SerialError("firmware never answered PING")

    def firmware_id(self):
        return self.command("ID?")

    def config(self):
        line = self.command("CONFIG?")
        out = {}
        for tok in line.split(" ")[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                out[k] = v
        out["buttons"] = int(out.get("buttons", 0))
        out["hats"] = int(out.get("hats", 0))
        out["axes"] = out.get("axes", "").split(",") if out.get("axes") else []
        out["axesMin"] = int(out.get("axesMin", 0))
        out["axesMax"] = int(out.get("axesMax", 0))
        return out

    def begin(self):
        return self.ok("BEGIN", timeout=10.0)

    def connected(self):
        return self.command("CONN?") == "CONN 1"

    def wait_connected(self, timeout=25.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.connected():
                return
            time.sleep(0.5)
        raise SerialError("device did not report CONN 1")

    def press(self, n):
        self.ok(f"PRESS {n}")

    def release(self, n):
        self.ok(f"RELEASE {n}")

    def special(self, sub, idx):
        self.ok(f"SPECIAL {sub} {idx}")

    def axis(self, name, value):
        self.ok(f"AXIS {name} {int(value)}")

    def hat(self, idx, direction):
        self.ok(f"HAT {idx} {direction}")

    def battery(self, level):
        self.ok(f"BATTERY {level}")

    def reset(self):
        self.ok("RESET")
