from __future__ import annotations

from typing import Any

from fastapi import Header, Request

from app.container import Container

SOURCES = {"ui", "api", "mcp", "command", "vision", "system"}


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_source(x_c64_source: str | None = Header(default=None)) -> str:
    return x_c64_source if x_c64_source in SOURCES else "api"


async def audited(c: Container, source: str, operation: str, coro, intent: dict[str, Any] | None = None):  # noqa: ANN001
    """Run a device coroutine inside an audit record and return its result."""
    async with c.audit.action(source, operation, None, intent) as rec:
        result = await coro
        rec.set_response(result if result is not None else {"ok": True})
    return result
