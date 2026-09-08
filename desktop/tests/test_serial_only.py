"""Serial-only HIL subset -- runs on macOS / Windows against a flashed board.

No BLE bond, no host HID stack. These assertions ride the `hil_runner` USB
serial channel only. They catch firmware regressions in descriptor generation,
report sizing, the DIS / PnP config and the serial protocol itself -- a laptop
smoke test between full Linux HIL runs.

What they can't catch: anything that needs the host's HID interpretation of the
device (button -> key code, axis -> ABS code, ...). That's the behavioural
layer, which needs a per-platform native read backend -- not here yet.
"""

import pathlib

import pytest

pytestmark = pytest.mark.serial_only

from hil import hidraw  # noqa: E402  -- decode_items only, pure stdlib

GOLDEN_DIR = pathlib.Path(__file__).resolve().parents[2] / "firmware" / "golden"

# firmware/include/hil_profile.h, mirrored. Only the fields CONFIG? reports.
ALL_AXES = ["x", "y", "z", "rx", "ry", "rz", "s1", "s2"]
PROFILE_LAYOUT = {
    "default": dict(buttons=64, hats=4, special="none", axes=ALL_AXES, axesMin=0, axesMax=0x7FFF),
    # specials also carries the signed-axis range (min -32767); minimal also
    # carries the Output + Feature reports (separate HID report types).
    "specials": dict(
        buttons=16,
        hats=1,
        axes=ALL_AXES,
        axesMin=-32767,
        axesMax=0x7FFF,
        special="start,select,menu,home,back,volinc,voldec,volmute",
    ),
    "minimal": dict(buttons=1, hats=0, special="none", axes=["x"], axesMin=0, axesMax=0x7FFF),
    "maxbtn": dict(buttons=128, hats=0, special="none", axes=[], axesMin=0, axesMax=0x7FFF),
    "local": dict(buttons=4, hats=1, special="none", axes=["x", "y"], axesMin=0, axesMax=0x7FFF),
}

# hil_profile.h HIL_VID / HIL_PID / HIL_GUID_VERSION and the HIL_DIS_* strings.
DIS_EXPECTED = {
    "model": "HIL-MODEL",
    "serial": "HIL-SN-0001",
    "hw": "HIL-HW-1",
    "sw": "HIL-SW-1",
    "mfr": "LeeNX-HIL",
}
PNP_EXPECTED = {"vid": "1D34", "pid": "8010", "ver": "0110"}


def _descriptor_diff(want, got):
    wi, gi = hidraw.decode_items(want), hidraw.decode_items(got)
    lines = [f"descriptor differs: {len(want)}B golden vs {len(got)}B device"]
    for n, (a, b) in enumerate(zip(wi, gi)):
        if a != b:
            lines.append(f"  item {n}: {a[0]}={a[1]}  ->  {b[0]}={b[1]}")
    if len(wi) != len(gi):
        lines.append(f"  item count {len(wi)} -> {len(gi)}")
    return "\n".join(lines)


# --- liveness + identity --------------------------------------------------
def test_ping(dut):
    assert dut.ping()


def test_id_reports_expected_profile(dut, profile):
    fid = dut.firmware_id()
    assert fid.startswith("ID hil_runner")
    assert f"profile={profile}" in fid


def test_advertised_name(dut, profile):
    """NAME? -- the BLE name the board advertises. <= 18 chars (fits the legacy
    advertising packet next to the HID service UUID). The `local` profile must
    NOT use the rig's "HILpad " prefix, or a dev board clashes with the rig."""
    name = dut.device_name()
    assert name, "NAME? returned nothing"
    assert len(name) <= 18, f"advertised name {name!r} is {len(name)} chars (>18)"
    if profile == "local":
        assert not name.startswith("HILpad "), f"{name!r} clashes with the reference rig namespace"


# --- configuration -------------------------------------------------------
def test_config_matches_profile(dut, profile):
    if profile not in PROFILE_LAYOUT:
        pytest.skip(f"no expected layout for profile {profile!r}")
    want = PROFILE_LAYOUT[profile]
    cfg = dut.config()
    assert cfg["profile"] == profile
    assert cfg["buttons"] == want["buttons"]
    assert cfg["hats"] == want["hats"]
    assert cfg.get("special") == want["special"]
    assert cfg["axes"] == want["axes"]
    assert cfg["axesMin"] == want["axesMin"]
    assert cfg["axesMax"] == want["axesMax"]


def test_dis_matches_firmware_constants(dut, profile):
    dis = dut.device_info()
    for k, v in DIS_EXPECTED.items():
        assert dis.get(k) == v, f"DIS {k}={dis.get(k)!r}, expected {v!r}"
    # firmware revision carries the profile (hil_profile.h)
    assert dis.get("fw") == f"HIL-FW-{profile}"


def test_pnp_matches_firmware_constants(dut):
    pnp = dut.pnp()
    assert pnp.get("vidsrc") == "1"
    for k, v in PNP_EXPECTED.items():
        assert pnp.get(k, "").upper() == v, f"PNP {k}={pnp.get(k)!r}, expected {v!r}"


# --- HID report descriptor (the library's own output, over serial) ------
def test_report_sizes_within_buffer(dut):
    sizes = dut.report_sizes()
    # BleGamepad assembles the descriptor into a fixed tempHidReportDescriptor[150]
    # with no bounds check -- begin() corrupts the stack if it overruns.
    assert 0 < sizes["descriptor"] <= 150, (
        f"descriptor {sizes['descriptor']}B nears the 150B buffer"
    )
    assert 0 < sizes["report"] <= 63, f"unexpected input report size {sizes['report']}"


def test_descriptor_size_matches_bytes(dut):
    desc = dut.report_descriptor()  # RMAP?
    assert len(desc) == dut.report_sizes()["descriptor"]
    assert len(desc) <= 150


def test_descriptor_matches_golden(dut, profile):
    golden = GOLDEN_DIR / f"{profile}.hiddesc"
    if not golden.exists():
        pytest.skip(f"no golden for profile {profile!r} (firmware/golden/{profile}.hiddesc)")
    want = bytes.fromhex(golden.read_text().strip())
    got = dut.report_descriptor()
    assert got == want, _descriptor_diff(want, got)


# --- serial protocol round-trips ---------------------------------------
def test_button_press_release_roundtrip(dut):
    cfg = dut.config()
    dut.press(1)
    dut.release(1)
    dut.press(cfg["buttons"])  # last valid button
    dut.release(cfg["buttons"])


def test_button_out_of_range_rejected(dut):
    from hil.serialdev import SerialError

    cfg = dut.config()
    with pytest.raises(SerialError):
        dut.press(cfg["buttons"] + 1)
    with pytest.raises(SerialError):
        dut.press(0)


def test_axis_roundtrip(dut):
    cfg = dut.config()
    if not cfg["axes"]:
        pytest.skip("profile has no axes")
    for name in (cfg["axes"][0], cfg["axes"][-1]):
        dut.axis(name, cfg["axesMin"])
        dut.axis(name, 0)
        dut.axis(name, cfg["axesMax"])


def test_hat_roundtrip(dut):
    cfg = dut.config()
    if not cfg["hats"]:
        pytest.skip("profile has no hats")
    for direction in range(0, 9):
        dut.hat(1, direction)
    dut.hat(1, 0)


def test_reset(dut):
    dut.reset()


def test_bonds_query(dut):
    """BONDS? parses. A board may carry stale bonds (previous host / firmware) --
    `pair-assist.py --clear-bonds` or CLEARBONDS drops them."""
    for mac in dut.bonds():
        assert len(mac.split(":")) == 6, f"not a MAC: {mac!r}"


# --- connection params (only meaningful once bonded by hand) -----------
def test_peer_info_when_connected(dut):
    if not dut.connected():
        pytest.skip(
            "board not bonded/connected -- pair once by hand in the OS BT settings for this check"
        )
    peer = dut.peer_info()
    assert 5 <= peer["interval_ms"] <= 100
    assert peer["mtu"] >= 23
