"""PRG / CRT / SID / MOD runners.

Files that live on the Ultimate's own storage are started with ``PUT`` + ``file=``;
files on this computer are uploaded with ``POST`` (the source file is only read).
"""

from __future__ import annotations

from pathlib import Path

from .capabilities import CapabilityMatrix
from .client import UltimateClient, UltimateNotFound
from .input import InputUnsupported

MAX_UPLOAD_BYTES = 16 * 1024 * 1024

_KIND_CAP = {"run_prg": "runPrg", "load_prg": "loadPrg", "run_crt": "runCrt", "sid": "sidPlayback",
             "mod": "modPlayback"}


class RunnerService:
    def __init__(self, client: UltimateClient, caps: CapabilityMatrix):
        self.client = client
        self.caps = caps

    async def run(self, kind: str, *, device_path: str | None = None, local_path: Path | None = None,
                  songnr: int | None = None) -> None:
        cap = _KIND_CAP.get(kind)
        if cap is None:
            raise ValueError(f"unknown runner {kind}")
        if not self.caps.usable(cap):
            raise InputUnsupported(f"{cap} is not supported by this device ({self.caps.state(cap)})")
        if bool(device_path) == bool(local_path):
            raise ValueError("exactly one of device_path / local_path is required")
        try:
            if device_path:
                await self._put(kind, device_path, songnr)
            else:
                assert local_path is not None
                if local_path.stat().st_size > MAX_UPLOAD_BYTES:
                    raise ValueError("file too large to upload")
                await self._post(kind, local_path.read_bytes(), local_path.name, songnr)
        except UltimateNotFound:
            # 404 on PUT usually means "file not found on device"; only demote on uploads.
            if local_path:
                self.caps.record_use(cap, False, not_found=True)
            raise
        self.caps.record_use(cap, True)

    async def _put(self, kind: str, file: str, songnr: int | None) -> None:
        c = self.client
        if kind == "run_prg":
            await c.run_prg(file)
        elif kind == "load_prg":
            await c.load_prg(file)
        elif kind == "run_crt":
            await c.run_crt(file)
        elif kind == "sid":
            await c.sid_play(file, songnr)
        elif kind == "mod":
            await c.mod_play(file)

    async def _post(self, kind: str, data: bytes, name: str, songnr: int | None) -> None:
        c = self.client
        if kind == "run_prg":
            await c.run_prg_upload(data, name)
        elif kind == "load_prg":
            await c.load_prg_upload(data, name)
        elif kind == "run_crt":
            await c.run_crt_upload(data, name)
        elif kind == "sid":
            await c.sid_play_upload(data, songnr, name)
        elif kind == "mod":
            await c.mod_play_upload(data, name)
