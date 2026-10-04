"""Strongly typed client for the documented Ultimate REST API (v1).

Reference: https://1541u-documentation.readthedocs.io/en/latest/api/api_calls.html

Only documented routes are implemented. There are deliberately NO methods for
firmware updates, FPGA images, or file deletion. Configuration flash writes exist
only as explicit methods and are never called automatically.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote

import httpx

from .transport import Transport, UltimateResponse

log = logging.getLogger("c64.client")

DriveId = str  # "a", "b", or other names reported by GET /v1/drives
DriveMode = Literal["1541", "1571", "1581"]
MountMode = Literal["readwrite", "readonly", "unlinked"]
ImageType = Literal["d64", "g64", "d71", "g71", "d81"]
StreamName = Literal["video", "audio", "debug"]
Transition = Literal["tap", "press", "release"]

MAX_WRITEMEM_PUT = 128  # documented limit for PUT writemem hex data


class UltimateError(Exception):
    def __init__(self, operation: str, status: int, errors: list[str] | None = None, detail: str = ""):
        self.operation = operation
        self.status = status
        self.errors = errors or []
        self.detail = detail
        msg = f"{operation} failed with HTTP {status}"
        if self.errors:
            msg += ": " + "; ".join(self.errors)
        elif detail:
            msg += f": {detail}"
        super().__init__(msg)


class UltimateAuthError(UltimateError):
    """HTTP 403 — network password missing or wrong."""


class UltimateNotFound(UltimateError):
    """HTTP 404 — route unsupported by this firmware, or the addressed resource is absent."""


class UltimateConnectionError(UltimateError):
    def __init__(self, operation: str, detail: str):
        super().__init__(operation, 0, detail=detail)


@dataclass
class VersionInfo:
    api_version: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceInfo:
    product: str = ""
    firmware_version: str = ""
    fpga_version: str = ""
    core_version: str = ""
    hostname: str = ""
    unique_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DriveInfo:
    id: str
    enabled: bool = False
    bus_id: int | None = None
    type: str = ""
    rom: str = ""
    image_file: str = ""
    image_path: str = ""
    last_error: str = ""
    partitions: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def mounted(self) -> bool:
        return bool(self.image_file)


@dataclass
class CallRecord:
    operation: str
    method: str
    path: str
    status: int
    elapsed_ms: float
    ok: bool
    errors: list[str]


def _hex_addr(address: int) -> str:
    if not 0 <= address <= 0xFFFF:
        raise ValueError(f"address out of range: {address:#x}")
    return f"{address:04X}"


def parse_drives(payload: Any) -> list[DriveInfo]:
    """Parse GET /v1/drives. Firmware returns ``{"drives": [{"a": {...}}, {"b": {...}}, ...]}``;
    we also accept a flat dict or list-of-dicts-with-id to tolerate firmware variants."""
    drives: list[DriveInfo] = []
    items = payload.get("drives", payload) if isinstance(payload, dict) else payload
    entries: list[tuple[str, dict[str, Any]]] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and len(item) == 1 and isinstance(next(iter(item.values())), dict):
                k, v = next(iter(item.items()))
                entries.append((k, v))
            elif isinstance(item, dict):
                entries.append((str(item.get("id") or item.get("name") or len(entries)), item))
    elif isinstance(items, dict):
        entries = [(k, v) for k, v in items.items() if isinstance(v, dict)]
    for key, d in entries:
        image_file = str(d.get("image_file", "") or "")
        image_path = str(d.get("image_path", "") or "")
        # Firmware 1.1.0 reports the full path in image_file and leaves image_path empty.
        if "/" in image_file and not image_path:
            image_path, _, image_file = image_file.rpartition("/")
            image_path = image_path or "/"
        drives.append(
            DriveInfo(
                id=key,
                enabled=bool(d.get("enabled", False)),
                bus_id=d.get("bus_id"),
                type=str(d.get("type", "")),
                rom=str(d.get("rom", "")),
                image_file=image_file,
                image_path=image_path,
                last_error=str(d.get("last_error", "") or ""),
                partitions=list(d.get("partitions", []) or []),
                raw=d,
            )
        )
    return drives


class UltimateClient:
    def __init__(self, transport: Transport):
        self.transport = transport
        self.listeners: list[Callable[[CallRecord], None]] = []

    async def aclose(self) -> None:
        await self.transport.aclose()

    # ------------------------------------------------------------------ core
    async def raw(self, method: str, path: str, *, operation: str = "", params=None, body=None,
                  json_body=None, filename=None) -> UltimateResponse:
        """Send a request and return the response without raising on HTTP errors."""
        op = operation or f"{method} {path}"
        try:
            resp = await self.transport.request(method, path, params=params, body=body,
                                                json_body=json_body, filename=filename)
        except httpx.TimeoutException as exc:
            self._emit(CallRecord(op, method, path, 0, 0, False, ["timeout"]))
            raise UltimateConnectionError(op, f"timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            self._emit(CallRecord(op, method, path, 0, 0, False, [type(exc).__name__]))
            raise UltimateConnectionError(op, f"{type(exc).__name__}: {exc}") from exc
        self._emit(CallRecord(op, method, path, resp.status, resp.elapsed_ms, resp.ok, resp.errors))
        return resp

    async def _call(self, method: str, path: str, operation: str, **kw) -> UltimateResponse:
        resp = await self.raw(method, path, operation=operation, **kw)
        if resp.status == 403:
            raise UltimateAuthError(operation, 403, resp.errors, "network password missing or incorrect")
        if resp.status == 404:
            raise UltimateNotFound(operation, 404, resp.errors, resp.text_excerpt())
        if not resp.ok:
            raise UltimateError(operation, resp.status, resp.errors, resp.text_excerpt())
        # The firmware reports soft failures in an "errors" list with HTTP 200.
        if resp.errors and "json" in resp.content_type:
            raise UltimateError(operation, resp.status, resp.errors)
        return resp

    def _emit(self, record: CallRecord) -> None:
        for listener in self.listeners:
            try:
                listener(record)
            except Exception:  # pragma: no cover - listeners must never break calls
                log.exception("call listener failed")

    # ----------------------------------------------------------------- about
    async def version(self) -> VersionInfo:
        data = (await self._call("GET", "/v1/version", "version")).json() or {}
        return VersionInfo(api_version=str(data.get("version", "")), raw=data)

    async def info(self) -> DeviceInfo:
        data = (await self._call("GET", "/v1/info", "info")).json() or {}
        return DeviceInfo(
            product=str(data.get("product", "")),
            firmware_version=str(data.get("firmware_version", "")),
            fpga_version=str(data.get("fpga_version", "")),
            core_version=str(data.get("core_version", "")),
            hostname=str(data.get("hostname", "")),
            unique_id=str(data.get("unique_id", "")),
            raw=data,
        )

    # --------------------------------------------------------------- machine
    async def reset(self) -> None:
        await self._call("PUT", "/v1/machine:reset", "machine.reset")

    async def reboot(self) -> None:
        await self._call("PUT", "/v1/machine:reboot", "machine.reboot")

    async def pause(self) -> None:
        await self._call("PUT", "/v1/machine:pause", "machine.pause")

    async def resume(self) -> None:
        await self._call("PUT", "/v1/machine:resume", "machine.resume")

    async def power_off(self) -> None:
        await self._call("PUT", "/v1/machine:poweroff", "machine.poweroff")

    async def menu_button(self) -> None:
        await self._call("PUT", "/v1/machine:menu_button", "machine.menu_button")

    async def menu_screen(self) -> bytes | None:
        """Return the 2000-byte menu screen, or None when no readable menu is active (HTTP 404)."""
        resp = await self.raw("GET", "/v1/machine:menu_screen", operation="machine.menu_screen")
        if resp.status == 404:
            return None
        if resp.status == 403:
            raise UltimateAuthError("machine.menu_screen", 403, resp.errors)
        if not resp.ok:
            raise UltimateError("machine.menu_screen", resp.status, resp.errors, resp.text_excerpt())
        return resp.content

    async def read_memory(self, address: int, length: int = 256) -> bytes:
        if not 1 <= length <= 65536:
            raise ValueError("length must be 1..65536")
        resp = await self._call("GET", "/v1/machine:readmem", "machine.readmem",
                                params={"address": _hex_addr(address), "length": length})
        return resp.content

    async def write_memory(self, address: int, data: bytes) -> None:
        """Low-level DMA write. Internal only — callers must go through a guarded service."""
        if not data:
            raise ValueError("no data")
        if address + len(data) > 0x10000:
            raise ValueError("write crosses end of address space")
        if len(data) <= MAX_WRITEMEM_PUT:
            await self._call("PUT", "/v1/machine:writemem", "machine.writemem",
                             params={"address": _hex_addr(address), "data": data.hex().upper()})
        else:
            await self._call("POST", "/v1/machine:writemem", "machine.writemem",
                             params={"address": _hex_addr(address)}, body=data)

    async def debug_register(self) -> str:
        data = (await self._call("GET", "/v1/machine:debugreg", "machine.debugreg")).json() or {}
        return str(data.get("value", ""))

    # ----------------------------------------------------------------- input
    async def input_state(self) -> dict[str, Any]:
        return (await self._call("GET", "/v1/machine:input", "machine.input.get")).json() or {}

    async def send_input(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        if not 1 <= len(events) <= 64:
            raise ValueError("between 1 and 64 events per request")
        resp = await self._call("POST", "/v1/machine:input", "machine.input",
                                json_body={"events": events})
        return resp.json() or {}

    # ---------------------------------------------------------------- drives
    async def drives(self) -> list[DriveInfo]:
        return parse_drives((await self._call("GET", "/v1/drives", "drives.list")).json() or {})

    async def mount(self, drive: DriveId, image: str, type: ImageType | None = None,
                    mode: MountMode | None = None) -> None:
        await self._call("PUT", f"/v1/drives/{drive}:mount", "drives.mount",
                         params={"image": image, "type": type, "mode": mode})

    async def mount_upload(self, drive: DriveId, data: bytes, type: ImageType | None = None,
                           mode: MountMode | None = None, filename: str = "disk.d64") -> None:
        await self._call("POST", f"/v1/drives/{drive}:mount", "drives.mount_upload",
                         params={"type": type, "mode": mode}, body=data, filename=filename)

    async def drive_reset(self, drive: DriveId) -> None:
        await self._call("PUT", f"/v1/drives/{drive}:reset", "drives.reset")

    async def drive_remove(self, drive: DriveId) -> None:
        await self._call("PUT", f"/v1/drives/{drive}:remove", "drives.remove")

    async def drive_on(self, drive: DriveId) -> None:
        await self._call("PUT", f"/v1/drives/{drive}:on", "drives.on")

    async def drive_off(self, drive: DriveId) -> None:
        await self._call("PUT", f"/v1/drives/{drive}:off", "drives.off")

    async def drive_load_rom(self, drive: DriveId, file: str) -> None:
        await self._call("PUT", f"/v1/drives/{drive}:load_rom", "drives.load_rom", params={"file": file})

    async def drive_load_rom_upload(self, drive: DriveId, data: bytes) -> None:
        if len(data) not in (16384, 32768):
            raise ValueError("drive ROM must be 16K or 32K")
        await self._call("POST", f"/v1/drives/{drive}:load_rom", "drives.load_rom_upload", body=data,
                         filename="drive.rom")

    async def drive_set_mode(self, drive: DriveId, mode: DriveMode) -> None:
        await self._call("PUT", f"/v1/drives/{drive}:set_mode", "drives.set_mode", params={"mode": mode})

    # --------------------------------------------------------------- runners
    async def sid_play(self, file: str, songnr: int | None = None) -> None:
        await self._call("PUT", "/v1/runners:sidplay", "runners.sidplay", params={"file": file, "songnr": songnr})

    async def sid_play_upload(self, data: bytes, songnr: int | None = None, filename: str = "tune.sid") -> None:
        await self._call("POST", "/v1/runners:sidplay", "runners.sidplay_upload", params={"songnr": songnr},
                         body=data, filename=filename)

    async def mod_play(self, file: str) -> None:
        await self._call("PUT", "/v1/runners:modplay", "runners.modplay", params={"file": file})

    async def mod_play_upload(self, data: bytes, filename: str = "tune.mod") -> None:
        await self._call("POST", "/v1/runners:modplay", "runners.modplay_upload", body=data, filename=filename)

    async def load_prg(self, file: str) -> None:
        await self._call("PUT", "/v1/runners:load_prg", "runners.load_prg", params={"file": file})

    async def load_prg_upload(self, data: bytes, filename: str = "program.prg") -> None:
        await self._call("POST", "/v1/runners:load_prg", "runners.load_prg_upload", body=data, filename=filename)

    async def run_prg(self, file: str) -> None:
        await self._call("PUT", "/v1/runners:run_prg", "runners.run_prg", params={"file": file})

    async def run_prg_upload(self, data: bytes, filename: str = "program.prg") -> None:
        await self._call("POST", "/v1/runners:run_prg", "runners.run_prg_upload", body=data, filename=filename)

    async def run_crt(self, file: str) -> None:
        await self._call("PUT", "/v1/runners:run_crt", "runners.run_crt", params={"file": file})

    async def run_crt_upload(self, data: bytes, filename: str = "cart.crt") -> None:
        await self._call("POST", "/v1/runners:run_crt", "runners.run_crt_upload", body=data, filename=filename)

    # --------------------------------------------------------------- streams
    async def stream_start(self, stream: StreamName, target: str) -> None:
        await self._call("PUT", f"/v1/streams/{stream}:start", f"streams.{stream}.start", params={"ip": target})

    async def stream_stop(self, stream: StreamName) -> None:
        await self._call("PUT", f"/v1/streams/{stream}:stop", f"streams.{stream}.stop")

    # --------------------------------------------------------------- configs
    async def config_categories(self) -> list[str]:
        data = (await self._call("GET", "/v1/configs", "configs.list")).json() or {}
        return list(data.get("categories", []))

    async def config_category(self, category: str) -> dict[str, Any]:
        return (await self._call("GET", f"/v1/configs/{quote(category, safe='*')}", "configs.category")).json() or {}

    async def config_item(self, category: str, item: str) -> dict[str, Any]:
        path = f"/v1/configs/{quote(category, safe='*')}/{quote(item, safe='*')}"
        return (await self._call("GET", path, "configs.item")).json() or {}

    async def config_set(self, category: str, item: str, value: str) -> None:
        path = f"/v1/configs/{quote(category, safe='')}/{quote(item, safe='')}"
        await self._call("PUT", path, "configs.set", params={"value": value})

    async def config_save_to_flash(self) -> None:
        """Persists the running configuration. Never called automatically."""
        await self._call("PUT", "/v1/configs:save_to_flash", "configs.save_to_flash")

    async def config_load_from_flash(self) -> None:
        await self._call("PUT", "/v1/configs:load_from_flash", "configs.load_from_flash")

    # ----------------------------------------------------------------- files
    async def file_info(self, path: str) -> dict[str, Any]:
        path = path if path.startswith("/") else "/" + path
        return (await self._call("GET", f"/v1/files{quote(path)}:info", "files.info")).json() or {}
