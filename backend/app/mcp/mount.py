"""Serve the MCP server from inside the console at /mcp (Streamable HTTP).

Access rules (checked on every request, so changing the token in Settings applies immediately):
* MCP_TOKEN set  → every request needs ``Authorization: Bearer <token>``; any host may connect.
* no MCP_TOKEN   → only requests addressed to this machine (localhost / 127.0.0.1 / ::1) or to a
                   host listed in MCP_ALLOWED_HOSTS; requests carrying a browser Origin from another
                   site are refused (protects against DNS-rebinding attacks from web pages).
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any

from mcp.server.transport_security import TransportSecuritySettings

from app.config import ConfigStore

log = logging.getLogger("c64.mcp")

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _host_only(value: str) -> str:
    value = (value or "").strip().lower()
    if value.startswith("["):  # [::1]:8064
        return value.split("]")[0] + "]"
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


async def _deny(send, status: int, message: str) -> None:  # noqa: ANN001
    body = json.dumps({"error": message}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if status == 401:
        headers.append((b"www-authenticate", b'Bearer realm="c64-ultimate-mcp"'))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class MCPGuard:
    """ASGI wrapper enforcing the access rules above before the MCP transport sees the request."""

    def __init__(self, app, config: ConfigStore):  # noqa: ANN001
        self.app = app
        self.config = config

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        s = self.config.settings
        if not s.MCP_ENABLED:
            return await _deny(send, 404, "MCP endpoint is disabled (MCP_ENABLED=false)")
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        token = s.MCP_TOKEN
        if token:
            auth = headers.get("authorization", "")
            given = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not given or not hmac.compare_digest(given, token):
                return await _deny(send, 401, "missing or invalid bearer token")
            return await self.app(scope, receive, send)
        allowed = LOCAL_HOSTS | {h.strip().lower() for h in s.MCP_ALLOWED_HOSTS.split(",") if h.strip()}
        host = _host_only(headers.get("host", ""))
        if host not in allowed:
            return await _deny(send, 403, f"host '{host}' not allowed: set MCP_TOKEN (recommended) or add it to "
                                          "MCP_ALLOWED_HOSTS")
        origin = headers.get("origin")
        if origin:
            o_host = _host_only(origin.split("://", 1)[-1].split("/", 1)[0])
            if o_host not in allowed:
                return await _deny(send, 403, "cross-origin requests are not allowed without MCP_TOKEN")
        return await self.app(scope, receive, send)


def build_mcp_route(config: ConfigStore):  # noqa: ANN201
    """Returns (starlette Route for /mcp, session_manager) — the manager must be run in the app lifespan."""
    from starlette.routing import Route

    from app.mcp.server import mcp, set_api_base

    set_api_base(f"http://127.0.0.1:{config.settings.WEB_PORT}")
    # Host/Origin checks are done by MCPGuard (so they can follow the Settings at runtime).
    sub = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    inner = next(r for r in sub.routes if getattr(r, "path", None) == "/mcp")
    endpoint = inner.app if hasattr(inner, "app") else inner.endpoint
    route = Route("/mcp", endpoint=MCPGuard(endpoint, config))
    return route, mcp.session_manager
