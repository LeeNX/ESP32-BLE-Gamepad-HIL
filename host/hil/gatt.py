"""Read the DUT's GATT side -- Device Information (0x180A), Battery (0x180F),
PnP ID (0x2A50), Battery Power State (0x2A1A).

The device is *already* connected: once bonded, BlueZ's input plugin holds the
link open and bridges the HID service into the kernel. Device Information,
Battery and PnP stay visible to a generic GATT client (see GattVsHid.md), so we
just call `ReadValue` on their `org.bluez.GattCharacteristic1` D-Bus objects
over that existing connection.

We deliberately do NOT use bleak here: its BlueZ backend calls `Disconnect()`
on `__aexit__`, which tears down the whole BLE link -- dropping the HID
connection and the `/dev/input/event*` node mid-test.
"""

import asyncio
import re
import subprocess

BLUEZ = "org.bluez"


def _u(short):
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"


DIS_CHARS = {
    "model": _u(0x2A24), "serial": _u(0x2A25), "fw": _u(0x2A26),
    "hw": _u(0x2A27), "sw": _u(0x2A28), "mfr": _u(0x2A29),
}
PNP_UUID = _u(0x2A50)
BATTERY_LEVEL_UUID = _u(0x2A19)
POWER_STATE_UUID = _u(0x2A1A)

_POWER_FIELDS = ("info", "discharging", "charging", "level")
_POWER_MEANING = {
    "info": {0: "unknown", 1: "not-supported", 2: "not-present", 3: "present"},
    "discharging": {0: "unknown", 1: "not-supported", 2: "not-discharging", 3: "discharging"},
    "charging": {0: "unknown", 1: "not-chargeable", 2: "not-charging", 3: "charging"},
    "level": {0: "unknown", 1: "not-supported", 2: "good", 3: "critically-low"},
}


def _decode_pnp(raw):
    if not raw or len(raw) < 7:
        return {"_raw": bytes(raw).hex()} if raw else None
    return {
        "vendor_id_source": raw[0],
        "vendor_id": int.from_bytes(raw[1:3], "little"),
        "product_id": int.from_bytes(raw[3:5], "little"),
        "product_version": int.from_bytes(raw[5:7], "little"),
    }


def _decode_power_state(raw):
    if not raw:
        return None
    b = raw[0]
    vals = {f: (b >> (2 * i)) & 0b11 for i, f in enumerate(_POWER_FIELDS)}
    return {"_byte": b, **vals,
            **{f"{f}_meaning": _POWER_MEANING[f][vals[f]] for f in _POWER_FIELDS}}


def _dev_path(mac):
    return f"/org/bluez/hci0/dev_{mac.upper().replace(':', '_')}"


async def _char_paths(bus, dev_path):
    """{uuid: object_path} for every GattCharacteristic1 under dev_path."""
    from dbus_fast import BusType  # noqa: F401  (import lives with dbus_fast)

    intro = await bus.introspect(BLUEZ, "/")
    root = bus.get_proxy_object(BLUEZ, "/", intro)
    om = root.get_interface("org.freedesktop.DBus.ObjectManager")
    objects = await om.call_get_managed_objects()
    out = {}
    for path, ifaces in objects.items():
        ch = ifaces.get("org.bluez.GattCharacteristic1")
        if ch and path.startswith(dev_path + "/"):
            out[ch["UUID"].value.lower()] = path
    return out


async def _read_char(bus, path):
    intro = await bus.introspect(BLUEZ, path)
    obj = bus.get_proxy_object(BLUEZ, path, intro)
    iface = obj.get_interface("org.bluez.GattCharacteristic1")
    return bytes(await iface.call_read_value({}))


async def _connect_bus():
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus
    return await MessageBus(bus_type=BusType.SYSTEM).connect()


async def _read_uuids(mac, uuids):
    """uuids: iterable of char UUIDs -> {uuid_lower: bytes|None}."""
    bus = await _connect_bus()
    try:
        paths = await _char_paths(bus, _dev_path(mac))
        out = {}
        for u in uuids:
            p = paths.get(u.lower())
            if p is None:
                out[u.lower()] = None
                continue
            try:
                out[u.lower()] = await _read_char(bus, p)
            except Exception:
                out[u.lower()] = None
        return out
    finally:
        bus.disconnect()  # closes the D-Bus socket only, NOT the BLE link


async def _read_all(mac):
    raw = await _read_uuids(
        mac, [*DIS_CHARS.values(), PNP_UUID, BATTERY_LEVEL_UUID, POWER_STATE_UUID])
    di = {}
    for key, uuid in DIS_CHARS.items():
        b = raw.get(uuid.lower())
        di[key] = b.decode("utf-8", "replace").strip("\x00").strip() if b else None
    bl = raw.get(BATTERY_LEVEL_UUID.lower())
    return {
        "device_info": di,
        "pnp": _decode_pnp(raw.get(PNP_UUID.lower())),
        "battery_level": bl[0] if bl else None,
        "power_state": _decode_power_state(raw.get(POWER_STATE_UUID.lower())),
    }


def read_all(mac):
    """Blocking: {device_info, pnp, battery_level, power_state}."""
    return asyncio.run(_read_all(mac))


def read_battery_level(mac):
    raw = asyncio.run(_read_uuids(mac, [BATTERY_LEVEL_UUID]))[BATTERY_LEVEL_UUID.lower()]
    return raw[0] if raw else None


def read_power_state(mac):
    raw = asyncio.run(_read_uuids(mac, [POWER_STATE_UUID]))[POWER_STATE_UUID.lower()]
    return _decode_power_state(raw)


def read_battery_upower(mac):
    """Independent battery-% cross-check via UPower (no BLE code). Returns an
    int percentage or None. See LinuxHIDTesting.md "Battery level via upower"."""
    path = f"gaming_input_dev_{mac.replace(':', '_')}"
    try:
        listed = subprocess.run(["upower", "-e"], capture_output=True, text=True, timeout=10).stdout
        match = next((ln for ln in listed.splitlines() if path in ln), None)
        if not match:
            return None
        out = subprocess.run(["upower", "-i", match.strip()],
                             capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"percentage:\s*([0-9.]+)", out)
        return round(float(m.group(1))) if m else None
    except Exception:
        return None


if __name__ == "__main__":
    import json
    import sys

    data = read_all(sys.argv[1])
    data["battery_upower"] = read_battery_upower(sys.argv[1])
    print(json.dumps(data, indent=2))
