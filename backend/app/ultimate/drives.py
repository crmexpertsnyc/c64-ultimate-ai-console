"""Drive helpers on top of the raw client, gated by the capability matrix."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .capabilities import CapabilityMatrix
from .client import DriveInfo, UltimateClient, UltimateNotFound
from .input import InputUnsupported

DISK_TYPES = {".d64": "d64", ".g64": "g64", ".d71": "d71", ".g71": "g71", ".d81": "d81"}
MAX_UPLOAD_BYTES = 16 * 1024 * 1024


def image_type_for(path: str) -> str | None:
    return DISK_TYPES.get(Path(path).suffix.lower())


class DriveService:
    def __init__(self, client: UltimateClient, caps: CapabilityMatrix):
        self.client = client
        self.caps = caps

    def _need(self, cap: str) -> None:
        if not self.caps.usable(cap):
            raise InputUnsupported(f"{cap} is not supported by this device ({self.caps.state(cap)})")

    async def status(self) -> list[DriveInfo]:
        self._need("driveStatus")
        return await self.client.drives()

    async def mount_device_path(self, drive: str, image: str, mode: str | None = None) -> None:
        """Mount an image that lives on the Ultimate's own storage (e.g. /Usb0/games/x.d64)."""
        self._need("mountDisk")
        await self.client.mount(drive, image, type=image_type_for(image), mode=mode)  # type: ignore[arg-type]
        self.caps.record_use("mountDisk", True)

    async def mount_local_file(self, drive: str, path: Path, mode: str | None = "readonly") -> None:
        """Upload an image from this computer and mount it (read-only by default so the
        source file on disk is never modified)."""
        self._need("mountDisk")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("image too large to upload")
        await self.client.mount_upload(drive, path.read_bytes(), type=image_type_for(path.name),  # type: ignore[arg-type]
                                       mode=mode, filename=path.name)  # type: ignore[arg-type]
        self.caps.record_use("mountDisk", True)

    async def _control(self, cap: str, coro) -> None:  # noqa: ANN001
        self._need(cap)
        try:
            await coro
        except UltimateNotFound:
            self.caps.record_use(cap, False, not_found=True)
            raise
        self.caps.record_use(cap, True)

    async def remove(self, drive: str) -> None:
        await self._control("driveControl", self.client.drive_remove(drive))

    async def reset(self, drive: str) -> None:
        await self._control("driveControl", self.client.drive_reset(drive))

    async def power(self, drive: str, on: bool) -> None:
        await self._control("driveControl", self.client.drive_on(drive) if on else self.client.drive_off(drive))

    async def set_mode(self, drive: str, mode: str) -> None:
        if mode not in ("1541", "1571", "1581"):
            raise ValueError("mode must be 1541, 1571 or 1581")
        await self._control("driveMode", self.client.drive_set_mode(drive, mode))  # type: ignore[arg-type]

    async def load_rom(self, drive: str, device_file: str) -> None:
        await self._control("driveRom", self.client.drive_load_rom(drive, device_file))


def drive_to_dict(d: DriveInfo) -> dict[str, Any]:
    return {"id": d.id, "enabled": d.enabled, "busId": d.bus_id, "type": d.type, "rom": d.rom,
            "imageFile": d.image_file, "imagePath": d.image_path, "mounted": d.mounted,
            "lastError": d.last_error, "partitions": d.partitions}
