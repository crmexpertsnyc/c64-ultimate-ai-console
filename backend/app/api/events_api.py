"""📅 Retro events calendar (CSDb parties, 🤖 web-researched fairs and meetups, your own) and 🧩 firmware news."""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.container import Container
from app.services.ask import AskError
from app.services.retro_events import EventError, ics_calendar

from .deps import get_container

router = APIRouter(tags=["events"])


class EventBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    start: date
    end: date | None = None
    city: str | None = Field(None, max_length=120)
    country: str | None = Field(None, max_length=80)
    url: str | None = Field(None, max_length=600)
    type: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=2000)


class EventPatch(BaseModel):
    hidden: bool | None = None
    name: str | None = Field(None, min_length=1, max_length=200)
    start: date | None = None
    end: date | None = None
    city: str | None = Field(None, max_length=120)
    country: str | None = Field(None, max_length=80)
    url: str | None = Field(None, max_length=600)
    type: str | None = Field(None, max_length=120)
    notes: str | None = Field(None, max_length=2000)


def _events(c: Container):  # noqa: ANN202
    svc = getattr(c, "events", None)
    if svc is None:
        raise HTTPException(503, "events are not available")
    return svc


def _firmware(c: Container):  # noqa: ANN202
    fw = getattr(c, "firmware", None)
    if fw is None:
        raise HTTPException(503, "firmware news is not available")
    return fw


@router.get("/api/events", summary="Upcoming retro events (soonest first), what's live now, filters")
async def list_events(country: str | None = Query(None, max_length=80), type: str | None = Query(None, max_length=40),  # noqa: A002
                      q: str | None = Query(None, max_length=60), include_past: bool = False,
                      include_hidden: bool = False, date_from: date | None = Query(None, alias="from"),
                      date_to: date | None = Query(None, alias="to"), region: str | None = Query(None, max_length=10),
                      scope: str | None = Query(None, pattern="^(commodore|retro)$"), home_first: bool = False,
                      c: Container = Depends(get_container)):
    """country="home" = the home country (Settings → EVENTS_HOME_COUNTRY, United States by default)."""
    return _events(c).list(country or None, type or None, q, include_past=include_past, include_hidden=include_hidden,
                           date_from=date_from, date_to=date_to, region=region or None, scope=scope, home_first=home_first)


@router.post("/api/events/refresh", summary="Read CSDb's upcoming events now")
async def refresh(c: Container = Depends(get_container)):
    return await _events(c).refresh(wait_details=False)


@router.post("/api/events/research", summary="🤖 Find fairs, expos and meetups worldwide with web search now")
async def research(c: Container = Depends(get_container)):
    try:
        return await _events(c).research()
    except AskError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/events", summary="Add your own event")
async def add_event(body: EventBody, c: Container = Depends(get_container)):
    try:
        return _events(c).add(body.model_dump())
    except EventError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/events/calendar.ics", summary="📅 All upcoming events as a subscribable calendar")
async def calendar_ics(c: Container = Depends(get_container)):
    return Response(ics_calendar(_events(c).upcoming()), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="c64-retro-events.ics"', "Cache-Control": "no-cache"})


@router.get("/api/events/{event_id:int}.ics", summary="📅 One event as an iCalendar file (add to your calendar)")
async def event_ics(event_id: int, c: Container = Depends(get_container)):
    e = _events(c).get(event_id)
    if e is None:
        raise HTTPException(404, "no such event")
    slug = re.sub(r"[^a-z0-9]+", "-", e.name.lower()).strip("-")[:60] or "event"
    return Response(ics_calendar([e], e.name), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.ics"'})


@router.patch("/api/events/{event_id:int}", summary="Hide / show an event, or edit one you added")
async def edit_event(event_id: int, body: EventPatch, c: Container = Depends(get_container)):
    try:
        out = _events(c).edit(event_id, body.model_dump(exclude_unset=True))
    except EventError as exc:
        raise HTTPException(409, str(exc)) from exc
    if out is None:
        raise HTTPException(404, "no such event")
    return out


@router.delete("/api/events/{event_id:int}", summary="Delete an event you added")
async def delete_event(event_id: int, c: Container = Depends(get_container)):
    try:
        ok = _events(c).delete(event_id)
    except EventError as exc:
        raise HTTPException(409, str(exc)) from exc
    if ok is None:
        raise HTTPException(404, "no such event")
    return {"deleted": event_id}


# ------------------------------------------------------------------ 🧩 firmware news (notification only)
@router.get("/api/firmware", summary="🧩 Is newer firmware out for this device? (notification only — never updates)")
async def firmware(c: Container = Depends(get_container)):
    return _firmware(c).status()


@router.post("/api/firmware/check", summary="🧩 Check the maker's download page now (reads the page only)")
async def firmware_check(c: Container = Depends(get_container)):
    return await _firmware(c).check()
