"""Read the DUT's GATT side -- Device Information (0x180A), PnP ID (0x2A50),
Battery (0x180F: level 0x2A19, power state 0x2A1A) -- via `bleak`
(CoreBluetooth on macOS, WinRT on Windows).

Same fields as the rig's ../host/hil/gatt.py, different transport: the rig
talks D-Bus to BlueZ directly (and *avoids* bleak on Linux -- its BlueZ
backend disconnects on `__aexit__`, dropping the HID link mid-test, see that
module's docstring). macOS has no D-Bus, so this goes through `bleak`, scanning
by the DUT's advertised name (`NAME?`) rather than a MAC -- bleak's device
identifier is a CoreBluetooth UUID on macOS, not stable across machines/runs.

CONFIRMED on real hardware: this reaches Device Information / PnP / Battery
fine even though the DUT is already bonded to this Mac and its HID service is
owned by the system HID stack -- but the HID service itself (0x1812, which
carries the Feature/Input/Output Report characteristics) is NOT exposed here.
Once a BLE peripheral advertises HID, macOS's Bluetooth HID stack claims that
one service for itself and doesn't hand it to another GATT client; the other
services on the same device stay reachable. For the Feature Report, see
test_feature_report.py / hidapi instead -- that goes through the OS's native
HID API, which *can* reach it.
"""

from __future__ import annotations

import asyncio


def _u(short16):
    return f"0000{short16:04x}-0000-1000-8000-00805f9b34fb"


DIS_CHARS = {
    "model": _u(0x2A24),
    "serial": _u(0x2A25),
    "fw": _u(0x2A26),
    "hw": _u(0x2A27),
    "sw": _u(0x2A28),
    "mfr": _u(0x2A29),
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
    return {
        "_byte": b,
        **vals,
        **{f"{f}_meaning": _POWER_MEANING[f][vals[f]] for f in _POWER_FIELDS},
    }


async def _find(name, timeout=15.0):
    from bleak import BleakScanner

    device = await BleakScanner.find_device_by_filter(
        lambda d, adv: adv.local_name == name, timeout=timeout
    )
    if device is None:
        raise TimeoutError(f"no BLE advertisement seen for {name!r} within {timeout:.0f}s")
    return device


async def _read_uuids(name, uuids):
    """uuids: iterable of char UUIDs -> {uuid_lower: bytes|None}."""
    from bleak import BleakClient

    device = await _find(name)
    async with BleakClient(device) as client:
        out = {}
        for u in uuids:
            try:
                out[u.lower()] = bytes(await client.read_gatt_char(u))
            except Exception:
                out[u.lower()] = None
        return out


async def _read_all(name):
    raw = await _read_uuids(
        name, [*DIS_CHARS.values(), PNP_UUID, BATTERY_LEVEL_UUID, POWER_STATE_UUID]
    )
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


def read_all(name):
    """Blocking: {device_info, pnp, battery_level, power_state}. `name` is the
    DUT's advertised BLE name (`dut.device_name()` / `NAME?`)."""
    return asyncio.run(_read_all(name))


def read_battery_level(name):
    raw = asyncio.run(_read_uuids(name, [BATTERY_LEVEL_UUID]))[BATTERY_LEVEL_UUID.lower()]
    return raw[0] if raw else None


def read_power_state(name):
    raw = asyncio.run(_read_uuids(name, [POWER_STATE_UUID]))[POWER_STATE_UUID.lower()]
    return _decode_power_state(raw)


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(read_all(sys.argv[1]), indent=2))
