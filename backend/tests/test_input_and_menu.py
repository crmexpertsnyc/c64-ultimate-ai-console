import pytest
from conftest import build

from app.ultimate.client import UltimateError
from app.ultimate.input import InputUnsupported, LegacyStateError, resolve_key
from app.ultimate.menu import MenuController, MenuFormatError, detect_encoding, parse_menu_screen


def screen_text(sim) -> str:
    return "\n".join(sim.lines)


async def test_rest_typing_reaches_editor():
    sim, _, _, inputs = await build("modern")
    await inputs.type_text('PRINT "HI"')
    assert 'PRINT "HI"' in screen_text(sim)


async def test_rest_press_release_and_release_all():
    sim, _, _, inputs = await build("modern")
    await inputs.press_joystick(["up", "fire"], 2)
    assert sim.held_joy[2] == {"up", "fire"}
    assert len(inputs.held) == 2
    await inputs.release_joystick(["up"], 2)
    assert sim.held_joy[2] == {"fire"}
    await inputs.press_key("shift")
    await inputs.release_all()
    assert sim.held_joy[2] == set() and sim.held_keys == set() and not inputs.held


async def test_failure_triggers_release_all(monkeypatch):
    sim, client, _, inputs = await build("modern")
    await inputs.press_joystick(["left"], 1)
    original = client.send_input
    calls = []

    async def flaky(events):
        calls.append(events)
        if events[0].get("kind") != "release_all":
            raise UltimateError("machine.input", 500, ["boom"])
        return await original(events)

    monkeypatch.setattr(client, "send_input", flaky)
    with pytest.raises(UltimateError):
        await inputs.tap_joystick(["fire"], 1)
    assert calls[-1] == [{"kind": "release_all"}]
    assert sim.held_joy[1] == set()


async def test_invalid_inputs_rejected():
    _, _, _, inputs = await build("modern")
    with pytest.raises(ValueError):
        await inputs.tap_joystick(["jump"], 2)
    with pytest.raises(ValueError):
        await inputs.tap_joystick(["fire"], 3)
    with pytest.raises(ValueError):
        resolve_key("hyperspace")
    with pytest.raises(ValueError):
        await inputs.type_text("emoji 🙂")


async def test_legacy_typing_uses_keyboard_buffer():
    sim, _, _, inputs = await build("legacy")
    await inputs.legacy_type_text("PRINT 42\r")
    assert "PRINT 42" in screen_text(sim)
    assert sim.mem[0xC6] == 0


async def test_legacy_refuses_when_program_owns_machine():
    sim, client, _, inputs = await build("legacy")
    await client.run_prg("/Usb0/game.prg")  # program installs its own IRQ handler
    before = bytes(sim.mem[0x0277:0x0281])
    with pytest.raises(LegacyStateError):
        await inputs.legacy_type_text("RUN\r")
    assert bytes(sim.mem[0x0277:0x0281]) == before  # nothing written


async def test_legacy_marks_joystick_and_holds_unsupported():
    _, _, _, inputs = await build("legacy")
    with pytest.raises(InputUnsupported):
        await inputs.tap_joystick(["fire"], 2)
    with pytest.raises(InputUnsupported):
        await inputs.press_key("a")
    with pytest.raises(InputUnsupported):
        await inputs.tap_key("run_stop")
    assert await inputs.release_all() is False


async def test_legacy_load_command_queues_run():
    sim, client, _, inputs = await build("legacy")
    await client.mount("a", "/Usb0/games/Bruce Lee.d64")
    await inputs.legacy_load_command(run=True)
    import asyncio
    for _ in range(40):
        await asyncio.sleep(0.02)
        await client.version()  # advance simulated time
        if sim.running:
            break
    assert sim.running == "Bruce Lee"


# ------------------------------------------------------------------- menu
def _screen(chars: bytes, colors: bytes) -> bytes:
    return chars.ljust(1000, b" ") + colors.ljust(1000, bytes([0x6E]))


def test_parse_menu_ascii_and_selection():
    rows = [b"  Usb0".ljust(40), b"  Flash".ljust(40), bytes(0x80 | c for c in b"  Temp".ljust(20)) + b" " * 20]
    screen = parse_menu_screen(_screen(b"".join(rows), b""))
    assert screen.encoding == "ascii"
    assert screen.lines[0].strip() == "Usb0"
    assert screen.selected_row == 2 and screen.selected_text == "Temp"
    assert screen.cells[2][2].reverse and screen.cells[0][2].fg == 0x0E and screen.cells[0][2].bg == 0x06


def test_parse_menu_screencodes():
    # "HELLO" in C64 screen codes (H=8, E=5, L=12, O=15)
    chars = bytes([8, 5, 12, 12, 15]) + bytes([0x20]) * 995
    screen = parse_menu_screen(_screen(chars, b""))
    assert detect_encoding(chars) == "screencode"
    assert screen.lines[0].startswith("HELLO")


def test_parse_menu_rejects_wrong_length():
    with pytest.raises(MenuFormatError):
        parse_menu_screen(b"\x00" * 1999)


async def test_menu_closed_loop():
    sim, client, caps, inputs = await build("modern")
    menu = MenuController(client, caps, inputs, poll_interval=0.001)
    assert await menu.read() is None
    opened = await menu.open()
    assert opened["verified"] and sim.menu_open
    r = await menu.navigate("down")
    assert r["changed"] and r["after"]["selectedText"] == "Flash"
    r = await menu.navigate("up")
    r = await menu.navigate("up")  # already at top: no change, and not retried
    assert r["changed"] is False and "not retried" in r["note"]
    sent_before = len([c for c in sim.calls if c[1] == "/v1/machine:input"])
    await menu.navigate("return")
    assert len([c for c in sim.calls if c[1] == "/v1/machine:input"]) == sent_before + 1
    closed = await menu.close()
    assert closed["verified"] and not sim.menu_open


async def test_menu_navigation_unsupported_on_legacy():
    _, client, caps, inputs = await build("legacy")
    menu = MenuController(client, caps, inputs)
    with pytest.raises(InputUnsupported):
        await menu.navigate("down")
