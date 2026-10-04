"""Capability detection by direct probing.

Nothing is inferred from the firmware version string. Each capability is in one of
four states:

* ``supported``   – a probe (or a real call) proved the route exists.
* ``unsupported`` – a probe proved the route is absent (route-level 404/405/501).
* ``unverified``  – the route cannot be probed without side effects (e.g. reset,
                    power off). It is usable on explicit user request; the first real
                    call promotes it to supported or demotes it to unsupported.
* ``unknown``     – the probe could not reach a verdict (device offline, ambiguous 404).

Probes only use read-only requests, or requests that intentionally omit a required
parameter so the firmware rejects them before doing anything (e.g. ``PUT
/v1/runners:run_prg`` with no ``file``). They never reset, mount, write memory,
start streams, or touch configuration.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .client import UltimateClient, UltimateConnectionError
from .transport import UltimateResponse

log = logging.getLogger("c64.capabilities")

State = Literal["supported", "unsupported", "unverified", "unknown"]

CAPABILITY_NAMES: dict[str, str] = {
    "machineReset": "Machine reset",
    "machineReboot": "Machine reboot",
    "machinePause": "Machine pause",
    "machineResume": "Machine resume",
    "machinePowerOff": "Machine power off",
    "menuButton": "Ultimate menu button",
    "menuScreen": "Ultimate menu screen read",
    "directKeyboard": "REST keyboard injection",
    "directJoystick": "REST joystick injection",
    "legacyKeyboard": "Legacy keyboard (KERNAL buffer)",
    "memoryRead": "Memory read (DMA)",
    "memoryWrite": "Memory write (DMA)",
    "driveStatus": "Drive status",
    "driveControl": "Drive reset/on/off/remove",
    "driveRom": "Drive ROM loading",
    "driveMode": "Drive mode selection",
    "mountDisk": "Disk mounting",
    "loadPrg": "PRG loading",
    "runPrg": "PRG running",
    "runCrt": "CRT running",
    "sidPlayback": "SID playback",
    "modPlayback": "MOD playback",
    "videoStream": "Video streaming",
    "audioStream": "Audio streaming",
    "configApi": "Configuration API",
    "fileApi": "File API",
    "ftp": "FTP",
    "telnet": "Telnet",
}

# Substrings that indicate a 404 was produced by a route handler (e.g. "file not found",
# "no menu active") rather than by the router ("route not found").
_HANDLER_404_HINTS = ("file", "param", "argument", "missing", "menu", "drive", "image", "path",
                      "address", "required", "invalid")
_ROUTE_404_HINTS = ("route", "unknown", "not implemented", "no such", "unsupported")


@dataclass
class Capability:
    state: State = "unknown"
    evidence: str = ""
    checked_at: float = 0.0

    @property
    def usable(self) -> bool:
        return self.state in ("supported", "unverified")


@dataclass
class CapabilityMatrix:
    firmware: str = ""
    product: str = ""
    api_version: str = ""
    password_required: bool = False
    auth_ok: bool = True
    reachable: bool = False
    probed_at: float = 0.0
    details: dict[str, Capability] = field(default_factory=lambda: {k: Capability() for k in CAPABILITY_NAMES})

    def state(self, name: str) -> State:
        return self.details.get(name, Capability()).state

    def supported(self, name: str) -> bool:
        return self.state(name) == "supported"

    def usable(self, name: str) -> bool:
        return self.details.get(name, Capability()).usable

    def set(self, name: str, state: State, evidence: str) -> None:
        self.details[name] = Capability(state=state, evidence=evidence[:300], checked_at=time.time())

    def record_use(self, name: str, success: bool, not_found: bool = False) -> None:
        """Learn from a real call: promote unverified→supported, demote on route 404."""
        cap = self.details.get(name)
        if cap is None:
            return
        if success and cap.state != "supported":
            self.set(name, "supported", "verified by successful call")
        elif not_found and cap.state in ("unverified", "unknown"):
            self.set(name, "unsupported", "call returned 404")

    @property
    def input_mode(self) -> str:
        if self.supported("directKeyboard"):
            return "rest"
        if self.usable("legacyKeyboard"):
            return "legacy"
        return "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "firmware": self.firmware,
            "product": self.product,
            "apiVersion": self.api_version,
            "reachable": self.reachable,
            "passwordRequired": self.password_required,
            "authOk": self.auth_ok,
            "probedAt": self.probed_at,
            "inputMode": self.input_mode,
            # Strict booleans: true only when verified.
            "capabilities": {k: v.state == "supported" for k, v in self.details.items()},
            # Usable includes unverified routes that may be tried on explicit user request.
            "usable": {k: v.usable for k, v in self.details.items()},
            "details": {k: {"label": CAPABILITY_NAMES.get(k, k), **asdict(v)} for k, v in self.details.items()},
        }


def classify(resp: UltimateResponse) -> tuple[State, str]:
    """Decide whether a probe response proves the route exists."""
    evidence = f"HTTP {resp.status}: {resp.text_excerpt(160)}"
    if resp.status == 403:
        return "unknown", "authentication required (403)"
    if resp.status in (405, 501):
        return "unsupported", evidence
    if resp.status == 404:
        text = (" ".join(resp.errors) or resp.text_excerpt(200)).lower()
        if any(h in text for h in _ROUTE_404_HINTS):
            return "unsupported", evidence
        if any(h in text for h in _HANDLER_404_HINTS):
            return "supported", evidence + " (handler-level 404)"
        return "unsupported", evidence
    return "supported", evidence


async def _tcp_probe(host: str, port: int, timeout: float = 2.0) -> tuple[State, str]:
    if not host:
        return "unknown", "no host"
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (TimeoutError, OSError) as exc:
        return "unsupported", f"port {port} closed ({type(exc).__name__})"
    banner = b""
    try:
        banner = await asyncio.wait_for(reader.read(120), 1.0)
    except (TimeoutError, OSError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
    # Telnet banners contain IAC negotiation and ANSI escapes; keep printable text only.
    banner = re.sub(rb"\xff[\xfb-\xfe].|\xff[\xf0-\xfa]", b"", banner)  # telnet IAC negotiation
    raw_text = re.sub(r"\x1b(\[[0-9;?]*[A-Za-z]|[A-Za-z])", "", banner.decode("latin-1", errors="replace"))
    text = " ".join("".join(ch for ch in raw_text if 32 <= ord(ch) < 127).split())[:80]
    return "supported", f"port {port} open" + (f" — {text}" if text else "")


BASELINE_PATH = "/v1/machine:c64console_nonexistent_probe"
IDENTICAL_TO_UNKNOWN = "identical to the device's unknown-route 404"


class CapabilityProber:
    def __init__(self, client: UltimateClient, host: str = "", tcp_probes: bool = True):
        self.client = client
        self.host = host
        self.tcp_probes = tcp_probes
        # Response to a route that certainly does not exist. Firmware 1.1.0 answers unknown
        # routes with a bare 404 and an empty body; a probe returning exactly the same thing
        # proves the route is absent (and resolves "404 = menu closed?" ambiguity).
        self._baseline: UltimateResponse | None = None

    async def _probe(self, matrix: CapabilityMatrix, name: str, method: str, path: str,
                     params: dict[str, Any] | None = None, json_body: Any = None) -> State:
        try:
            resp = await self.client.raw(method, path, operation=f"probe.{name}", params=params,
                                         json_body=json_body)
        except UltimateConnectionError as exc:
            matrix.set(name, "unknown", str(exc))
            return "unknown"
        if resp.status == 403:
            matrix.auth_ok = False
            matrix.password_required = True
        base = self._baseline
        if resp.status == 404 and base is not None and base.status == 404 and resp.content == base.content:
            matrix.set(name, "unsupported", f"{method} {path} → HTTP 404, {IDENTICAL_TO_UNKNOWN}")
            return "unsupported"
        state, evidence = classify(resp)
        matrix.set(name, state, f"{method} {path} → {evidence}")
        return state

    async def probe(self) -> CapabilityMatrix:
        m = CapabilityMatrix(probed_at=time.time())
        # --- Identity. /v1/version is the baseline: without it there is no REST API.
        try:
            resp = await self.client.raw("GET", "/v1/version", operation="probe.version")
        except UltimateConnectionError as exc:
            for name in CAPABILITY_NAMES:
                m.set(name, "unknown", f"device unreachable: {exc.detail}")
            return m
        m.reachable = True
        if resp.status == 403:
            m.password_required, m.auth_ok = True, False
            for name in CAPABILITY_NAMES:
                m.set(name, "unknown", "authentication failed (403) — check C64_ULTIMATE_PASSWORD")
            return m
        if resp.ok:
            m.api_version = str((resp.json() or {}).get("version", ""))
        info = await self.client.raw("GET", "/v1/info", operation="probe.info")
        try:
            self._baseline = await self.client.raw("GET", BASELINE_PATH, operation="probe.baseline")
        except UltimateConnectionError:
            self._baseline = None
        if info.ok:
            data = info.json() or {}
            m.firmware = str(data.get("firmware_version", ""))
            m.product = str(data.get("product", ""))

        # --- Read-only / validation-rejected probes.
        await self._probe(m, "menuScreen", "GET", "/v1/machine:menu_screen")
        ev = m.details["menuScreen"].evidence
        if (m.state("menuScreen") == "unsupported" and "HTTP 404" in ev and IDENTICAL_TO_UNKNOWN not in ev
                and not any(h in ev.lower() for h in _ROUTE_404_HINTS)):
            # A 404 is documented both for "no readable menu active" and for a missing route.
            # The body did not say which, so leave it undecided; the first successful read
            # while the menu is open promotes it.
            m.set("menuScreen", "unknown", ev + " — ambiguous (menu may simply be closed)")
        input_state = await self._probe(m, "directKeyboard", "GET", "/v1/machine:input")
        m.set("directJoystick", input_state, m.details["directKeyboard"].evidence)
        await self._probe(m, "memoryRead", "GET", "/v1/machine:readmem", params={"address": "0000", "length": 1})
        # PUT writemem without address/data is rejected by validation; nothing is written.
        await self._probe(m, "memoryWrite", "PUT", "/v1/machine:writemem")
        drives = await self._probe(m, "driveStatus", "GET", "/v1/drives")
        # PUT mount without an image parameter is rejected before anything is mounted.
        await self._probe(m, "mountDisk", "PUT", "/v1/drives/a:mount")
        await self._probe(m, "loadPrg", "PUT", "/v1/runners:load_prg")
        await self._probe(m, "runPrg", "PUT", "/v1/runners:run_prg")
        await self._probe(m, "runCrt", "PUT", "/v1/runners:run_crt")
        await self._probe(m, "sidPlayback", "PUT", "/v1/runners:sidplay")
        await self._probe(m, "modPlayback", "PUT", "/v1/runners:modplay")
        # Stopping a stream that is not running has no effect.
        await self._probe(m, "videoStream", "PUT", "/v1/streams/video:stop")
        await self._probe(m, "audioStream", "PUT", "/v1/streams/audio:stop")
        await self._probe(m, "configApi", "GET", "/v1/configs")
        await self._probe(m, "fileApi", "GET", "/v1/files/Temp:info")

        # --- Routes that cannot be probed without side effects.
        group = "documented v1 route; not probed because probing would change machine state"
        for name in ("machineReset", "machineReboot", "machinePause", "machineResume", "menuButton"):
            m.set(name, "unverified", group)
        m.set("machinePowerOff", "unverified", group + " (documented for Ultimate 64 class hardware)")
        if drives == "supported":
            for name in ("driveControl", "driveRom", "driveMode"):
                m.set(name, "unverified", "drives API present; action routes not probed (side effects)")
        else:
            for name in ("driveControl", "driveRom", "driveMode"):
                m.set(name, "unknown", "drives API not detected")

        # --- Legacy keyboard needs both DMA read (state checks) and write (buffer).
        if m.supported("memoryRead") and m.supported("memoryWrite"):
            m.set("legacyKeyboard", "supported",
                  "memory read+write available; each use still verifies KERNAL state first")
        else:
            m.set("legacyKeyboard", "unsupported", "requires memory read and write")

        # --- Other documented network services.
        if self.tcp_probes and self.host:
            host = self.host.split(":")[0]
            ftp, telnet = await asyncio.gather(_tcp_probe(host, 21), _tcp_probe(host, 23))
            m.set("ftp", *ftp)
            m.set("telnet", *telnet)
        else:
            m.set("ftp", "unknown", "not probed")
            m.set("telnet", "unknown", "not probed")
        return m
