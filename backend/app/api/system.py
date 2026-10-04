"""Settings, setup wizard, AI, command, audit, troubleshooting, Assembly64 and vision endpoints."""

from __future__ import annotations

import platform
import sys
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.ai.intents import INTENT_HELP, Intent
from app.container import Container
from app.library.scanner import scan_root
from app.services import assembly64 as a64
from app.version import VERSION

from .deps import get_container, get_source

router = APIRouter()


class TestConnectionBody(BaseModel):
    host: str = Field(..., min_length=1, max_length=253)
    port: int = Field(80, ge=1, le=65535)
    password: str = ""
    protocol: Literal["http", "https"] = "http"


class CommandBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=300, examples=["Play Bruce Lee"])
    context: dict[str, Any] = Field(default_factory=dict,
                                    description="Optional: selectedGameId, menuOpen")
    confirm: bool = False
    use_ai: bool = True


class IntentBody(BaseModel):
    intent: Intent
    context: dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False


# ------------------------------------------------------------------ system
@router.get("/api/health", tags=["system"])
async def health(request: Request, c: Container = Depends(get_container)):
    build = getattr(request.app.state, "frontend_build", None)
    return {"ok": True, "version": VERSION, "connected": c.device.connected, "simulated": c.settings.SIMULATE_C64,
            "setupComplete": c.settings.SETUP_COMPLETE, "frontendBuild": build() if build else None}


@router.get("/api/settings", tags=["system"], summary="Current settings (secrets redacted)")
async def get_settings(c: Container = Depends(get_container)):
    return c.settings.public_dict()


@router.put("/api/settings", tags=["system"], summary="Update settings (persisted to DATA_DIR/settings.json)")
async def put_settings(changes: dict[str, Any], c: Container = Depends(get_container)):
    from app.config import AUTH_FIELDS
    changes = {k: v for k, v in changes.items() if k not in AUTH_FIELDS}  # password: /api/auth only
    try:
        s = await c.apply_settings(changes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return s.public_dict()


@router.post("/api/setup/test-connection", tags=["system"], summary="Test a device without saving settings")
async def test_connection(body: TestConnectionBody, c: Container = Depends(get_container)):
    return await c.device.test_connection(body.host, body.port, body.password, body.protocol)


@router.get("/api/ai", tags=["ai"])
async def ai_status(c: Container = Depends(get_container)):
    return c.ai_status()


class AITestBody(BaseModel):
    """Optional unsaved values to test; empty fields fall back to the saved settings."""
    AI_PROVIDER: str | None = None
    AI_BASE_URL: str | None = None
    AI_MODEL: str | None = None
    AI_API_KEY: str | None = None


@router.post("/api/ai/test", tags=["ai"], summary="Ping the AI provider (saved settings, or unsaved values in the body)")
async def ai_test(body: AITestBody | None = None, c: Container = Depends(get_container)):
    from app.ai.providers import make_provider
    from app.config import Settings
    from app.logging_setup import register_secret

    overrides = {k: v for k, v in (body.model_dump() if body else {}).items() if v not in (None, "")}
    if not overrides:
        return {**c.ai_status(), "ping": await c.provider.ping(), "unsaved": False}
    register_secret(overrides.get("AI_API_KEY"))
    try:
        trial = Settings.model_validate({**c.settings.model_dump(), **overrides})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    provider = make_provider(trial)
    return {**provider.describe(), "ping": await provider.ping(), "unsaved": True}


# --------------------------------------------------------------------- mcp
@router.get("/api/mcp/info", tags=["mcp"], summary="How to connect MCP clients (URLs, stdio command, tools)")
async def mcp_info(request: Request, c: Container = Depends(get_container)):
    import sys
    from pathlib import Path

    from app.services.network import access_urls

    s = c.settings
    port = request.url.port or s.WEB_PORT
    others = [u["url"] for u in await access_urls(port, s.C64_ULTIMATE_HOST, s.PUBLIC_URL) if u["kind"] != "other"]
    backend = Path(__file__).resolve().parents[2]
    tools: list[str] = []
    try:
        from app.mcp.server import mcp
        tools = sorted(t.name for t in await mcp.list_tools())
    except ImportError:
        pass
    return {
        "enabled": s.MCP_ENABLED and bool(tools),
        "localUrl": f"http://localhost:{port}/mcp",
        "lanUrls": [f"{u}/mcp" for u in others],
        "tokenSet": bool(s.MCP_TOKEN),
        "allowedHosts": s.MCP_ALLOWED_HOSTS,
        "stdio": {"python": sys.executable, "script": str(backend / "c64_mcp.py"),
                  "apiBase": f"http://127.0.0.1:{port}"},
        "tools": tools,
    }


# ------------------------------------------------------------ other devices
@router.get("/api/access", tags=["system"], summary="Addresses phones, tablets and other computers can open")
async def access_info(request: Request, c: Container = Depends(get_container)):
    from app.services.network import access_urls

    s = c.settings
    port = request.url.port or s.WEB_PORT
    host = (s.WEB_HOST or "").strip().lower()
    return {
        "port": port,
        "urls": await access_urls(port, s.C64_ULTIMATE_HOST, s.PUBLIC_URL),
        "localOnly": host in {"127.0.0.1", "localhost", "::1"},
        "viewingFrom": request.client.host if request.client else None,
    }


@router.get("/api/access/qr.svg", tags=["system"], summary="QR code (SVG) for an address")
async def access_qr(url: str):
    from fastapi.responses import Response

    if len(url) > 300 or not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be an http(s) address")
    try:
        import segno
    except ImportError as exc:
        raise HTTPException(501, "QR codes need the 'segno' package") from exc
    import io

    buf = io.BytesIO()
    segno.make(url, error="m", micro=False).save(buf, kind="svg", scale=8, border=2, dark="#000", light="#fff",
                                                 xmldecl=False, svgns=True)
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


# --------------------------------------------------------------------- ask
class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=300)


@router.post("/api/ask", tags=["ai"], summary="Ask a question (AI answer, grounded in Brave web search when set up)")
async def ask(body: AskBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    from app.services.ask import AskError
    try:
        async with c.audit.action(source, "ask", body.question) as rec:
            result = await c.ask.ask(body.question)
            rec.set_response({"games": len(result["games"]), "webSearch": result["webSearch"]})
            return result
    except AskError as exc:
        raise HTTPException(409, str(exc)) from exc


class SearchTestBody(BaseModel):
    BRAVE_API_KEY: str = ""


@router.post("/api/ask/test-search", tags=["ai"], summary="Check a Brave Search API key (unsaved key optional)")
async def test_search(body: SearchTestBody | None = None, c: Container = Depends(get_container)):
    from app.services.ask import AskError, brave_search
    key = (body.BRAVE_API_KEY if body else "") or c.settings.BRAVE_API_KEY
    if not key:
        return {"ok": False, "error": "No Brave Search API key set"}
    try:
        results = await brave_search("Commodore 64 best games", key, count=3)
    except AskError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "results": len(results), "sample": results[0]["title"] if results else None}


# ----------------------------------------------------------------- command
@router.post("/api/command", tags=["command"], summary="Natural-language command → validated intent → action")
async def command(body: CommandBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    result = await c.router.handle_text(body.text, source="command" if source == "api" else source,
                                        context=body.context, confirm=body.confirm, use_ai=body.use_ai)
    return result.to_dict()


@router.post("/api/command/interpret", tags=["command"], summary="Interpret only (dry run, nothing executed)")
async def interpret(body: CommandBody, c: Container = Depends(get_container)):
    intent, meta = await c.engine.interpret(body.text, body.context, use_ai=body.use_ai)
    return {"intent": intent.model_dump(mode="json", exclude_none=True), "interpretation": meta}


@router.post("/api/intent", tags=["command"], summary="Execute a structured intent (for agents)")
async def run_intent(body: IntentBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    result = await c.router.execute(body.intent, source=source, context=body.context, confirm=body.confirm)
    return result.to_dict()


@router.get("/api/command/examples", tags=["command"])
async def examples():
    return [{"intent": k.value, "example": v} for k, v in INTENT_HELP.items()]


# ------------------------------------------------------------------- audit
@router.get("/api/audit", tags=["audit"])
async def audit(limit: int = 100, offset: int = 0, failures: bool = False, source: str | None = None,
                c: Container = Depends(get_container)):
    return c.audit.list(min(limit, 500), offset, failures, source)


@router.get("/api/troubleshooting", tags=["audit"], summary="Diagnostics bundle (no secrets)")
async def troubleshooting(c: Container = Depends(get_container)):
    st = c.device.status()
    return {
        "app": {"version": VERSION, "python": sys.version.split()[0], "platform": platform.platform(),
                "time": time.time()},
        "settings": c.settings.public_dict(),
        "device": {k: st[k] for k in ("configured", "simulated", "connected", "host", "baseUrl", "lastError",
                                      "lastContact", "apiVersion", "info", "inputMode")},
        "capabilities": st["capabilities"],
        "streams": st["streams"],
        "recentCalls": list(c.audit.recent_calls)[-100:],
        "recentFailures": c.audit.list(20, 0, True),
        "ai": c.ai_status(),
        "websocketClients": c.hub.subscriber_count,
        "hints": _hints(c, st),
    }


def _hints(c: Container, st: dict[str, Any]) -> list[str]:
    hints = []
    if not st["configured"]:
        hints.append("No device configured: run the setup wizard or set C64_ULTIMATE_HOST.")
    elif not st["connected"]:
        err = st.get("lastError") or ""
        if "403" in err or "auth" in err.lower():
            hints.append("HTTP 403: the Ultimate has a network password. Set it in Settings (sent as X-Password).")
        else:
            hints.append("Cannot reach the Ultimate. Check the IP/hostname, that the Web Remote Control / REST "
                         "service is enabled in the Ultimate network settings, and that both are on the same LAN.")
    caps = st["capabilities"]["details"]
    if caps.get("directKeyboard", {}).get("state") == "unsupported":
        hints.append("This firmware has no REST input API: keyboard uses the guarded legacy keyboard buffer and "
                     "joystick injection is unavailable. Newer firmware adds POST /v1/machine:input.")
    if caps.get("menuScreen", {}).get("state") in ("unknown", "unsupported"):
        hints.append("Menu screen reading is not verified: open the Ultimate menu and press Refresh on the Menu page.")
    for kind in ("video", "audio"):
        if st["streams"][kind].get("error"):
            hints.append(f"{kind} stream: {st['streams'][kind]['error']} (another program may be using the port).")
    return hints


# --------------------------------------------------------------- assembly64
class GenerateBody(BaseModel):
    name: str = "Home Assembly"
    host: str
    port: int = 8000
    client_id: str = "Spiffy"
    merge_with_device: bool = True


class HomeSearchBody(BaseModel):
    server: str = Field(..., examples=["http://192.168.1.20:8000"])
    query: str = Field(..., min_length=1, max_length=80)
    category: str | None = None


class HomeImportBody(BaseModel):
    server: str
    entry_id: str
    category: str
    content_id: str
    filename: str


@router.get("/api/assembly64/server-json", tags=["assembly64"], summary="Read /flash/config/server.json (FTP, read-only)")
async def server_json(c: Container = Depends(get_container)):
    if c.settings.SIMULATE_C64:
        return {"ok": False, "error": "not available in simulation mode"}
    return await a64.read_server_json(c.settings.C64_ULTIMATE_HOST, c.settings.C64_ULTIMATE_PASSWORD)


@router.post("/api/assembly64/generate", tags=["assembly64"],
             summary="Generate a server.json entry + instructions (never written to the device)")
async def generate(body: GenerateBody, c: Container = Depends(get_container)):
    try:
        entry = a64.generate_server_entry(body.name, body.host, body.port, body.client_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    existing = None
    if body.merge_with_device and not c.settings.SIMULATE_C64 and c.settings.C64_ULTIMATE_HOST:
        current = await a64.read_server_json(c.settings.C64_ULTIMATE_HOST, c.settings.C64_ULTIMATE_PASSWORD)
        existing = current.get("parsed") if current.get("ok") else None
    return {"entry": entry, "merged": a64.merged_server_json(existing, entry), "basedOnDevice": existing is not None,
            "instructions": a64.INSTRUCTIONS}


@router.post("/api/assembly64/search", tags=["assembly64"], summary="Search a Home Assembly 64 server")
async def home_search(body: HomeSearchBody):
    try:
        return await a64.HomeAssemblyClient(body.server).search(body.query, body.category)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"server error: {exc}") from exc


@router.get("/api/assembly64/entries", tags=["assembly64"])
async def home_entries(server: str, entry_id: str, category: str):
    try:
        return await a64.HomeAssemblyClient(server).entries(entry_id, category)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"server error: {exc}") from exc


@router.post("/api/assembly64/import", tags=["assembly64"],
             summary="Download a Home Assembly 64 file into DATA_DIR/home-assembly and add it to the library")
async def home_import(body: HomeImportBody, c: Container = Depends(get_container)):
    import asyncio

    cache = c.settings.data_path / "home-assembly"
    cache.mkdir(parents=True, exist_ok=True)
    try:
        dl = await a64.Assembly64Client(body.server, c.settings.ASSEMBLY64_CLIENT_ID).download(
            body.entry_id, body.category, body.content_id, body.filename)
        target = a64.safe_cache_path(cache, dl.filename or body.filename)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"download failed: {exc}") from exc
    target.write_bytes(dl.data)

    def _scan():
        with c.sf() as s:
            return scan_root(s, str(cache))
    result = await asyncio.to_thread(_scan)
    return {"ok": True, "path": str(target), "size": len(dl.data), "scan": result.__dict__}


# ------------------------------------------------------------ online catalog
class CatalogEntryBody(BaseModel):
    id: str = Field(..., max_length=64)
    category: int = Field(..., ge=0, le=999)
    name: str | None = Field(None, max_length=200)
    group: str | None = Field(None, max_length=200)
    year: int | None = None
    source: str | None = Field(None, max_length=80)
    disk: int | None = Field(None, ge=1, le=20)


@router.get("/api/catalog", tags=["catalog"], summary="Assembly64 catalog configuration")
async def catalog_status(c: Container = Depends(get_container)):
    return c.catalog.status()


@router.get("/api/catalog/search", tags=["catalog"], summary="Search the Assembly64 online catalog")
async def catalog_search(q: str, kind: Literal["games", "music", "demos", "tools", "all"] = "games",
                         offset: int = 0, c: Container = Depends(get_container)):
    if not q.strip() or len(q) > 80:
        raise HTTPException(400, "query must be 1..80 characters")
    if offset == 0:
        c.taste.record("search", text=q)
    try:
        return await c.catalog.search(q, kind, offset=max(0, offset))
    except a64.Assembly64Error as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/catalog/entries/{category}/{entry_id}", tags=["catalog"], summary="Files of a catalog entry")
async def catalog_entries(category: int, entry_id: str, c: Container = Depends(get_container)):
    try:
        files = await c.catalog.client().entries(entry_id, category)
    except a64.Assembly64Error as exc:
        raise HTTPException(502, str(exc)) from exc
    chosen = {f.get("id") for f in a64.choose_files(files)}
    return [{**f, "selected": f.get("id") in chosen} for f in files]


@router.post("/api/catalog/fetch", tags=["catalog"], summary="Download an entry into the library (no launch)")
async def catalog_fetch(body: CatalogEntryBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    try:
        async with c.audit.action(source, "catalog.fetch", body.name, body.model_dump()) as rec:
            game_id = await c.catalog.fetch(body.id, body.category, body.model_dump())
            rec.set_response({"gameId": game_id})
    except a64.Assembly64Error as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"gameId": game_id}


@router.post("/api/catalog/play", tags=["catalog"], summary="Download (if needed) and launch a catalog entry")
async def catalog_play(body: CatalogEntryBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    if not c.device.connected:
        raise HTTPException(503, f"C64 Ultimate not connected: {c.device.last_error or 'offline'}")
    try:
        async with c.audit.action(source, "catalog.fetch", body.name, body.model_dump()) as rec:
            game_id = await c.catalog.fetch(body.id, body.category, body.model_dump())
            rec.set_response({"gameId": game_id})
    except a64.Assembly64Error as exc:
        raise HTTPException(502, str(exc)) from exc
    job = await c.launcher.launch(game_id, disk=body.disk, source=source, user_command=body.name)
    return {"gameId": game_id, "job": job.to_dict()}


# -------------------------------------------------------------------- vision
class VisionStartBody(BaseModel):
    goal: str = Field(..., min_length=1, max_length=200, examples=["press whatever starts the game"])
    port: int = Field(2, ge=1, le=2)
    confirm: bool = False


@router.get("/api/vision", tags=["vision"])
async def vision_status(c: Container = Depends(get_container)):
    return c.vision.status()


@router.post("/api/vision/start", tags=["vision"], summary="Explicitly start an AI vision session (experimental)")
async def vision_start(body: VisionStartBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    if not body.confirm:
        raise HTTPException(400, "vision control requires confirm=true")
    if source == "mcp":
        raise HTTPException(403, "vision control cannot be started by an MCP client")
    try:
        async with c.audit.action(source, "vision.start", body.goal):
            return await c.vision.start(body.goal, body.port)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/api/vision/stop", tags=["vision"])
async def vision_stop(c: Container = Depends(get_container)):
    return await c.vision.stop()



