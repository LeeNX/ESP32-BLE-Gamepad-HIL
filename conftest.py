"""Fixtures for the ESP32-BLE-Gamepad hardware-in-the-loop suite.

Fixture chain (all session-scoped):
    rigcfg -> firmware -> dut -> connected_dut -> bt_mac -> gamepad
An autouse per-test fixture resets the pad and drains pending events.
"""

import fcntl
import json
import os
import pathlib
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

REPO = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(REPO / "host"))
from hil import bluetooth  # noqa: E402
from hil.config import load as load_config  # noqa: E402
from hil.evdev_utils import Capture, find_all_nodes, find_gamepad  # noqa: E402
from hil.serialdev import SerialDev  # noqa: E402

STATE = pathlib.Path.home() / ".cache" / "esp32-hil" / "state.json"


def pytest_addoption(parser):
    parser.addoption("--board", default=os.environ.get("HIL_BOARD", "esp32dev"))
    parser.addoption(
        "--port", default=os.environ.get("HIL_PORT"), help="serial command channel to hil_runner"
    )
    parser.addoption(
        "--flash-port",
        default=os.environ.get("HIL_FLASH_PORT"),
        help="port esptool/pio flash on, if different from --port "
        "(esp32-c3: native USB to flash, UART bridge to talk)",
    )
    parser.addoption("--profile", default=os.environ.get("HIL_PROFILE"))
    parser.addoption(
        "--no-flash",
        action="store_true",
        help="skip building/flashing; use firmware already on the board",
    )
    parser.addoption(
        "--bundle",
        default=os.environ.get("HIL_BUNDLE"),
        help="flash a prebuilt firmware bundle (builder/make_bundle.py) "
        "with esptool instead of running PlatformIO",
    )
    parser.addoption(
        "--no-pair", action="store_true", help="assume the DUT is already bonded+connected"
    )
    parser.addoption("--repair", action="store_true", help="drop the existing bond and pair fresh")
    parser.addoption(
        "--bench",
        action="store_true",
        help="run the slow latency/throughput benchmark tests "
        "(test_latency.py) and let bench.py record results",
    )
    parser.addoption(
        "--bench-quick",
        action="store_true",
        help="with --bench: run the short sweep (n=40, 3 gap values, ~2 min "
        "vs ~6) -- for a quick check when you don't need full rigour",
    )
    parser.addoption(
        "--update-golden",
        action="store_true",
        help="rewrite firmware/golden/<profile>.hiddesc from the "
        "live device instead of asserting against it",
    )


# --- config -----------------------------------------------------------------
@pytest.fixture(scope="session")
def rigcfg(pytestconfig):
    cfg = load_config(REPO)
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
    # flash_port defaults to the command port -- only boards whose flash channel
    # and command channel are physically different interfaces set it (esp32-c3
    # with an external UART bridge; see README "ESP32-C3 serial bridge").
    b["flash_port"] = pytestconfig.getoption("flash_port") or b.get("flash_port") or b["port"]
    # The name the firmware advertises with no --name / $HIL_DEVICE_NAME override:
    # "<rig.device_name> <board>", except the `local` dev profile which uses
    # "HILdev <board>" (firmware/include/hil_profile.h HIL_DEVICE_NAME). An
    # actual override is carried in the bundle manifest -- see the `manifest`
    # fixture and test_connection.py::test_advertised_name.
    prefix = "HILdev" if b.get("profile") == "local" else cfg["rig"]["device_name"]
    b["device_name"] = f"{prefix} {board}"
    for key in ("port", "flash_port"):
        if "CHANGE-ME" in b[key]:
            pytest.exit(
                f"set {key} for board '{board}' in hil_config.toml "
                f"(or pass --{key.replace('_', '-')}); got {b[key]!r}"
            )
    return b


# --- firmware --------------------------------------------------------------
PROFILE_ENV_SUFFIX = {
    "default": "",
    "specials": "-specials",
    "minimal": "-minimal",
    "maxbtn": "-maxbtn",
    "local": "-local",  # ad-hoc dev profile -- not in CI; see firmware/include/hil_profile.h
}


def _flash_bundle(bundle_dir, port):
    """TESTER path: flash a prebuilt bundle with esptool (no PlatformIO)."""
    bundle = pathlib.Path(bundle_dir)
    manifest = json.loads((bundle / "manifest.json").read_text())
    cmd = [sys.executable, str(REPO / "tester" / "flash.py"), str(bundle), "--port", port]
    print(
        f"\n[firmware] bundle {bundle.name}  ({manifest['lib_describe']}, "
        f"profile={manifest['profile']})"
    )
    r = subprocess.run(cmd)
    if r.returncode != 0:
        pytest.exit(f"flashing bundle {bundle} failed")
    return manifest


@pytest.fixture(scope="session")
def firmware(rigcfg, pytestconfig):
    env_name = rigcfg["pio_env"] + PROFILE_ENV_SUFFIX.get(rigcfg["profile"], "")
    bundle = pytestconfig.getoption("bundle")

    if pytestconfig.getoption("no_flash"):
        return env_name  # --no-flash wins over everything

    if bundle:
        manifest = _flash_bundle(bundle, rigcfg["flash_port"])
        if manifest["board"] != rigcfg["name"] or manifest["profile"] != rigcfg["profile"]:
            pytest.exit(
                f"bundle is {manifest['board']}/{manifest['profile']}, "
                f"expected {rigcfg['name']}/{rigcfg['profile']}"
            )
        time.sleep(2)
        return env_name

    pio = rigcfg["rig"]["pio"]
    cmd = [
        pio,
        "run",
        "-e",
        env_name,
        "-t",
        "upload",
        "--upload-port",
        rigcfg["flash_port"],
        "-d",
        str(REPO / "firmware"),
    ]
    # platformio.ini resolves the library-under-test via symlink://${sysenv.HIL_LIB_DIR}
    env = {**os.environ, "HIL_LIB_DIR": rigcfg["rig"].get("lib_dir", "")}
    print(f"\n[firmware] {' '.join(cmd)}")
    # Native USB-Serial/JTAG (C3/S3) uploads are flaky -- "Packet content
    # transfer stopped" -- and usually succeed on a retry.
    for attempt in range(3):
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode == 0:
            break
        print(f"[firmware] upload attempt {attempt + 1} failed, retrying")
        time.sleep(3)
    else:
        pytest.exit(f"firmware build/upload failed:\n{r.stdout[-4000:]}\n{r.stderr[-2000:]}")
    time.sleep(2)  # let the board reboot into the new image
    return env_name


@pytest.fixture(scope="session")
def manifest(pytestconfig):
    """The flashed bundle's manifest.json, or {} when building from source /
    --no-flash. Tests read optional keys (e.g. `device_name`, set only when the
    advertised BLE name was overridden at build time)."""
    bundle = pytestconfig.getoption("bundle")
    if not bundle:
        return {}
    return json.loads((pathlib.Path(bundle) / "manifest.json").read_text())


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
        pytest.exit(
            f"firmware profile {cfg.get('profile')!r} != expected {want!r} "
            f"(flash the right env or fix hil_config.toml)"
        )
    yield d
    d.close()


@pytest.fixture(scope="session")
def connected_dut(dut):
    dut.begin()
    time.sleep(1.0)
    # begin() builds the HID report descriptor into a fixed 150-byte buffer with
    # no bounds check -- an over-large descriptor corrupts the stack here. If the
    # firmware stops answering right after BEGIN, that's the most likely cause.
    if not dut.ping():
        pytest.exit(
            "firmware stopped responding right after BEGIN -- likely a HID "
            "report descriptor overflow in bleGamepad.begin() for this profile"
        )
    return dut


# --- bluetooth bond ------------------------------------------------------
def _load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


@contextmanager
def _flock(name):
    """Cross-process mutex (POSIX flock -- Linux tester, macOS one-box). Used to
    serialise the two things parallel per-board runs (tester/test-all.sh
    --by-board) must not do at once on the shared adapter: writing state.json,
    and pairing (BlueZ discovery + `remove`/`pair` are adapter-global)."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE.with_name(name), "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        yield


def _merge_state(name, entry):
    """Set one device's record without clobbering a concurrent writer's."""
    with _flock("state.lock"):
        s = _load_state()
        s[name] = entry
        STATE.write_text(json.dumps(s, indent=2))


@pytest.fixture(scope="session")
def bt_mac(rigcfg, connected_dut, pytestconfig):
    name = rigcfg["device_name"]
    state = _load_state()
    prev = state.get(name, {})
    # A profile change alters the HID report descriptor, which the host has
    # cached against the old bond -> must re-pair. So does a state record that
    # doesn't confirm the current profile (or ensure_paired finding an
    # untracked bond).
    want_fresh = pytestconfig.getoption("repair") or (
        bool(prev) and prev.get("profile") != rigcfg["profile"]
    )

    if pytestconfig.getoption("no_pair") and prev.get("mac") and not want_fresh:
        return prev["mac"]  # no BtCtl at all -- lets --by-board lanes stay independent

    # One board pairs at a time: `remove`/`pair` and BlueZ discovery are
    # adapter-global, and a second bluetoothctl agent confuses bluetoothd
    # (org.bluez.Error.InProgress). So the BtCtl session lives *entirely* inside
    # the lock -- only one agent exists at any moment across parallel lanes.
    with _flock("pair.lock"), bluetooth.BtCtl() as btctl:
        mac = bluetooth.ensure_paired(btctl, name, known_mac=prev.get("mac"), want_fresh=want_fresh)
        connected_dut.wait_connected()
    _merge_state(name, {"mac": mac, "profile": rigcfg["profile"]})
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


def _evdev_alive(dev):
    try:
        dev.capabilities()
        return True
    except OSError:
        return False


def _recover_link(rigcfg, connected_dut, cap, mac):
    """A transient BLE drop takes the evdev node with it (ENODEV). BlueZ
    auto-reconnects a trusted bond; nudge it and re-acquire the node."""
    name = rigcfg["device_name"]
    print(f"\n[recover] evdev node gone -- reconnecting {mac}")
    for _ in range(20):
        if not bluetooth.is_connected(mac):
            # short-lived BtCtl under the lock -- one agent at a time (see bt_mac)
            with _flock("pair.lock"), bluetooth.BtCtl() as btctl:
                btctl.connect(mac)
        try:
            connected_dut.wait_connected(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        try:
            dev = find_gamepad(name, timeout=5)
        except Exception:  # noqa: BLE001
            time.sleep(2)
            continue
        cap.dev.close() if cap.dev else None
        cap.dev = dev
        print(f"[recover] back on {dev.path}")
        return
    pytest.fail(f"lost the BLE link to {mac} and could not reconnect")


@pytest.fixture(scope="session")
def all_nodes(rigcfg, bt_mac):
    nodes = find_all_nodes(rigcfg["device_name"])
    yield nodes
    for n in nodes:
        n.close()


# --- GATT / Device Information -------------------------------------------
@pytest.fixture(scope="session")
def gatt():
    """The hil.gatt module, with dbus-fast confirmed importable (skip otherwise)."""
    from hil import gatt as _gatt

    try:
        import dbus_fast  # noqa: F401
    except ImportError as e:
        pytest.skip(f"dbus-fast not installed -- GATT reads unavailable: {e}")
    return _gatt


@pytest.fixture(scope="session")
def device_info(gatt, bt_mac):
    """DIS strings + PnP + battery + power-state, read over GATT (not HID)."""
    return gatt.read_all(bt_mac)


# --- benchmark gate -----------------------------------------------------
@pytest.fixture(scope="session")
def bench_enabled(pytestconfig):
    if not pytestconfig.getoption("bench"):
        pytest.skip("latency/throughput benchmark is opt-in; pass --bench")
    return True


# --- per-test reset --------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset(request):
    fx = request.fixturenames
    if "connected_dut" in fx and "gamepad" in fx:
        dut = request.getfixturevalue("connected_dut")
        _, cap = request.getfixturevalue("gamepad")
        if not _evdev_alive(cap.dev):
            _recover_link(
                request.getfixturevalue("rigcfg"),
                dut,
                cap,
                request.getfixturevalue("bt_mac"),
            )
        dut.reset()
        cap.drain()
    yield
