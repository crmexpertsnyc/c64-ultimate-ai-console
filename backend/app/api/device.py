"""Device control endpoints (/api/device/*, /api/capabilities)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.container import Container
from app.ultimate.drives import drive_to_dict
from app.ultimate.menu import MENU_KEYS

from .deps import audited, get_container, get_source

router = APIRouter(tags=["device"])


class ConfirmBody(BaseModel):
    confirm: bool = False


class KeyBody(BaseModel):
    key: str = Field(..., max_length=20, examples=["return", "f1", "run_stop", "a"])
    transition: Literal["tap", "press", "release"] = "tap"


class TypeBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=512)
    press_return: bool = False


class JoystickBody(BaseModel):
    inputs: list[str] = Field(..., min_length=1, max_length=7, examples=[["fire"], ["up", "fire"]])
    port: int | None = Field(None, ge=1, le=2)
    transition: Literal["tap", "press", "release"] = "tap"


class PortBody(BaseModel):
    port: int = Field(..., ge=1, le=2)


class MenuBody(BaseModel):
    action: str = Field(..., description="open | close | toggle | " + " | ".join(MENU_KEYS))


class MountBody(BaseModel):
    path: str = Field(..., max_length=1024, description="Path on the Ultimate (storage=device) or on this machine")
    storage: Literal["device", "local"] = "device"
    mode: Literal["readwrite", "readonly", "unlinked"] | None = None


class DriveModeBody(BaseModel):
    mode: Literal["1541", "1571", "1581"]


class RunnerBody(BaseModel):
    kind: Literal["run_prg", "load_prg", "run_crt", "sid", "mod"]
    path: str = Field(..., max_length=1024, description="File path on the Ultimate's storage")
    songnr: int | None = Field(None, ge=0, le=255)


@router.get("/api/device", summary="Connection status, device info, drives, input mode")
async def device_status(c: Container = Depends(get_container)):
    return c.device.status()


@router.get("/api/capabilities", summary="Probed capability matrix")
async def capabilities(c: Container = Depends(get_container)):
    return c.device.caps.to_dict()


@router.post("/api/device/connect", summary="Reconnect and re-probe capabilities")
async def reconnect(c: Container = Depends(get_container)):
    return await c.device.connect()


def _need_connected(c: Container) -> None:
    if not c.device.connected:
        raise HTTPException(503, f"C64 Ultimate not connected: {c.device.last_error or 'offline'}")


@router.post("/api/device/reset", summary="Reset the C64 (PUT /v1/machine:reset)")
async def reset(c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    await c.device.release_all_inputs("reset")
    await audited(c, source, "machine.reset", c.device.client.reset())
    c.device.caps.record_use("machineReset", True)
    return {"ok": True}


@router.post("/api/device/reboot", summary="Reboot (PUT /v1/machine:reboot)")
async def reboot(c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    await c.device.release_all_inputs("reboot")
    await audited(c, source, "machine.reboot", c.device.client.reboot())
    c.device.caps.record_use("machineReboot", True)
    return {"ok": True}


@router.post("/api/device/pause")
async def pause(c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    await audited(c, source, "machine.pause", c.device.client.pause())
    c.device.caps.record_use("machinePause", True)
    return {"ok": True}


@router.post("/api/device/resume")
async def resume(c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    await audited(c, source, "machine.resume", c.device.client.resume())
    c.device.caps.record_use("machineResume", True)
    return {"ok": True}


@router.post("/api/device/power-off", summary="Power off — requires {\"confirm\": true}")
async def power_off(body: ConfirmBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    if not body.confirm:
        raise HTTPException(400, "power off requires confirm=true")
    if not c.settings.ALLOW_POWER_OFF:
        raise HTTPException(403, "power off disabled by ALLOW_POWER_OFF=false")
    await c.device.release_all_inputs("power off")
    await audited(c, source, "machine.poweroff", c.device.client.power_off())
    c.device.connected = False
    c.device.publish_status()
    return {"ok": True}


@router.get("/api/device/menu", summary="Parsed Ultimate menu screen (null when closed)")
async def read_menu(c: Container = Depends(get_container)):
    _need_connected(c)
    screen = await c.device.menu.read()
    return {"open": screen is not None, "screen": screen.to_dict() if screen else None}


@router.post("/api/device/menu", summary="Open/close/toggle the menu or navigate with closed-loop verification")
async def menu(body: MenuBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    m = c.device.menu
    if body.action == "open":
        return await audited(c, source, "menu.open", m.open())
    if body.action == "close":
        return await audited(c, source, "menu.close", m.close())
    if body.action == "toggle":
        await audited(c, source, "menu.toggle", c.device.client.menu_button())
        c.device.caps.record_use("menuButton", True)
        return {"action": "toggle", "verified": False}
    if body.action in MENU_KEYS:
        return await audited(c, source, f"menu.{body.action}", m.navigate(body.action))
    raise HTTPException(400, f"unknown menu action {body.action}")


@router.post("/api/device/input", summary="Keyboard key press/release/tap")
async def key_input(body: KeyBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    inp = c.device.inputs
    fn = {"tap": inp.tap_key, "press": inp.press_key, "release": inp.release_key}[body.transition]
    await audited(c, source, f"input.key.{body.transition}", fn(body.key), {"key": body.key})
    return {"ok": True, "input": inp.status()}


@router.post("/api/device/type", summary="Type text (REST input, or guarded legacy keyboard buffer)")
async def type_text(body: TypeBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    text = body.text + ("\r" if body.press_return else "")
    n = await audited(c, source, "input.type", c.device.inputs.type_text(text), {"length": len(text)})
    return {"ok": True, "typed": n, "mode": c.device.inputs.mode}


@router.post("/api/device/joystick", summary="Joystick press/release/tap")
async def joystick(body: JoystickBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    inp = c.device.inputs
    fn = {"tap": inp.tap_joystick, "press": inp.press_joystick, "release": inp.release_joystick}[body.transition]
    await audited(c, source, f"input.joystick.{body.transition}", fn(body.inputs, body.port),
                  {"inputs": body.inputs, "port": body.port})
    return {"ok": True, "input": inp.status()}


@router.post("/api/device/joystick/port", summary="Select default joystick port")
async def joystick_port(body: PortBody, c: Container = Depends(get_container)):
    c.device.inputs.set_joystick_port(body.port)
    c.device.publish_status()
    return c.device.inputs.status()


@router.post("/api/device/release-all", summary="Emergency: release every injected input")
async def release_all(c: Container = Depends(get_container), source: str = Depends(get_source)):
    async with c.audit.action(source, "input.release_all") as rec:
        sent = await c.device.inputs.release_all()
        rec.set_response({"sent": sent})
    return {"ok": True, "sent": sent}


@router.get("/api/device/drives")
async def drives(c: Container = Depends(get_container)):
    _need_connected(c)
    return [drive_to_dict(d) for d in await c.device.refresh_drives()]


@router.post("/api/device/drives/{drive}/mount", summary="Mount an image (device path or local upload)")
async def mount(drive: str, body: MountBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    from pathlib import Path
    ds = c.device.drives
    coro = ds.mount_device_path(drive, body.path, body.mode) if body.storage == "device" else \
        ds.mount_local_file(drive, Path(body.path), body.mode or "readonly")
    await audited(c, source, "drives.mount", coro, {"drive": drive, "path": body.path})
    await c.device.refresh_drives()
    c.device.publish_status()
    return {"ok": True}


@router.post("/api/device/drives/{drive}/mode", summary="Select 1541/1571/1581 mode")
async def drive_mode(drive: str, body: DriveModeBody, c: Container = Depends(get_container),
                     source: str = Depends(get_source)):
    _need_connected(c)
    await audited(c, source, "drives.set_mode", c.device.drives.set_mode(drive, body.mode), {"mode": body.mode})
    await c.device.refresh_drives()
    return {"ok": True}


@router.post("/api/device/drives/{drive}/{action}", summary="remove | reset | on | off")
async def drive_action(drive: str, action: Literal["remove", "reset", "on", "off"],
                       c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    ds = c.device.drives
    coro = {"remove": lambda: ds.remove(drive), "reset": lambda: ds.reset(drive),
            "on": lambda: ds.power(drive, True), "off": lambda: ds.power(drive, False)}[action]()
    await audited(c, source, f"drives.{action}", coro, {"drive": drive})
    await c.device.refresh_drives()
    c.device.publish_status()
    return {"ok": True}


@router.post("/api/device/run", summary="Run a PRG/CRT/SID/MOD stored on the Ultimate")
async def run_file(body: RunnerBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    _need_connected(c)
    await audited(c, source, f"runners.{body.kind}", c.device.runners.run(body.kind, device_path=body.path,
                                                                           songnr=body.songnr))
    return {"ok": True}


@router.get("/api/device/screen", summary="C64 text screen via DMA memory read (if supported)")
async def text_screen(c: Container = Depends(get_container)):
    _need_connected(c)
    lines = await c.device.inputs.read_text_screen()
    return {"available": lines is not None, "lines": lines}


@router.get("/api/device/screen/sample", summary="What the real C64 shows: text, mode, scrollers, joystick reads")
async def screen_sample(c: Container = Depends(get_container)):
    """Read-only (DMA reads). Same shape as Browser Play's screen sample, for the startup analyzer, hints,
    co-pilot and score tracking on the real machine."""
    _need_connected(c)
    if not c.device.inputs.caps.usable("memoryRead"):
        raise HTTPException(409, "this firmware does not allow reading the C64's memory over the network")
    from app.services.screen_reader import ScreenWatcher
    from app.ultimate.client import UltimateError
    watcher = getattr(c, "screen_watcher", None)
    if watcher is None or watcher.client is not c.device.inputs.client:
        watcher = c.screen_watcher = ScreenWatcher(c.device.inputs.client)
    try:
        return await watcher.sample()
    except UltimateError as exc:
        raise HTTPException(502, f"could not read the C64's screen: {exc}") from exc


# ------------------------------------------------------------ joystick bridge
@router.get("/api/joybridge", summary="ESP32 joystick bridge status")
async def joybridge_status(c: Container = Depends(get_container)):
    return c.device.bridge.status()


@router.post("/api/joybridge/discover", summary="Find joystick bridges on the local network")
async def joybridge_discover():
    from app.ultimate.joybridge import discover
    return {"found": await discover()}


class BridgeTestBody(BaseModel):
    port: int | None = None


@router.post("/api/joybridge/test", summary="Wiring test: taps up, down, left, right, fire in turn")
async def joybridge_test(body: BridgeTestBody | None = None, c: Container = Depends(get_container),
                         source: str = Depends(get_source)):
    bridge = c.device.bridge
    port = (body.port if body else None) or c.device.inputs.joystick_port
    if not bridge.online:
        raise HTTPException(503, "the joystick bridge is not responding")
    if port not in (bridge.info or {}).get("ports", []):
        raise HTTPException(409, f"the joystick bridge is not wired to port {port}")
    async with c.audit.action(source, "joybridge.test", f"port {port}"):
        done = await bridge.test_pattern(port)
    return {"ok": True, "port": port, "sent": done}


@router.post("/api/device/handoff-to-browser",
             summary="Switching to 💻 In browser: stop the game on the real C64 (reset) so both don't run")
async def handoff_to_browser(c: Container = Depends(get_container), source: str = Depends(get_source)):
    from app.services.launcher import SessionState

    session = c.launcher.session
    if not c.device.connected or not session.game_id:
        return {"reset": False}
    title, fmt = session.title, (session.format or "").lower()
    await c.device.release_all_inputs("switched to browser play")
    # A cartridge (CRT) can survive a plain reset and restart itself; a reboot clears it.
    if fmt == "crt" and c.device.caps.usable("machineReboot"):
        await audited(c, source, "machine.reboot", c.device.client.reboot(), {"reason": "switched to browser play"})
    else:
        await audited(c, source, "machine.reset", c.device.client.reset(), {"reason": "switched to browser play"})
    c.launcher.session = SessionState()
    c.hub.publish("session", c.launcher.session.to_dict())
    c.device.publish_status()
    return {"reset": True, "title": title}
