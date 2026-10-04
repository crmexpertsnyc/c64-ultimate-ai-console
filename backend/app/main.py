"""FastAPI application entrypoint.

Run:  uvicorn app.main:app --host 0.0.0.0 --port 8064
OpenAPI docs: /docs (Swagger UI), /redoc, /openapi.json
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth as auth_api
from app.api import device, library, media, netplay_api, news_api, profiles_api, smart, streams, system, taste, updates_api
from app.config import ConfigStore
from app.container import Container
from app.logging_setup import setup_logging
from app.services.launcher import LaunchError
from app.ultimate.client import UltimateAuthError, UltimateConnectionError, UltimateError, UltimateNotFound
from app.ultimate.input import InputUnsupported, LegacyStateError
from app.ultimate.menu import MenuFormatError

log = logging.getLogger("c64.main")


def create_app(config: ConfigStore | None = None, start_device: bool = True) -> FastAPI:
    config = config or ConfigStore()
    setup_logging(config.settings.LOG_LEVEL)

    mcp_route, mcp_sessions = _build_mcp(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from contextlib import AsyncExitStack
        container = Container(config)
        app.state.container = container
        async with AsyncExitStack() as stack:
            if mcp_sessions is not None:
                await stack.enter_async_context(mcp_sessions.run())
            if start_device:
                await container.start()
            log.info("C64 Ultimate AI Console ready (device=%s, simulate=%s, ai=%s, mcp=%s)",
                     config.settings.C64_ULTIMATE_HOST or "-", config.settings.SIMULATE_C64,
                     config.settings.AI_PROVIDER, "/mcp" if mcp_route is not None else "off")
            try:
                yield
            finally:
                # Safety: release injected inputs (release_all) and stop streams on shutdown.
                await container.stop()

    app = FastAPI(
        title="C64 Ultimate AI Console API",
        version=system.VERSION,
        description="Safe, capability-aware control of a Commodore 64 Ultimate over its documented REST API. "
                    "Set header `X-C64-Source` (ui|api|mcp) to label audit entries.",
        lifespan=lifespan,
    )
    from app.auth import AuthGate
    from app.profiles import ProfileContext
    # 👪 who is playing (header X-C64-Profile) — inside the auth gate, so only signed-in requests get here
    app.add_middleware(ProfileContext, service_provider=lambda: getattr(getattr(app.state, "container", None), "profiles", None))
    app.add_middleware(AuthGate, config=config)  # inside CORS, so preflight requests are never blocked
    origins = [o.strip() for o in config.settings.CORS_ORIGINS.split(",") if o.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    def err(status: int, exc: Exception, kind: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": str(exc), "kind": kind})

    @app.exception_handler(InputUnsupported)
    async def _unsupported(_: Request, exc: InputUnsupported):
        return err(409, exc, "unsupported")

    @app.exception_handler(LegacyStateError)
    async def _legacy(_: Request, exc: LegacyStateError):
        return err(409, exc, "unsafe_state")

    @app.exception_handler(LaunchError)
    async def _launch(_: Request, exc: LaunchError):
        return err(409, exc, "launch")

    @app.exception_handler(UltimateAuthError)
    async def _auth(_: Request, exc: UltimateAuthError):
        return err(502, exc, "device_auth")

    @app.exception_handler(UltimateNotFound)
    async def _nf(_: Request, exc: UltimateNotFound):
        return err(502, exc, "device_not_found")

    @app.exception_handler(UltimateConnectionError)
    async def _conn(_: Request, exc: UltimateConnectionError):
        return err(503, exc, "device_unreachable")

    @app.exception_handler(UltimateError)
    async def _ult(_: Request, exc: UltimateError):
        return err(502, exc, "device_error")

    @app.exception_handler(MenuFormatError)
    async def _menu(_: Request, exc: MenuFormatError):
        return err(502, exc, "menu_format")

    @app.exception_handler(ValueError)
    async def _value(_: Request, exc: ValueError):
        return err(400, exc, "invalid")

    for r in (auth_api.router, device.router, library.router, system.router, streams.router, media.router,
              taste.router, smart.router, profiles_api.router, netplay_api.router, news_api.router,
              updates_api.router):
        app.include_router(r)
    from app.features import routers
    for r in routers():
        app.include_router(r)

    if mcp_route is not None:
        app.router.routes.append(mcp_route)  # before the SPA catch-all below
    _mount_frontend(app, config)
    return app


def _build_mcp(config: ConfigStore):  # noqa: ANN202
    """MCP endpoint at /mcp, if the optional `mcp` package is installed and MCP_ENABLED is on."""
    if not config.settings.MCP_ENABLED:
        return None, None
    try:
        from app.mcp.mount import build_mcp_route
    except ImportError:
        log.info("MCP package not installed; /mcp endpoint disabled (pip install '.[mcp]')")
        return None, None
    return build_mcp_route(config)


def _mount_frontend(app: FastAPI, config: ConfigStore) -> None:
    candidates = [config.settings.FRONTEND_DIST, "../frontend/dist", "./frontend/dist", "/app/frontend/dist"]
    dist = next((Path(p) for p in candidates if p and (Path(p) / "index.html").is_file()), None)
    if dist is None:
        log.info("frontend build not found; API only (run `npm run build` in frontend/)")
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    def build_id() -> str:
        # The hashed bundle name changes with every `npm run build`; browsers compare it to reload stale tabs.
        names = sorted(p.name for p in (dist / "assets").glob("index-*.js"))
        return names[-1] if names else ""

    app.state.frontend_build = build_id

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith(("api/", "ws", "docs", "redoc", "openapi", "mcp")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        f = (dist / path).resolve()
        if path and f.is_file() and dist.resolve() in f.parents:
            # HTML pages and the emulator's files must never be served stale after an update
            # (browsers revalidate; unchanged files are cheap to confirm).
            fresh = f.suffix == ".html" or path.startswith("emulator/") or path in ("sw.js", "manifest.json")
            return FileResponse(f, headers={"Cache-Control": "no-cache"} if fresh else None)
        if path.startswith(("emulator/", "assets/")):  # missing static file: 404, not the app shell
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        # Never cache the HTML shell, so a refresh always picks up the latest build.
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})


app = create_app()
