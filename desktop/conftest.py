"""Fixtures for the desktop (macOS / Windows) serial-only HIL subset.

This is the portable slice of the Linux rig: everything that rides the
`hil_runner` USB-serial command channel and needs no BLE pairing and no host
HID stack. It reuses the rig's `hil.serialdev.SerialDev` and `hil.hidraw`
verbatim (../host is on the path) and the checked-in golden descriptors under
../firmware/golden.

    dut  ->  a ready SerialDev, firmware profile confirmed

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
