import asyncio
import time

import pytest
from conftest import build

from app.ultimate import joybridge as jb
from app.ultimate.input import InputController, InputUnsupported
from app.ultimate.joybridge import JoyBridge, mask_of, packet, parse_pong
from app.ultimate.joybridge_fake import serve


async def _bridge_pair(ports=(2,)):
    transport, fake = await serve("127.0.0.1", 0, ports=ports)
    port = transport.get_extra_info("sockname")[1]
    bridge = JoyBridge()
    await bridge.configure("127.0.0.1", port)
    for _ in range(100):
        if bridge.online:
            break
        await asyncio.sleep(0.01)
    return transport, fake, bridge


async def _settle():
    await asyncio.sleep(0.03)


def test_mask_and_packet_format():
    assert mask_of(["up", "fire"]) == 0x11
    with pytest.raises(ValueError):
        mask_of(["fire2"])
    p = packet(jb.T_STATE, 0x1234, bytes([2, 0x11]))
    assert p[:4] == b"C64J" and p[4] == 1 and p[5] == jb.T_STATE and p[6:8] == b"\x34\x12" and p[8:] == bytes([2, 0x11])
    assert parse_pong(b"junk") is None


async def test_bridge_comes_online_and_reports_ports():
    transport, fake, bridge = await _bridge_pair(ports=(1, 2))
    try:
        assert bridge.online
        st = bridge.status()
        assert st["ports"] == [1, 2] and st["name"] == "fake-joybridge" and st["firmware"] == "1.0"
        assert st["rttMs"] is not None
    finally:
        await bridge.close()
        transport.close()


async def test_press_release_tap_reach_the_port():
    transport, fake, bridge = await _bridge_pair()
    try:
        await bridge.press(2, ["up", "fire"])
        await _settle()
        assert fake.masks[2] == 0x11
        await bridge.release(2, ["up"])
        await _settle()
        assert fake.masks[2] == 0x10
        await bridge.set(2, [])
        await bridge.tap(2, ["left"], 0.05)
        await _settle()
        assert (2, 0x04) in fake.history and fake.masks[2] == 0
        with pytest.raises(ValueError):
            await bridge.press(1, ["up"])  # not wired to port 1
    finally:
        await bridge.close()
        transport.close()


async def test_held_state_is_kept_alive_then_failsafe_releases():
    transport, fake, bridge = await _bridge_pair()
    try:
        await bridge.press(2, ["right"])
        await asyncio.sleep(0.6)  # longer than the 0.35 s failsafe: keepalives must hold it
        assert fake.masks[2] == 0x08
        bridge._task.cancel()  # console "crashes": no more keepalives
        await asyncio.sleep(0.6)
        assert fake.masks[2] == 0
    finally:
        await bridge.close()
        transport.close()


async def test_out_of_order_state_is_ignored():
    transport, fake = await serve("127.0.0.1", 0)
    addr = transport.get_extra_info("sockname")
    loop = asyncio.get_running_loop()
    sender, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=addr)
    try:
        sender.sendto(packet(jb.T_STATE, 10, bytes([2, 0x01])))
        await _settle()
        sender.sendto(packet(jb.T_STATE, 9, bytes([2, 0x02])))  # older: dropped
        await _settle()
        assert fake.masks[2] == 0x01
        sender.sendto(packet(jb.T_STATE, 11, bytes([2, 0x00])))
        await _settle()
        assert fake.masks[2] == 0
    finally:
        sender.close()
        transport.close()


async def test_input_controller_prefers_bridge_on_legacy_firmware():
    transport, fake, bridge = await _bridge_pair()
    try:
        sim, client, caps, _ = await build("legacy")
        inputs = InputController(client, caps, type_delay_ms=0, bridge=bridge)
        assert inputs.status()["joystickVia"] == "bridge" and inputs.status()["joystickSupported"]
        await inputs.press_joystick(["down"], 2)
        await _settle()
        assert fake.masks[2] == 0x02 and inputs.held
        await inputs.set_joystick(["left", "fire"], 2)
        await _settle()
        assert fake.masks[2] == 0x14 and {h.inputs[0] for h in inputs.held.values()} == {"left", "fire"}
        assert await inputs.release_all() is True
        await _settle()
        assert fake.masks[2] == 0 and not inputs.held
        with pytest.raises(ValueError):
            await inputs.press_joystick(["fire2"], 2)
    finally:
        await bridge.close()
        transport.close()


async def test_no_bridge_on_legacy_firmware_is_unsupported():
    sim, client, caps, _ = await build("legacy")
    bridge = JoyBridge()
    bridge.host = "192.0.2.1"  # configured but silent
    inputs = InputController(client, caps, type_delay_ms=0, bridge=bridge)
    assert inputs.status()["joystickVia"] is None
    with pytest.raises(InputUnsupported, match="not responding"):
        await inputs.tap_joystick(["fire"], 2)


async def test_rest_set_joystick_diffs_press_and_release():
    sim, _, _, inputs = await build("modern")
    await inputs.set_joystick(["up", "fire"], 2)
    assert sim.held_joy[2] == {"up", "fire"}
    await inputs.set_joystick(["fire"], 2)
    assert sim.held_joy[2] == {"fire"}
    await inputs.set_joystick([], 2)
    assert sim.held_joy[2] == set() and not inputs.held


async def test_discover_finds_fake_bridge(monkeypatch):
    transport, fake = await serve("127.0.0.1", 0)
    port = transport.get_extra_info("sockname")[1]
    monkeypatch.setattr(jb, "_subnet_broadcasts", lambda: ["127.0.0.1"])
    try:
        t = time.monotonic()
        found = await jb.discover(timeout=0.3, port=port)
        assert time.monotonic() - t < 2
        assert any(f["host"] == "127.0.0.1" and f["ports"] == [2] for f in found)
    finally:
        transport.close()


def test_api_status_and_offline_test(app_client):
    client = app_client()
    with client:
        st = client.get("/api/joybridge").json()
        assert st["configured"] is False and st["online"] is False
        assert client.post("/api/joybridge/test").status_code == 503
        assert client.get("/api/device/status").status_code in (200, 404)
