"""Fixtures for the desktop (macOS / Windows) HIL subset.

`dut` (serial command channel) is the portable slice of the Linux rig -- it
needs no BLE bond and no host HID stack, and reuses the rig's
`hil.serialdev.SerialDev` / `hil.hidraw` verbatim (../host is on the path).

`hidgamepad` (raw HID input reports, hidapi) needs the board **bonded to this
host** -- run `pair-assist.py` once first. It reads the reports the firmware
sends over BLE; the `hid`-marked tests decode them against the report layout.

    dut         a ready SerialDev, firmware profile confirmed
    hidgamepad  the DUT's HID device, opened raw (skips if not bonded / no access)

Point it at a board with --port (or $HIL_PORT); flash first with --bundle.
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "host"))
from hidgamepad import drain, read_after  # noqa: E402  -- desktop/, on pythonpath

from hil.hidraw import HIL_PID, HIL_VID  # noqa: E402
from hil.serialdev import SerialDev  # noqa: E402

GOLDEN_DIR = REPO / "firmware" / "golden"
FLASH_PY = REPO / "tester" / "flash.py"


def pytest_addoption(parser):
    parser.addoption("--port", default=os.environ.get("HIL_PORT"), help="hil_runner serial port")
    parser.addoption(
        "--flash-port",
        default=os.environ.get("HIL_FLASH_PORT"),
        help="port esptool flashes on, if different from --port",
    )
    parser.addoption(
        "--profile",
        default=os.environ.get("HIL_PROFILE", "default"),
        help="expected firmware profile (default: default)",
    )
    parser.addoption(
        "--bundle",
        default=os.environ.get("HIL_BUNDLE"),
        help="flash this bundle dir with esptool before the run",
    )
    parser.addoption(
        "--no-flash",
        action="store_true",
        help="use whatever firmware is already on the board (ignore --bundle)",
    )


@pytest.fixture(scope="session")
def profile(pytestconfig):
    return pytestconfig.getoption("profile")


@pytest.fixture(scope="session")
def port(pytestconfig):
    p = pytestconfig.getoption("port")
    if not p:
        pytest.exit(
            "no serial port -- pass --port (macOS: /dev/cu.usbserial-*, Windows: COM5) or set $HIL_PORT"
        )
    return p


@pytest.fixture(scope="session")
def flashed(pytestconfig, port, profile):
    """Flash --bundle with esptool (reusing tester/flash.py) unless --no-flash.

    esptool output is streamed live (capture is suspended for it) -- a flash is
    a ~10s hardware operation and silence looks like a hang.
    """
    bundle = pytestconfig.getoption("bundle")
    if pytestconfig.getoption("no_flash"):
        print("\n[flash] --no-flash: using the firmware already on the board")
        return None
    if not bundle:
        print(
            "\n[flash] no --bundle: using the firmware already on the board "
            "(pass --bundle=<dir> to flash one)"
        )
        return None

    bundle = pathlib.Path(bundle).expanduser()
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest["profile"] != profile:
        pytest.exit(f"bundle profile {manifest['profile']!r} != expected {profile!r}")
    flash_port = pytestconfig.getoption("flash_port") or port

    cmd = [sys.executable, str(FLASH_PY), str(bundle), "--port", flash_port]
    capmgr = pytestconfig.pluginmanager.getplugin("capturemanager")
    print(f"\n[flash] {bundle.name} -> {flash_port}")
    with capmgr.global_and_fixture_disabled():
        r = subprocess.run(cmd)
    if r.returncode != 0:
        pytest.exit(f"flashing {bundle} failed (exit {r.returncode}) -- see esptool output above")
    print(
        f"[flash] ok: {manifest['board']}/{manifest['profile']} {manifest.get('lib_describe', '')}"
    )
    return manifest


@pytest.fixture(scope="session")
def dut(port, profile, flashed):
    d = SerialDev(port)
    d.wait_ready()
    print(f"\n[dut] {d.firmware_id()}")
    got = d.config().get("profile")
    if got != profile:
        d.close()
        pytest.exit(
            f"firmware profile {got!r} != expected {profile!r} -- "
            f"flash the right bundle (--bundle) or pass --profile {got}"
        )
    yield d
    d.close()


@pytest.fixture(scope="session")
def hidgamepad(dut):
    """The DUT's HID device opened for raw input reports (hidapi).

    Skips unless the board is bonded to this host (`pair-assist.py`) and its
    reports actually reach us -- on macOS that can need Input Monitoring granted
    to this Python (System Settings > Privacy & Security > Input Monitoring).
    """
    hid = pytest.importorskip("hid", reason="pip install hidapi")

    if not dut.connected():
        pytest.skip("board not bonded/connected -- run  python pair-assist.py --port <port>  first")

    h = hid.device()
    try:
        h.open(HIL_VID, HIL_PID)
    except OSError as e:
        pytest.skip(f"can't open the DUT HID device ({e}) -- bonded? Input Monitoring granted?")
    h.set_nonblocking(True)

    # Prove reports flow: a stray press must produce at least one report.
    dut.reset()
    drain(h)
    dut.press(1)
    got = read_after(h)
    dut.release(1)
    drain(h)
    if not got:
        h.close()
        pytest.skip(
            "opened the HID device but no input reports arrived -- on macOS grant "
            "Input Monitoring to this Python and re-run"
        )

    yield h
    h.close()


@pytest.fixture(scope="session")
def sdlpad(dut):
    """The DUT as an SDL joystick (pygame) -- "what a game sees".

    Skips unless the board is bonded to this host (`pair-assist.py`) and SDL
    enumerates it. Headless (SDL_VIDEODRIVER=dummy); a game controller needs no
    macOS TCC grant.
    """
    pytest.importorskip("pygame", reason="pip install pygame")
    import sdlgamepad

    if not dut.connected():
        pytest.skip("board not bonded/connected -- run  python pair-assist.py --port <port>  first")

    sdlgamepad.init()
    j = sdlgamepad.find(HIL_VID, HIL_PID)
    if j is None:
        pytest.skip("SDL does not enumerate the DUT -- bonded? try re-plugging / re-pairing")
    print(
        f"\n[sdl] {j.get_name()!r} guid={j.get_guid()} "
        f"buttons={j.get_numbuttons()} axes={j.get_numaxes()} hats={j.get_numhats()}"
    )
    yield j
    import pygame

    pygame.quit()
