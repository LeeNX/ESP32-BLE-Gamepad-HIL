"""Special buttons. start/select/menu are HID Generic-Desktop usages and land
on the gamepad node; home/back/volume* are Consumer-page usages and the kernel
routes them to a separate consumer-control input node -- so this test watches
*all* of the DUT's event nodes at once."""

import select
import time

import pytest
from evdev import ecodes

SPECIALS = {
    0: "start",
    1: "select",
    2: "menu",
    3: "home",
    4: "back",
    5: "volinc",
    6: "voldec",
    7: "volmute",
}
DESKTOP = {0, 1, 2}  # expected on the gamepad node


def _collect_all(nodes, settle=0.4, hard=2.0):
    fds = {d.fd: d for d in nodes}
    events = []
    start = last = time.time()
    while time.time() - start < hard:
        r, _, _ = select.select(list(fds), [], [], settle)
        if not r:
            break
        for fd in r:
            for e in fds[fd].read():
                if e.type == ecodes.EV_KEY:
                    events.append((fds[fd].name, e.code, e.value))
        last = time.time()
        if time.time() - last >= settle:
            break
    return events


@pytest.fixture(scope="module", autouse=True)
def _require_specials(connected_dut):
    if connected_dut.config().get("special", "none") == "none":
        pytest.skip("profile has no special buttons (use --profile specials)")


@pytest.fixture(scope="module")
def special_sweep(connected_dut, all_nodes):
    for d in all_nodes:
        try:
            while d.read_one():
                pass
        except (BlockingIOError, OSError):
            pass
    result = {}
    for idx, name in SPECIALS.items():
        _collect_all(all_nodes, settle=0.2, hard=0.5)  # drain
        connected_dut.special("PRESS", idx)
        downs = [(nn, c) for nn, c, v in _collect_all(all_nodes) if v == 1]
        connected_dut.special("RELEASE", idx)
        ups = [(nn, c) for nn, c, v in _collect_all(all_nodes) if v == 0]
        result[idx] = {"name": name, "downs": downs, "ups": ups}
    return result


def test_desktop_specials_on_gamepad_node(special_sweep, rigcfg):
    for idx in DESKTOP:
        r = special_sweep[idx]
        assert len(r["downs"]) == 1, f"{r['name']}: downs={r['downs']}"
        node_name, code = r["downs"][0]
        assert rigcfg["device_name"] in node_name
        assert [c for _, c in r["ups"]] == [code], f"{r['name']}: ups={r['ups']}"


def test_all_specials_produce_exactly_one_event(special_sweep):
    problems = [
        f"{r['name']}: {r['downs']}" for r in special_sweep.values() if len(r["downs"]) != 1
    ]
    assert not problems, "specials not producing exactly one key-down:\n" + "\n".join(problems)


def test_specials_are_one_to_one(special_sweep):
    codes = [r["downs"][0] for r in special_sweep.values() if len(r["downs"]) == 1]
    assert len(set(codes)) == len(codes), f"special buttons collide: {codes}"
