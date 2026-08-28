"""Fixtures for the ESP32-BLE-Gamepad hardware-in-the-loop suite.

Fixture chain (all session-scoped):
    rigcfg -> firmware -> dut -> connected_dut -> bt_mac -> gamepad
An autouse per-test fixture resets the pad and drains pending events.
"""

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

REPO = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "host"))
from hil import bluetooth  # noqa: E402
from hil.evdev_utils import Capture, find_all_nodes, find_gamepad  # noqa: E402
from hil.serialdev import SerialDev  # noqa: E402

STATE = pathlib.Path.home() / ".cache" / "esp32-hil" / "state.json"


def pytest_addoption(parser):
    parser.addoption("--board", default=os.environ.get("HIL_BOARD", "esp32dev"))
    parser.addoption("--port", default=os.environ.get("HIL_PORT"))
    parser.addoption("--profile", default=os.environ.get("HIL_PROFILE"))
    parser.addoption("--no-flash", action="store_true",
                     help="skip building/flashing; use firmware already on the board")
    parser.addoption("--no-pair", action="store_true",
                     help="assume the DUT is already bonded+connected")
    parser.addoption("--repair", action="store_true",
                     help="drop the existing bond and pair fresh")


# --- config -----------------------------------------------------------------
@pytest.fixture(scope="session")
def rigcfg(pytestconfig):
    cfg = tomllib.loads((REPO / "hil_config.toml").read_text())
    board = pytestconfig.getoption("board")
    if board not in cfg.get("board", {}):
        pytest.exit(f"unknown board {board!r}; known: {list(cfg.get('board', {}))}")
    b = dict(cfg["board"][board])
    b["name"] = board
    b["rig"] = cfg["rig"]
    if pytestconfig.getoption("port"):
        b["port"] = pytestconfig.getoption("port")
    if pytestconfig.getoption("profile"):
        b["profile"] = pytestconfig.getoption("profile")
    b["device_name"] = f"{cfg['rig']['device_name']} {board}"
    if "CHANGE-ME" in b["port"]:
        pytest.exit(f"set the serial port for board '{board}' in hil_config.toml "
                    f"(or pass --port); got {b['port']!r}")
    return b


# --- firmware --------------------------------------------------------------
@pytest.fixture(scope="session")
def firmware(rigcfg, pytestconfig):
    env_name = rigcfg["pio_env"]
    if rigcfg["profile"] == "signed-axes":
        env_name += "-signed"
    if pytestconfig.getoption("no_flash"):
        return env_name
    pio = rigcfg["rig"]["pio"]
    cmd = [pio, "run", "-e", env_name, "-t", "upload",
           "--upload-port", rigcfg["port"], "-d", str(REPO / "firmware")]
    print(f"\n[firmware] {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.exit(f"firmware build/upload failed:\n{r.stdout[-4000:]}\n{r.stderr[-2000:]}")
    time.sleep(2)  # let the board reboot into the new image
    return env_name


# --- serial DUT -----------------------------------------------------------
@pytest.fixture(scope="session")
def dut(rigcfg, firmware):
    d = SerialDev(rigcfg["port"])
    d.wait_ready()
    fid = d.firmware_id()
    print(f"[dut] {fid}")
    cfg = d.config()
    print(f"[dut] CONFIG raw: {cfg.get('_raw')!r}")
    want = rigcfg["profile"]
    if cfg.get("profile") != want:
        d.close()
        pytest.exit(f"firmware profile {cfg.get('profile')!r} != expected {want!r} "
                    f"(flash the right env or fix hil_config.toml)")
    yield d
    d.close()


@pytest.fixture(scope="session")
def connected_dut(dut):
    dut.begin()
    return dut


# --- bluetooth bond ------------------------------------------------------
def _load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


@pytest.fixture(scope="session")
def bt_mac(rigcfg, connected_dut, pytestconfig):
    name = rigcfg["device_name"]
    state = _load_state()
    prev = state.get(name, {})
    # A profile change alters the HID report descriptor, which the host has
    # cached against the old bond -> must re-pair.
    want_fresh = (pytestconfig.getoption("repair")
                  or prev.get("profile") not in (None, rigcfg["profile"]))

    if pytestconfig.getoption("no_pair") and prev.get("mac") and not want_fresh:
        return prev["mac"]

    mac = bluetooth.ensure_paired(name, known_mac=prev.get("mac"), want_fresh=want_fresh)
    connected_dut.wait_connected()
    state[name] = {"mac": mac, "profile": rigcfg["profile"]}
    _save_state(state)
    print(f"[bt] {name} -> {mac}")
    return mac


# --- evdev node ---------------------------------------------------------
@pytest.fixture(scope="session")
def gamepad(rigcfg, bt_mac):
    name = rigcfg["device_name"]
    dev = find_gamepad(name)
    print(f"[evdev] {dev.path}  {dev.name}")
    cap = Capture(dev)
    yield dev, cap
    for n in find_all_nodes(name):
        n.close()


@pytest.fixture(scope="session")
def all_nodes(rigcfg, bt_mac):
    nodes = find_all_nodes(rigcfg["device_name"])
    yield nodes
    for n in nodes:
        n.close()


# --- per-test reset --------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset(request):
    if "connected_dut" in request.fixturenames and "gamepad" in request.fixturenames:
        request.getfixturevalue("connected_dut").reset()
        request.getfixturevalue("gamepad")[1].drain()
    yield
