"""Unified keyboard / joystick input layer.

Two back ends:

* **REST** — ``POST /v1/machine:input`` (firmware that exposes it). Supports press,
  release, tap for keyboard and both joystick ports, plus ``release_all``.
* **Joystick bridge** — an ESP32 wired into the joystick port (see ``joybridge.py``). When it is
  online it handles all joystick input, on any firmware.
* **Legacy** — for firmware without the input route (e.g. Commodore 1.1.0 / Spiffy).
  Text is injected into the KERNAL keyboard buffer ($0277, length at $C6) through DMA
  memory writes. Before *every* write the machine state is verified:

    - IRQ vector $0314/$0315 must point at the stock KERNAL handler $EA31
      (a game that installed its own IRQ is not reading the buffer the same way),
    - XMAX ($0289) must be 10 (stock keyboard buffer size),
    - NDX ($C6) must be 0..10 (a sane pending-key count).

  If any check fails the write is refused. Legacy mode offers taps only: holding keys,
  RUN/STOP, RESTORE and joystick input have no safe documented technique, so they are
  reported as unsupported rather than faked.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .capabilities import CapabilityMatrix
from .client import UltimateClient, UltimateError, UltimateNotFound
from .joybridge import JoyBridge
from .petscii import (
    JOYSTICK_INPUTS,
    NAMED_KEYS,
    PETSCII_SPECIAL,
    REST_KEYS,
    ascii_to_petscii,
    char_to_rest_keys,
    screen_to_text,
)

log = logging.getLogger("c64.input")

KBD_BUFFER = 0x0277
KBD_COUNT = 0x00C6
KBD_XMAX = 0x0289
SCREEN_PAGE = 0x0288
IRQ_VECTOR = 0x0314
STOCK_IRQ = (0x31, 0xEA)


class InputUnsupported(Exception):
    """The requested input cannot be delivered on this device / firmware."""


class LegacyStateError(Exception):
    """Legacy injection refused because the machine is not in a verified KERNAL state."""


@dataclass
class HeldInput:
    kind: str  # "keyboard" | "joystick"
    inputs: tuple[str, ...]
    port: int | None
    since: float = field(default_factory=time.time)


def resolve_key(name: str) -> tuple[str, ...]:
    """Resolve a UI/command key name (or single character) to REST key names.
    Combinations are joined with '+', e.g. ``left_shift+a`` or ``commodore+shift``."""
    if "+" in name and len(name) > 1:
        combo: list[str] = []
        for part in name.split("+"):
            for k in resolve_key(part):
                if k not in combo:
                    combo.append(k)
        if not 1 <= len(combo) <= 8:
            raise ValueError("a key combination may contain 1..8 keys")
        return tuple(combo)
    key = name.strip().lower().replace(" ", "_").replace("/", "_")
    if key in NAMED_KEYS:
        return NAMED_KEYS[key]
    if key in REST_KEYS:
        return (key,)
    if len(name) == 1:
        combo = char_to_rest_keys(name)
        if combo:
            return combo
    raise ValueError(f"unknown key: {name!r}")


def _validate_joystick(port: int, inputs: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if port not in (1, 2):
        raise ValueError("joystick port must be 1 or 2")
    clean = tuple(dict.fromkeys(i.lower() for i in inputs))
    bad = [i for i in clean if i not in JOYSTICK_INPUTS]
    if bad or not clean:
        raise ValueError(f"invalid joystick inputs: {bad or 'none'}")
    return clean


class InputController:
    def __init__(self, client: UltimateClient, caps: CapabilityMatrix, type_delay_ms: int = 60,
                 max_hold_seconds: float = 20.0, bridge: JoyBridge | None = None):
        self.client = client
        self.bridge = bridge
        self.caps = caps
        self.type_delay = type_delay_ms / 1000
        self.max_hold_seconds = max_hold_seconds
        self.held: dict[str, HeldInput] = {}
        self.joystick_port = 2
        self._lock = asyncio.Lock()

    # ----------------------------------------------------------------- state
    @property
    def mode(self) -> str:
        return self.caps.input_mode

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "joystickPort": self.joystick_port,
            "joystickSupported": self.joystick_via is not None,
            "joystickVia": self.joystick_via,
            "bridge": self.bridge.status() if self.bridge else None,
            "keyHoldSupported": self.mode == "rest",
            "held": [{"key": k, "kind": h.kind, "inputs": list(h.inputs), "port": h.port,
                      "heldFor": round(time.time() - h.since, 1)} for k, h in self.held.items()],
        }

    @property
    def joystick_via(self) -> str | None:
        """Which back end carries joystick input: the ESP32 bridge (preferred), REST, or nothing."""
        if self.bridge and self.bridge.online:
            return "bridge"
        if self.caps.supported("directJoystick"):
            return "rest"
        return None

    def _require_rest(self, what: str) -> None:
        if self.mode != "rest":
            raise InputUnsupported(f"{what} requires the REST input API (machine:input), which this "
                                   f"firmware does not provide")

    async def _send(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Send REST input events; on any failure try to release everything we injected."""
        try:
            result = await self.client.send_input(events)
            self.caps.record_use("directKeyboard", True)
            return result
        except UltimateNotFound:
            self.caps.record_use("directKeyboard", False, not_found=True)
            self.caps.record_use("directJoystick", False, not_found=True)
            raise
        except Exception:
            await self._safe_release_all()
            raise

    async def _safe_release_all(self) -> None:
        self.held.clear()
        if self.bridge:
            self.bridge.release_all()
        if self.mode != "rest":
            return
        try:
            await self.client.send_input([{"kind": "release_all"}])
        except Exception as exc:  # noqa: BLE001 - best effort safety net
            log.warning("release_all after failure also failed: %s", exc)

    # -------------------------------------------------------------- keyboard
    async def press_key(self, key: str) -> None:
        self._require_rest("holding a key")
        keys = resolve_key(key)
        if "restore" in keys:
            raise ValueError("RESTORE can only be tapped")
        async with self._lock:
            await self._send([{"kind": "keyboard", "inputs": list(keys), "transition": "press"}])
            self.held[f"key:{key.lower()}"] = HeldInput("keyboard", keys, None)

    async def release_key(self, key: str) -> None:
        self._require_rest("releasing a key")
        keys = resolve_key(key)
        async with self._lock:
            await self._send([{"kind": "keyboard", "inputs": list(keys), "transition": "release"}])
            self.held.pop(f"key:{key.lower()}", None)

    async def tap_key(self, key: str) -> None:
        if self.mode == "rest":
            keys = resolve_key(key)
            async with self._lock:
                await self._send([{"kind": "keyboard", "inputs": list(keys), "transition": "tap"}])
            return
        if self.mode == "legacy":
            await self.legacy_tap(key)
            return
        raise InputUnsupported("no keyboard input method available on this device")

    async def type_text(self, text: str) -> int:
        """Type text. Returns number of characters sent."""
        if not text:
            return 0
        if len(text) > 512:
            raise ValueError("text too long (max 512 characters)")
        if self.mode == "rest":
            combos = []
            for ch in text:
                combo = char_to_rest_keys(ch)
                if combo is None:
                    raise ValueError(f"character {ch!r} cannot be typed on a C64 keyboard")
                combos.append(combo)
            async with self._lock:
                for combo in combos:
                    await self._send([{"kind": "keyboard", "inputs": list(combo), "transition": "tap"}])
                    await asyncio.sleep(self.type_delay)
            return len(text)
        if self.mode == "legacy":
            return await self.legacy_type_text(text)
        raise InputUnsupported("no keyboard input method available on this device")

    # -------------------------------------------------------------- joystick
    async def press_joystick(self, inputs: list[str], port: int | None = None) -> None:
        via = self._require_joystick()
        port = port or self.joystick_port
        clean = _validate_joystick(port, inputs)
        if via == "bridge":
            await self._bridge_call(self.bridge.press(port, clean))  # type: ignore[union-attr]
        else:
            async with self._lock:
                await self._send([{"kind": "joystick", "port": port, "inputs": list(clean), "transition": "press"}])
        for i in clean:
            self.held[f"joy{port}:{i}"] = HeldInput("joystick", (i,), port)

    async def release_joystick(self, inputs: list[str], port: int | None = None) -> None:
        via = self._require_joystick()
        port = port or self.joystick_port
        clean = _validate_joystick(port, inputs)
        if via == "bridge":
            await self._bridge_call(self.bridge.release(port, clean))  # type: ignore[union-attr]
        else:
            async with self._lock:
                await self._send([{"kind": "joystick", "port": port, "inputs": list(clean), "transition": "release"}])
        for i in clean:
            self.held.pop(f"joy{port}:{i}", None)

    async def tap_joystick(self, inputs: list[str], port: int | None = None) -> None:
        via = self._require_joystick()
        port = port or self.joystick_port
        clean = _validate_joystick(port, inputs)
        if via == "bridge":
            await self._bridge_call(self.bridge.tap(port, clean))  # type: ignore[union-attr]
            return
        async with self._lock:
            await self._send([{"kind": "joystick", "port": port, "inputs": list(clean), "transition": "tap"}])

    async def set_joystick(self, inputs: list[str], port: int | None = None) -> None:
        """Make exactly ``inputs`` held on the port (empty = all released). Used by live gamepad/keyboard
        passthrough, which sends whole states instead of press/release pairs."""
        via = self._require_joystick()
        port = port or self.joystick_port
        clean = _validate_joystick(port, inputs) if inputs else ()
        before = {h.inputs[0] for k, h in self.held.items() if h.kind == "joystick" and h.port == port}
        if via == "bridge":
            await self._bridge_call(self.bridge.set(port, clean))  # type: ignore[union-attr]
        else:
            events = []
            if before - set(clean):
                events.append({"kind": "joystick", "port": port, "inputs": sorted(before - set(clean)), "transition": "release"})
            if set(clean) - before:
                events.append({"kind": "joystick", "port": port, "inputs": sorted(set(clean) - before), "transition": "press"})
            if events:
                async with self._lock:
                    await self._send(events)
        for i in before - set(clean):
            self.held.pop(f"joy{port}:{i}", None)
        for i in clean:
            self.held.setdefault(f"joy{port}:{i}", HeldInput("joystick", (i,), port))

    async def _bridge_call(self, call) -> None:  # noqa: ANN001
        try:
            await call
        except RuntimeError as exc:
            await self._safe_release_all()
            raise InputUnsupported(str(exc)) from exc
        except ValueError:
            raise
        except Exception:
            await self._safe_release_all()
            raise

    def _require_joystick(self) -> str:
        via = self.joystick_via
        if via is None:
            if self.bridge and self.bridge.configured:
                raise InputUnsupported("the joystick bridge is not responding — check that it is powered and on Wi-Fi")
            raise InputUnsupported("joystick injection is not available: this firmware has no REST input "
                                   "API — connect an ESP32 joystick bridge (see docs/joystick-bridge.md)")
        return via

    def set_joystick_port(self, port: int) -> None:
        if port not in (1, 2):
            raise ValueError("joystick port must be 1 or 2")
        self.joystick_port = port

    async def release_all(self) -> bool:
        """Release every injected input. Returns True when a device call was made."""
        self.held.clear()
        sent = False
        if self.bridge and self.bridge.configured:
            self.bridge.release_all()
            sent = True
        if self.mode == "rest":
            async with self._lock:
                await self.client.send_input([{"kind": "release_all"}])
            return True
        return sent  # legacy injection never holds anything

    async def expire_stale_holds(self) -> list[str]:
        """Safety watchdog: release holds older than max_hold_seconds."""
        now = time.time()
        stale = [k for k, h in self.held.items() if now - h.since > self.max_hold_seconds]
        if stale:
            log.warning("releasing stale held inputs: %s", stale)
            await self._safe_release_all()
        return stale

    # ------------------------------------------------------------------ legacy
    async def verify_kernal_state(self) -> dict[str, Any]:
        """Read-only checks that the stock KERNAL editor owns the keyboard buffer."""
        if not self.caps.usable("memoryRead"):
            raise LegacyStateError("memory read unavailable; cannot verify machine state")
        irq = await self.client.read_memory(IRQ_VECTOR, 2)
        xmax = (await self.client.read_memory(KBD_XMAX, 1))[0]
        ndx = (await self.client.read_memory(KBD_COUNT, 1))[0]
        state = {"irq": f"${irq[1]:02X}{irq[0]:02X}", "xmax": xmax, "pending": ndx}
        if tuple(irq[:2]) != STOCK_IRQ:
            raise LegacyStateError(f"IRQ vector is {state['irq']}, not the stock KERNAL $EA31 — a program "
                                   f"owns the machine; refusing to write the keyboard buffer")
        if xmax != 10:
            raise LegacyStateError(f"keyboard buffer size (XMAX) is {xmax}, expected 10; refusing")
        if ndx > 10:
            raise LegacyStateError(f"keyboard buffer count is {ndx}; memory not in a known state")
        return state

    async def _wait_buffer_empty(self, timeout: float = 4.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (await self.client.read_memory(KBD_COUNT, 1))[0] == 0:
                return
            await asyncio.sleep(0.05)
        raise LegacyStateError("keyboard buffer did not drain; the machine is not reading keys")

    async def _legacy_inject(self, codes: list[int], wait_drain: bool = True) -> None:
        if not self.caps.usable("legacyKeyboard"):
            raise InputUnsupported("legacy keyboard injection needs memory read and write")
        async with self._lock:
            for i in range(0, len(codes), 10):
                chunk = codes[i:i + 10]
                state = await self.verify_kernal_state()
                if state["pending"]:
                    await self._wait_buffer_empty()
                await self.client.write_memory(KBD_BUFFER, bytes(chunk))
                await self.client.write_memory(KBD_COUNT, bytes([len(chunk)]))
                if wait_drain or i + 10 < len(codes):
                    await self._wait_buffer_empty()

    async def legacy_type_text(self, text: str, wait_drain: bool = True) -> int:
        codes = []
        for ch in text:
            code = ascii_to_petscii(ch)
            if code is None:
                raise ValueError(f"character {ch!r} has no PETSCII keyboard code")
            codes.append(code)
        await self._legacy_inject(codes, wait_drain=wait_drain)
        return len(codes)

    async def legacy_tap(self, key: str) -> None:
        name = key.strip().lower().replace(" ", "_")
        aliases = {"enter": "return", "delete": "del", "backspace": "del", "down": "cursor_down",
                   "up": "cursor_up", "left": "cursor_left", "right": "cursor_right", "clear": "clr",
                   "clr_home": "home", "inst_del": "del", "cursor_up_down": "cursor_down",
                   "cursor_left_right": "cursor_right"}
        chars = {"plus": "+", "minus": "-", "pound": "£", "at": "@", "star": "*", "colon": ":",
                 "semicolon": ";", "equals": "=", "comma": ",", "period": ".", "slash": "/", "space": " ",
                 "arrow_up": "↑", "arrow_left": "←"}
        name = aliases.get(name, name)
        if name in chars:
            key = chars[name]
        if name in ("run_stop", "runstop", "stop", "restore", "commodore", "ctrl", "shift"):
            raise InputUnsupported(f"{key} is read from the keyboard matrix/NMI and cannot be injected "
                                   f"through the KERNAL buffer")
        if name in PETSCII_SPECIAL:
            await self._legacy_inject([PETSCII_SPECIAL[name]])
            return
        if len(key) == 1 and ascii_to_petscii(key) is not None:
            await self._legacy_inject([ascii_to_petscii(key)])  # type: ignore[list-item]
            return
        raise InputUnsupported(f"key {key!r} is not available in legacy mode")

    async def legacy_press_return(self) -> None:
        await self._legacy_inject([0x0D])

    async def legacy_load_command(self, device: int = 8, filename: str = "*", secondary: int = 1,
                                  run: bool = False) -> str:
        if not 8 <= device <= 30:
            raise ValueError("device must be 8..30")
        if any(c in filename for c in '"\r\n') or len(filename) > 16:
            raise ValueError("invalid filename")
        cmd = f'LOAD"{filename}",{device},{secondary}\r'
        await self.legacy_type_text(cmd)
        if run:
            # Queued without waiting: the editor consumes it when BASIC returns to READY.
            await self.legacy_type_text("RUN\r", wait_drain=False)
        return cmd.strip()

    # ------------------------------------------------------------ screen read
    async def read_text_screen(self) -> list[str] | None:
        """Read the C64 text screen via DMA (for READY. detection). None if unavailable."""
        if not self.caps.usable("memoryRead"):
            return None
        try:
            page = (await self.client.read_memory(SCREEN_PAGE, 1))[0]
            if page == 0 or page > 0xFC:
                return None
            data = await self.client.read_memory(page * 256, 1000)
        except UltimateError:
            return None
        return screen_to_text(data)
