"""Direct access to the DUT's /dev/hidraw* node -- report descriptor (via
sysfs), and Feature / Output / Input reports (via ioctl / read / write).

The udev rule from bootstrap-host.sh (KERNELS=="0005:1D34:8010.*", GROUP=
"plugdev") makes the node group-rw, so a user in `plugdev` can open it without
root. Pure stdlib -- no hidapi.
"""

import fcntl
import glob
import os
import pathlib
import struct

HIL_VID = 0x1D34
HIL_PID = 0x8010

# --- ioctl numbers (linux/hidraw.h) ------------------------------------
_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _IOC(direction, typ, nr, size):
    return (direction << 30) | (ord(typ) << 8) | nr | (size << 16)


HIDIOCGRDESCSIZE = _IOC(_IOC_READ, "H", 0x01, 4)  # __u32
_HIDRAW_MAX_DESC = 4096
HIDIOCGRDESC = _IOC(_IOC_READ, "H", 0x02, 4 + _HIDRAW_MAX_DESC)  # struct {u32 size; u8 value[4096]}


def _hidiocsfeature(length):
    return _IOC(_IOC_WRITE | _IOC_READ, "H", 0x06, length)


def _hidiocgfeature(length):
    return _IOC(_IOC_WRITE | _IOC_READ, "H", 0x07, length)


# --- node discovery ---------------------------------------------------
def find_node(vid=HIL_VID, pid=HIL_PID, mac=None):
    """/dev/hidrawN for the DUT, or None. Matches HID_ID (VID/PID) in the sysfs
    uevent (HID_ID=0005:00001D34:00008010); when `mac` is given, also HID_UNIQ,
    so the right node is picked when several DUTs (all sharing this VID/PID) are
    bonded to the same adapter -- which is normal on a multi-board tester."""
    want_id = f"{vid:08X}:{pid:08X}".upper()
    want_uniq = f"HID_UNIQ={mac}".upper() if mac else None
    for p in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        try:
            ue = pathlib.Path(p, "device/uevent").read_text().upper()
        except OSError:
            continue
        if want_id not in ue.replace("HID_ID=0005:", ""):
            continue
        if want_uniq and want_uniq not in ue:
            continue
        return "/dev/" + os.path.basename(p)
    return None


def descriptor_via_sysfs(node):
    """The report descriptor the kernel received over GATT."""
    n = os.path.basename(node)
    return pathlib.Path(f"/sys/class/hidraw/{n}/device/report_descriptor").read_bytes()


class HidRaw:
    """Context manager around an open /dev/hidraw* fd."""

    def __init__(self, node):
        self.node = node
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.node, os.O_RDWR | os.O_NONBLOCK)
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def descriptor(self):
        size = struct.unpack("I", fcntl.ioctl(self.fd, HIDIOCGRDESCSIZE, struct.pack("I", 0)))[0]
        buf = bytearray(4 + _HIDRAW_MAX_DESC)
        struct.pack_into("I", buf, 0, size)
        fcntl.ioctl(self.fd, HIDIOCGRDESC, buf)
        return bytes(buf[4 : 4 + size])

    def get_feature(self, report_id, length):
        buf = bytearray(length + 1)
        buf[0] = report_id
        fcntl.ioctl(self.fd, _hidiocgfeature(len(buf)), buf, True)
        return bytes(buf[1:])

    def set_feature(self, report_id, data):
        buf = bytes([report_id]) + bytes(data)
        fcntl.ioctl(self.fd, _hidiocsfeature(len(buf)), buf, True)

    def write_output(self, report_id, data):
        return os.write(self.fd, bytes([report_id]) + bytes(data))

    def read_input(self, length=64):
        try:
            return os.read(self.fd, length)
        except BlockingIOError:
            return b""


# --- minimal HID report-descriptor item walker ----------------------
_ITEM_TYPE = {0: "Main", 1: "Global", 2: "Local"}
_MAIN = {0x80: "Input", 0x90: "Output", 0xB0: "Feature", 0xA0: "Collection", 0xC0: "EndCollection"}
_GLOBAL = {
    0x00: "UsagePage",
    0x10: "LogicalMin",
    0x20: "LogicalMax",
    0x30: "PhysicalMin",
    0x40: "PhysicalMax",
    0x50: "UnitExp",
    0x60: "Unit",
    0x70: "ReportSize",
    0x80: "ReportID",
    0x90: "ReportCount",
    0xA0: "Push",
    0xB0: "Pop",
}
_LOCAL = {0x00: "Usage", 0x10: "UsageMin", 0x20: "UsageMax"}


def decode_items(desc):
    """[(name, value_or_None), ...] -- enough to make a diff human-readable."""
    out = []
    i = 0
    while i < len(desc):
        b = desc[i]
        i += 1
        size = {0: 0, 1: 1, 2: 2, 3: 4}[b & 0x03]
        tag, typ = b & 0xF0, (b >> 2) & 0x03
        val = None
        if size:
            val = int.from_bytes(desc[i : i + size], "little")
            i += size
        table = {0: _MAIN, 1: _GLOBAL, 2: _LOCAL}.get(typ, {})
        name = table.get(tag, f"{_ITEM_TYPE.get(typ, '?')}:0x{tag:02X}")
        out.append((name, val))
    return out
