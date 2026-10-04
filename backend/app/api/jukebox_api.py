"""🎵 SID jukebox: search HVSC, play tunes on the C64's real SID chip, 🤖 AI-built stations, history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.container import Container
from app.services.ask import AskError
from app.services.jukebox import JukeboxError

from .deps import get_container, get_source

router = APIRouter(tags=["jukebox"])


class PlayBody(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, examples=["123456"])
    category: int = Field(..., examples=[18])
    title: str | None = Field(None, max_length=200)
    composer: str | None = Field(None, max_length=120)
    song: int | None = Field(None, ge=1, le=256, description="sub-tune (1-based); default: the tune's start song")
    stationId: int | None = None


class StationBody(BaseModel):
    prompt: str = Field(..., min_length=2, max_length=200, examples=["Rob Hubbard classics"])


def _http(exc: JukeboxError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.get("/api/jukebox/status", summary="Is the C64 ready for SID playback? What's playing?")
async def status(c: Container = Depends(get_container)):
    return c.jukebox.status()


@router.get("/api/jukebox/search", summary="Search HVSC tunes by title and/or composer")
async def search(q: str = Query("", max_length=60), composer: str = Query("", max_length=40),
                 c: Container = Depends(get_container)):
    try:
        return {"tunes": await c.jukebox.search(q, composer)}
    except JukeboxError as exc:
        raise _http(exc) from exc


@router.post("/api/jukebox/play", summary="Play a tune on the C64's SID chip (sidplay runner)")
async def play(body: PlayBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    try:
        return await c.jukebox.play(body.id, body.category, title=body.title, composer=body.composer,
                                    song=body.song, station_id=body.stationId, source=source)
    except JukeboxError as exc:
        raise _http(exc) from exc


@router.post("/api/jukebox/stop", summary="Stop the music (resets the C64)")
async def stop(c: Container = Depends(get_container), source: str = Depends(get_source)):
    try:
        return await c.jukebox.stop_playback(source)
    except JukeboxError as exc:
        raise _http(exc) from exc


@router.get("/api/jukebox/history", summary="The last 30 tunes played")
async def history(c: Container = Depends(get_container)):
    return {"plays": c.jukebox.history()}


@router.get("/api/jukebox/stations", summary="Saved stations, plus suggested prompts")
async def stations(c: Container = Depends(get_container)):
    return c.jukebox.stations()


@router.post("/api/jukebox/stations", summary="🤖 Make a themed station from a prompt")
async def make_station(body: StationBody, c: Container = Depends(get_container)):
    try:
        return await c.jukebox.make_station(body.prompt)
    except JukeboxError as exc:
        raise _http(exc) from exc
    except AskError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/jukebox/stations/{station_id}", summary="A station with its tracks")
async def station(station_id: int, c: Container = Depends(get_container)):
    st = c.jukebox.station(station_id)
    if st is None:
        raise HTTPException(404, "no such station")
    return st


@router.delete("/api/jukebox/stations/{station_id}", summary="Delete a station")
async def delete_station(station_id: int, c: Container = Depends(get_container)):
    if not c.jukebox.delete_station(station_id):
        raise HTTPException(404, "no such station")
    return {"ok": True}
