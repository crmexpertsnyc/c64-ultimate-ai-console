"""📚 Classic C64 magazines from the Internet Archive: issues, on-demand search index, 🤖 ask, 🤖 game reviews."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.container import Container
from app.services.ask import AskError

from .deps import get_container

router = APIRouter(tags=["magazines"])


class AskBody(BaseModel):
    question: str = Field(min_length=2, max_length=500)


def _unknown(series: str) -> HTTPException:
    return HTTPException(404, f"unknown magazine: {series[:40]}")


@router.get("/api/magazines", summary="Magazine series: issue counts, how many are searchable, index progress")
async def series_list(c: Container = Depends(get_container)):
    return await c.magazines.overview()


@router.get("/api/magazines/cover", summary="An issue's cover (front page), cached from the Internet Archive")
async def cover(key: str = Query(..., min_length=1, max_length=200), c: Container = Depends(get_container)):
    f = await c.magazines.cover(key)
    if f is None:
        raise HTTPException(404, "no cover for this issue")
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=2592000"})


@router.get("/api/magazines/search", summary="Full-text search of the indexed magazines (highlighted snippets)")
async def search(q: str = Query(..., min_length=1, max_length=200), series: str | None = Query(None, max_length=40),
                 limit: int = Query(20, ge=1, le=50), c: Container = Depends(get_container)):
    try:
        return c.magazines.search(q, series, limit)
    except KeyError:
        raise _unknown(series or "") from None


@router.post("/api/magazines/ask", summary="🤖 Ask the magazines — answered only from indexed excerpts, cited")
async def ask(body: AskBody, c: Container = Depends(get_container)):
    try:
        return await c.magazines.ask_question(body.question)
    except AskError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/magazines/{series}/issues", summary="A series' issues (from the Internet Archive, cached a day)")
async def issues(series: str, refresh: bool = False, c: Container = Depends(get_container)):
    try:
        return await c.magazines.issues(series, refresh)
    except KeyError:
        raise _unknown(series) from None


@router.post("/api/magazines/{series}/index", summary="Make a series searchable (downloads its OCR text, politely)")
async def start_index(series: str, c: Container = Depends(get_container)):
    try:
        return {"job": c.magazines.start_index(series)}
    except KeyError:
        raise _unknown(series) from None


@router.delete("/api/magazines/{series}/index", summary="Stop making a series searchable (what's done stays)")
async def cancel_index(series: str, c: Container = Depends(get_container)):
    try:
        return {"job": await c.magazines.cancel_index(series)}
    except KeyError:
        raise _unknown(series) from None


@router.get("/api/games/{game_id}/magazine-reviews", summary="📚 Magazine reviews found for a game (cached)")
async def game_reviews(game_id: int, c: Container = Depends(get_container)):
    return c.magazines.get_reviews(game_id)


@router.post("/api/games/{game_id}/magazine-reviews", summary="🤖 Find a game's reviews in the indexed magazines")
async def find_game_reviews(game_id: int, c: Container = Depends(get_container)):
    try:
        return await c.magazines.make_reviews(game_id)
    except KeyError:
        raise HTTPException(404, "no such game") from None
    except AskError as exc:
        raise HTTPException(409, str(exc)) from exc
