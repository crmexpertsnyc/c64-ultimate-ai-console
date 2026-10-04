"""Recommendations ("For you") and the taste signals behind them: thumbs up / down, play time, the profile."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.container import Container

from .deps import get_container

router = APIRouter(tags=["recommendations"])


@router.get("/api/recommendations", summary="Games picked for you (cached; 'stale' says a refresh would change them)")
async def recommendations(c: Container = Depends(get_container)):
    return c.recommend.current()


@router.post("/api/recommendations/refresh", summary="Make new recommendations from the current taste profile")
async def refresh_recommendations(c: Container = Depends(get_container)):
    return await c.recommend.refresh()


class RateBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    value: Literal[-1, 0, 1]
    gameId: int | None = None


@router.post("/api/taste/rate", summary="Thumbs up (1), thumbs down (-1) or clear (0) a game")
async def rate(body: RateBody, c: Container = Depends(get_container)):
    try:
        return c.taste.rate(body.title, body.value, body.gameId)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/taste/rating", summary="This game's rating (1, -1 or 0)")
async def rating(title: str = Query(..., min_length=1, max_length=255), c: Container = Depends(get_container)):
    return {"title": title, "value": c.taste.rating(title)}


class EventBody(BaseModel):
    kind: Literal["play_browser", "session"]
    gameId: int
    minutes: float = Field(0, ge=0, le=24 * 60)


@router.post("/api/taste/event", summary="Browser play: a game was started, or played for some minutes")
async def event(body: EventBody, c: Container = Depends(get_container)):
    if body.kind == "session" and body.minutes < 0.5:
        return {"ok": True}  # a glance is not a signal
    c.taste.record(body.kind, game_id=body.gameId, value=body.minutes if body.kind == "session" else 1.0)
    return {"ok": True}


@router.get("/api/taste/profile", summary="What the console has learned about what you like")
async def profile(c: Container = Depends(get_container)):
    p = c.taste.profile()
    p.pop("fingerprint", None)
    return p


@router.delete("/api/taste/game", summary="Forget one game's signals and rating")
async def forget(title: str = Query(..., min_length=1, max_length=255), c: Container = Depends(get_container)):
    c.taste.forget(title)
    return {"ok": True}


@router.delete("/api/taste", summary="Forget all taste history and ratings")
async def clear(c: Container = Depends(get_container)):
    c.taste.clear()
    return {"ok": True}
