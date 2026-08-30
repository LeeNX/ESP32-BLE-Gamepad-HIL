"""Read the DUT's GATT side -- Device Information (0x180A), Battery (0x180F),
PnP ID (0x2A50), Battery Power State (0x2A1A) -- over a plain BLE GATT client.

Unlike the HID service (which BlueZ's input plugin claims and hides, see
LinuxHIDTesting.md), these services stay visible to a generic GATT client even
while the device is bonded and bridged into the kernel, so bleak can read them.
"""

import asyncio
import re
import subprocess

# 16-bit GATT UUIDs, normalised to the full base-UUID form bleak wants.
def _u(short):
    return f"0000{short:04x}-0000-1000-8000-00805f9b34fb"

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
    if len(raw) < 7:
        return {"_raw": raw.hex()}
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


async def _read_all(mac):
    from bleak import BleakClient  # lazy: only GATT tests need bleak

    out = {"device_info": {}, "pnp": None, "battery_level": None, "power_state": None}
    async with BleakClient(mac, timeout=20.0) as client:
        for key, uuid in DIS_CHARS.items():
            try:
                out["device_info"][key] = (await client.read_gatt_char(uuid)).decode(
                    "utf-8", "replace").strip("\x00").strip()
            except Exception:
                out["device_info"][key] = None
        try:
            out["pnp"] = _decode_pnp(await client.read_gatt_char(PNP_UUID))
        except Exception:
            pass
        try:
            out["battery_level"] = (await client.read_gatt_char(BATTERY_LEVEL_UUID))[0]
        except Exception:
            pass
        try:
            out["power_state"] = _decode_power_state(
                await client.read_gatt_char(POWER_STATE_UUID))
        except Exception:
            pass
    return out


def read_all(mac):
    """Blocking: return {device_info, pnp, battery_level, power_state}."""
    return asyncio.run(_read_all(mac))


async def _read_chars(mac, uuids):
    from bleak import BleakClient

    out = {}
    async with BleakClient(mac, timeout=20.0) as client:
        for name, uuid in uuids.items():
            try:
                out[name] = await client.read_gatt_char(uuid)
            except Exception:
                out[name] = None
    return out


def read_battery_level(mac):
    """Blocking single-char read of 0x2A19; int percentage or None."""
    raw = asyncio.run(_read_chars(mac, {"b": BATTERY_LEVEL_UUID}))["b"]
    return raw[0] if raw else None


def read_power_state(mac):
    """Blocking read + decode of 0x2A1A; None if the characteristic is absent."""
    raw = asyncio.run(_read_chars(mac, {"p": POWER_STATE_UUID}))["p"]
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
        info = subprocess.run(["upower", "-i", match.strip()],
                              capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"percentage:\s*([0-9.]+)", info)
        return round(float(m.group(1))) if m else None
    except Exception:
        return None


if __name__ == "__main__":
    import json
    import sys

    mac = sys.argv[1]
    data = read_all(mac)
    data["battery_upower"] = read_battery_upower(mac)
    print(json.dumps(data, indent=2))
