"""👥 Netplay (host-streamed co-op) for Browser Play.

The host's browser runs the game; a friend's browser gets its picture and sound over WebRTC and sends its
joystick back on a data channel (player 2 = the other joystick port). The console only introduces the two
browsers: it relays WebRTC offers / answers / ICE candidates between the host and guests of a room — no game
data passes through it. Rooms live in memory, are identified by a short code, and end when the host leaves.
EmulatorJS's own netplay (lockstep, both sides emulating) is not usable in 4.2.3; streaming avoids needing
both emulators to stay in sync.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.container import Container

from .deps import get_container

log = logging.getLogger("c64.netplay")
router = APIRouter(tags=["netplay"])

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
MAX_GUESTS = 3
ROOM_TTL = 6 * 3600
MAX_MESSAGE = 64 * 1024


class Room:
    def __init__(self, code: str, game_id: int, title: str):
        self.code, self.game_id, self.title = code, game_id, title
        self.host: WebSocket | None = None
        self.guests: dict[str, WebSocket] = {}
        self.names: dict[str, str] = {}
        self.created = time.time()


ROOMS: dict[str, Room] = {}


def _cleanup() -> None:
    now = time.time()
    for code in [c for c, r in ROOMS.items() if now - r.created > ROOM_TTL or (r.host is None and now - r.created > 600)]:
        ROOMS.pop(code, None)


class RoomBody(BaseModel):
    gameId: int


@router.post("/api/netplay/rooms", summary="Host: open a co-op room for the game you are playing in the browser")
async def create_room(body: RoomBody, c: Container = Depends(get_container)):
    from app.models.db import Game
    with c.sf() as s:
        g = s.get(Game, body.gameId)
        if not g:
            raise HTTPException(404, "game not found")
        title = g.title
    _cleanup()
    code = "".join(secrets.choice(ALPHABET) for _ in range(6))
    ROOMS[code] = Room(code, body.gameId, title)
    return {"code": code, "gameId": body.gameId, "title": title, "stun": _stun(c)}


@router.get("/api/netplay/rooms/{code}", summary="Guest: what is being played in this room")
async def room_info(code: str, c: Container = Depends(get_container)):
    r = ROOMS.get(code.upper())
    if not r:
        raise HTTPException(404, "no such room — ask the host for a new link")
    return {"code": r.code, "gameId": r.game_id, "title": r.title, "hostOnline": r.host is not None,
            "guests": len(r.guests), "full": len(r.guests) >= MAX_GUESTS, "stun": _stun(c)}


def _stun(c: Container) -> list[str]:
    raw = getattr(c.settings, "NETPLAY_STUN", "stun:stun.l.google.com:19302") or ""
    return [u.strip() for u in raw.split(",") if u.strip().startswith(("stun:", "stuns:"))]


async def _send(ws: WebSocket | None, msg: dict[str, Any]) -> None:
    if ws is None:
        return
    with contextlib.suppress(Exception):
        await ws.send_text(json.dumps(msg))


@router.websocket("/ws/netplay/{code}")
async def signaling(ws: WebSocket, code: str, role: str = "guest", name: str = "Player 2"):
    """Relay between one host and up to three guests. Only these message types pass: offer, answer, ice, bye."""
    room = ROOMS.get(code.upper())
    await ws.accept()
    if room is None or role not in ("host", "guest"):
        await _send(ws, {"type": "error", "message": "no such room"})
        await ws.close()
        return
    guest_id = None
    if role == "host":
        if room.host is not None:
            await _send(room.host, {"type": "replaced"})
        room.host = ws
        for gid, name_ in room.names.items():  # guests who were waiting
            await _send(ws, {"type": "guest-joined", "guestId": gid, "name": name_})
    else:
        if len(room.guests) >= MAX_GUESTS:
            await _send(ws, {"type": "error", "message": "the room is full"})
            await ws.close()
            return
        guest_id = secrets.token_hex(4)
        room.guests[guest_id] = ws
        room.names[guest_id] = name.strip()[:24] or "Player 2"
        await _send(ws, {"type": "welcome", "guestId": guest_id, "title": room.title, "hostOnline": room.host is not None})
        await _send(room.host, {"type": "guest-joined", "guestId": guest_id, "name": room.names[guest_id]})
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_MESSAGE:
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind not in ("offer", "answer", "ice", "bye"):
                continue
            if role == "host":
                await _send(room.guests.get(str(msg.get("to"))), {"type": kind, "data": msg.get("data")})
            else:
                await _send(room.host, {"type": kind, "from": guest_id, "data": msg.get("data")})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if role == "host" and room.host is ws:
            room.host = None
            for g in list(room.guests.values()):
                await _send(g, {"type": "host-left"})
            ROOMS.pop(room.code, None)
        elif guest_id:
            room.guests.pop(guest_id, None)
            room.names.pop(guest_id, None)
            await _send(room.host, {"type": "guest-left", "guestId": guest_id})

