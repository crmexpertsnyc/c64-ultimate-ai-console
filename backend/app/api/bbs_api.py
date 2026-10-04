"""📟 BBS directory API and the browser-terminal relay (WebSocket ↔ telnet/TCP).

Relay rules (see services/bbs_net.py for the destination policy):
* The browser names an APPROVED board id — never a host or port.
* The board's host is resolved and checked server-side, then the connection goes to that checked IP (pinned).
* The WebSocket's Origin must match the page's own host; the console's sign-in applies (AuthGate on /ws).
* Limits: sessions overall / per device, connection attempts per 10 minutes, idle timeout, maximum session length,
  bytes each way, frame size, connect timeout. The TCP socket is closed whenever the session ends.
* Nothing typed or received is logged or stored; telnet is unencrypted and the page says so.

Wire format: binary frames carry terminal bytes both ways (the browser decodes and draws them — it never renders
remote content as HTML). Text frames are small JSON control messages: from the server {"type": "status"|"error"…},
from the browser {"type": "resize", "cols", "rows"}.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.auth import cookie_from, is_local, session_valid
from app.container import Container
from app.services import bbs_net

from .deps import get_container

router = APIRouter(tags=["bbs"])

CONNECT_TIMEOUT = 10.0
MAX_SECONDS = 3 * 3600
MAX_DOWN = 50 * 1024 * 1024
MAX_UP = 2 * 1024 * 1024
MAX_FRAME = 4096
READ_CHUNK = 4096
WATCH_EVERY = 5.0


def idle_seconds(c: Container) -> float:
    return max(1, int(getattr(c.settings, "BBS_IDLE_MINUTES", 20) or 20)) * 60.0


def is_admin(scope: dict[str, Any], c: Container) -> bool:
    """This computer, or a device signed in with the console password. Without a password, only this computer."""
    if is_local(scope):
        return True
    return bool(c.config.settings.APP_PASSWORD_HASH) and session_valid(c.config, cookie_from(scope))


def require_admin(request: Request, c: Container = Depends(get_container)) -> Container:
    if not is_admin(request.scope, c):
        raise HTTPException(403, "Approving and editing boards needs the console password: set one in Settings → "
                                 "Security and sign in, or use this on the console's own computer.")
    return c


def _errors(fn):  # noqa: ANN001, ANN202
    try:
        return fn()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class UserBody(BaseModel):
    favorite: bool | None = None
    notes: str | None = Field(None, max_length=4000)


class EditBody(BaseModel):
    name: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=2000)
    location: str | None = Field(None, max_length=160)
    website: str | None = Field(None, max_length=400)
    protocol: Literal["telnet", "raw"] | None = None
    petscii: Literal["confirmed", "unverified", "unknown"] | None = None
    ansi: Literal["confirmed", "unverified", "unknown"] | None = None
    compat_note: str | None = Field(None, max_length=300)


class AddBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(23, ge=1, le=65535)
    protocol: Literal["telnet", "raw"] = "telnet"
    description: str | None = Field(None, max_length=2000)
    website: str | None = Field(None, max_length=400)
    source_url: str | None = Field(None, max_length=400)


# ------------------------------------------------------------------ directory
@router.get("/api/bbs", summary="The BBS directory (approved boards; admins also see pending ones)")
async def boards(request: Request, q: str | None = Query(None, max_length=80),
                 terminal: Literal["petscii", "ansi"] | None = None, favorites: bool = False,
                 reachable: bool = False, review: str | None = Query(None, max_length=10),
                 c: Container = Depends(get_container)):
    admin = is_admin(request.scope, c)
    return {**c.bbs.list(q, terminal, favorites, reachable, review, admin), "admin": admin}


@router.get("/api/bbs/boards/{board_id}", summary="One board")
async def board(board_id: int, request: Request, c: Container = Depends(get_container)):
    return _errors(lambda: c.bbs.get(board_id, is_admin(request.scope, c)))


@router.put("/api/bbs/boards/{board_id}/me", summary="⭐ Favorite and 📝 notes (yours)")
async def user_data(board_id: int, body: UserBody, c: Container = Depends(get_container)):
    return _errors(lambda: c.bbs.set_user(board_id, body.favorite, body.notes))


@router.get("/api/bbs/hardware", summary="The C64 Ultimate's modem settings (read only) for dialing from the C64")
async def hardware(c: Container = Depends(get_container)):
    out: dict[str, Any] = {"dialOnC64": bool(c.settings.BBS_DIAL_ON_C64), "modem": None, "error": None}
    client = c.device.client
    if client is None or c.device.simulator is not None:
        out["error"] = "the console isn't connected to a real C64 Ultimate"
        return out
    try:
        data = await asyncio.wait_for(client.config_category("Modem Settings"), 6)
        items = data.get("Modem Settings") if isinstance(data.get("Modem Settings"), dict) else data
        keep = {}
        for k, v in (items or {}).items():
            if isinstance(v, dict):
                v = v.get("current", v.get("value"))
            if isinstance(v, str | int | float | bool):
                keep[str(k)] = v
        out["modem"] = keep
    except Exception as exc:  # noqa: BLE001 - informational only
        out["error"] = f"couldn't read the modem settings ({type(exc).__name__})"
    return out


# ------------------------------------------------------------------ admin
@router.post("/api/bbs/refresh", summary="Admin: refresh the directory from its sources now")
async def refresh(source: str | None = Query(None, max_length=20), c: Container = Depends(require_admin)):
    return await c.bbs.refresh(source)


@router.post("/api/bbs/boards/{board_id}/check", summary="Admin: check reachability of one board now")
async def check(board_id: int, c: Container = Depends(require_admin)):
    try:
        return await c.bbs.check_one(board_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/api/bbs/boards/{board_id}/{decision}", summary="Admin: approve / reject a board")
async def review(board_id: int, decision: Literal["approve", "reject", "pending"],
                 c: Container = Depends(require_admin)):
    to = {"approve": "approved", "reject": "rejected", "pending": "pending"}[decision]
    return _errors(lambda: c.bbs.review(board_id, to))


@router.patch("/api/bbs/boards/{board_id}", summary="Admin: correct a board (protocol, compatibility, text)")
async def edit(board_id: int, body: EditBody, c: Container = Depends(require_admin)):
    return _errors(lambda: c.bbs.edit(board_id, body.model_dump(exclude_unset=True)))


@router.post("/api/bbs/boards", summary="Admin: add a board by hand (it starts as pending)")
async def add(body: AddBody, c: Container = Depends(require_admin)):
    return _errors(lambda: c.bbs.add_manual(body.model_dump()))


# ------------------------------------------------------------------ relay
def origin_ok(ws: WebSocket) -> bool:
    """The page that opened the socket must be this console itself (same host and port)."""
    origin = ws.headers.get("origin")
    # behind a proxy (Tailscale Serve) the page's host arrives as X-Forwarded-Host; a browser page can't set it
    host = (ws.headers.get("x-forwarded-host") or ws.headers.get("host") or "").split(",")[0].strip().lower()
    if not origin or not host:
        return False
    try:
        o = urlsplit(origin)
    except ValueError:
        return False
    if o.scheme not in ("http", "https"):
        return False
    netloc = (o.netloc or "").lower()

    def bare(h: str) -> str:
        return h.removesuffix(":443" if o.scheme == "https" else ":80")
    return bool(netloc) and bare(netloc) == bare(host)


@router.websocket("/ws/bbs/{board_id}")
async def relay(ws: WebSocket, board_id: int, mode: str = "ansi", protocol: str = "auto",
                cols: int = 80, rows: int = 25):
    c: Container = ws.app.state.container
    svc = c.bbs
    if not origin_ok(ws):
        await ws.close(code=4403)
        return
    await ws.accept()

    async def status(state: str, **extra: Any) -> None:
        with contextlib.suppress(Exception):
            await ws.send_text(json.dumps({"type": "status", "state": state, **extra}))

    async def fail(detail: str, code: int = 1000) -> None:
        with contextlib.suppress(Exception):
            await ws.send_text(json.dumps({"type": "error", "detail": detail}))
            await ws.close(code=code)

    b = svc.board(board_id)
    if b is None or b.review != "approved":
        await fail("This board isn't approved for browser connections yet.", 4404)
        return
    from app.auth import client_id
    who = client_id(ws.scope)
    sid = secrets.token_hex(8)
    reason = svc.limits.acquire(sid, who, board_id)
    if reason:
        await fail(reason, 4429)
        return
    writer: asyncio.StreamWriter | None = None
    try:
        await status("connecting", host=b.host, port=b.port)
        try:
            reader, writer, _ip = await bbs_net.connect_pinned(b.host, b.port, resolver=svc.resolver,
                                                                opener=svc.opener, timeout=CONNECT_TIMEOUT)
        except bbs_net.DestinationError as exc:
            await fail(f"Couldn't connect: {exc}. The board may be down, or busy — try again later.")
            return
        use_telnet = (protocol if protocol in ("telnet", "raw") else b.protocol) == "telnet"
        tn = bbs_net.TelnetClient("PETSCII" if mode == "petscii" else "ANSI", max(20, min(cols, 255)),
                                  max(10, min(rows, 255)))
        with contextlib.suppress(Exception):
            svc.set_user(board_id, connected=True)
        await status("connected", host=b.host, port=b.port, protocol="telnet" if use_telnet else "raw")

        idle = idle_seconds(c)
        started = last_input = time.monotonic()
        down = up = 0
        ended = "closed"

        async def from_board() -> str:
            nonlocal down
            while True:
                chunk = await reader.read(READ_CHUNK)
                if not chunk:
                    return "The board closed the connection."
                down += len(chunk)
                if down > MAX_DOWN:
                    return "Session data limit reached — reconnect to continue."
                if use_telnet:
                    data, replies = tn.feed(chunk)
                    if replies:
                        writer.write(replies)
                        await writer.drain()
                else:
                    data = chunk
                if data:
                    await ws.send_bytes(data)

        async def from_browser() -> str:
            nonlocal up, last_input
            while True:
                msg = await ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    return "closed"
                raw = msg.get("bytes")
                if raw is not None:
                    if len(raw) > MAX_FRAME:
                        return "Input too large."
                    up += len(raw)
                    if up > MAX_UP:
                        return "Session input limit reached — reconnect to continue."
                    last_input = time.monotonic()
                    writer.write(tn.encode(raw) if use_telnet else raw)
                    await writer.drain()
                    continue
                text = msg.get("text")
                if text is None or len(text) > 512:
                    continue
                try:
                    ctl = json.loads(text)
                except ValueError:
                    continue
                if isinstance(ctl, dict) and ctl.get("type") == "resize" and use_telnet:
                    with contextlib.suppress(TypeError, ValueError):
                        sb = tn.resize(int(ctl.get("cols", 80)), int(ctl.get("rows", 25)))
                        if sb:
                            writer.write(sb)
                            await writer.drain()
                elif isinstance(ctl, dict) and ctl.get("type") == "close":
                    return "closed"

        async def watchdog() -> str:
            while True:
                await asyncio.sleep(WATCH_EVERY)
                now = time.monotonic()
                if now - last_input > idle:
                    return f"Disconnected after {round(idle / 60)} minutes without typing."
                if now - started > MAX_SECONDS:
                    return "Sessions are limited to 3 hours — reconnect to continue."

        tasks = [asyncio.create_task(t()) for t in (from_board, from_browser, watchdog)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            t = next(iter(done))
            try:
                ended = t.result()
            except (WebSocketDisconnect, RuntimeError):
                ended = "closed"
            except (OSError, ConnectionError):
                ended = "The connection to the board was lost."
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(BaseException):
                    await t
        if ended != "closed":
            await status("disconnected", detail=ended)
            with contextlib.suppress(Exception):
                await ws.close()
    except WebSocketDisconnect:
        pass
    finally:
        svc.limits.release(sid)
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(writer.wait_closed(), 2)
