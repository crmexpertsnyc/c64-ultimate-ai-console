"""Low-level transports for the Ultimate REST API.

``HttpTransport`` talks to a real device. ``SimulatedTransport`` routes the same
requests into an in-process ``SimulatedUltimate`` so the typed client, the
capability prober and every service above them run unchanged in SIMULATE_C64 mode.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger("c64.transport")


@dataclass
class UltimateResponse:
    status: int
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def json(self) -> Any:
        try:
            return json.loads(self.content.decode("utf-8", errors="replace") or "null")
        except ValueError:
            return None

    @property
    def errors(self) -> list[str]:
        data = self.json()
        if isinstance(data, dict) and isinstance(data.get("errors"), list):
            return [str(e) for e in data["errors"]]
        return []

    def text_excerpt(self, n: int = 240) -> str:
        if "octet-stream" in self.content_type:
            return f"<{len(self.content)} bytes binary>"
        return self.content[:n].decode("utf-8", errors="replace")


class Transport(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: bytes | None = None,
        json_body: Any = None,
        filename: str | None = None,
    ) -> UltimateResponse: ...

    async def aclose(self) -> None: ...


class HttpTransport:
    def __init__(self, base_url: str, password: str = "", timeout: float = 8.0, upload_mode: str = "raw",
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url
        self._password = password
        self.upload_mode = upload_mode
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": "c64-ai-console"}
        if self._password:
            headers["X-Password"] = self._password
        return headers

    async def request(self, method, path, params=None, body=None, json_body=None, filename=None):
        headers = self._headers()
        kwargs: dict[str, Any] = {"params": {k: v for k, v in (params or {}).items() if v is not None}}
        if json_body is not None:
            kwargs["json"] = json_body
        elif body is not None:
            if self.upload_mode == "multipart":
                kwargs["files"] = {"file": (filename or "upload.bin", body, "application/octet-stream")}
            else:
                headers["Content-Type"] = "application/octet-stream"
                kwargs["content"] = body
        start = time.perf_counter()
        resp = await self._client.request(method, path, headers=headers, **kwargs)
        elapsed = (time.perf_counter() - start) * 1000
        log.debug("%s %s -> %s (%.0f ms)", method, path, resp.status_code, elapsed)
        return UltimateResponse(
            status=resp.status_code,
            content=resp.content,
            headers={k.lower(): v for k, v in resp.headers.items()},
            elapsed_ms=elapsed,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class SimulatedTransport:
    def __init__(self, device: Any):
        self.device = device
        self.base_url = "simulated://c64u"

    async def request(self, method, path, params=None, body=None, json_body=None, filename=None):
        start = time.perf_counter()
        status, content, ctype = await self.device.handle(method, path, params or {}, body, json_body)
        if isinstance(content, (dict, list)):
            content = json.dumps(content).encode()
        return UltimateResponse(
            status=status,
            content=content or b"",
            headers={"content-type": ctype},
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )

    async def aclose(self) -> None:
        return None
