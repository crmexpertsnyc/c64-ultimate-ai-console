"""📰 C64 news, 🆕 new releases (play them right away) and 🎬 videos from the monitored feeds."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.container import Container
from app.services.ask import AskError

from .deps import get_container, get_source

router = APIRouter(tags=["news"])


@router.get("/api/news", summary="News, new releases and videos (newest first)")
async def news(kind: Literal["release", "news", "video", "product", "deal"] | None = None,
               category: Literal["game", "demo", "music", "graphics", "tool", "other", "hardware"] | None = None,
               q: str | None = Query(None, max_length=60), limit: int = Query(60, ge=1, le=200),
               offset: int = Query(0, ge=0), sort: Literal["newest", "popular", "trending"] = "newest",
               c: Container = Depends(get_container)):
    return c.news.list(kind, category, q, limit=limit, offset=offset, sort=sort)


@router.get("/api/news/new", summary="How many items arrived since a moment (ISO time) — the 🆕 badge")
async def new_since(since: datetime, c: Container = Depends(get_container)):
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return c.news.count_since(since)


@router.post("/api/news/refresh", summary="Check every feed now")
async def refresh(c: Container = Depends(get_container)):
    result = await c.news.refresh()
    c.news.start_stats()                 # downloads / votes / ratings of new releases, in the background
    return result


@router.get("/api/news/digest", summary="🤖 This week in C64 (the last one made)")
async def digest(c: Container = Depends(get_container)):
    return {"digest": c.news.digest}


@router.post("/api/news/digest", summary="🤖 Make this week's C64 round-up")
async def make_digest(c: Container = Depends(get_container)):
    try:
        return {"digest": await c.news.make_digest()}
    except AskError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/news/{item_id}/add", summary="Add a new release to the library (CSDb download) → gameId")
async def add_release(item_id: int, c: Container = Depends(get_container), source: str = Depends(get_source)):
    from app.services.imports import ImportError_
    item = c.news.get(item_id)
    if item is None:
        raise HTTPException(404, "no such news item")
    if item.game_id:
        from app.models.db import Game
        with c.sf() as s:
            if s.get(Game, item.game_id) is not None:
                return {"gameId": item.game_id}
    if not item.playable or not item.csdb_id:
        raise HTTPException(409, "this release can't be added automatically — open it on its site instead")
    try:
        async with c.audit.action(source, "news.add_release", item.title, {"csdbId": item.csdb_id}) as rec:
            game_id = await c.imports.import_url(f"https://csdb.dk/release/?id={item.csdb_id}", item.title)
            rec.set_response({"gameId": game_id})
    except ImportError_ as exc:
        raise HTTPException(400, str(exc)) from exc
    c.news.link_game(item_id, game_id)
    return {"gameId": game_id}
