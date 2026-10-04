"""🔄 Sources & updates: every dynamic source, when it was last checked, how often, and "run now"."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.container import Container
from app.services.scheduler import CHOICES

from .deps import get_container

router = APIRouter(tags=["updates"])


@router.get("/api/updates", summary="Every source the console keeps up to date, with its timetable and last result")
async def updates(c: Container = Depends(get_container)):
    return {"jobs": c.scheduler.status(), "choices": CHOICES, "enabled": bool(c.settings.NEWS_MONITOR),
            "running": c.scheduler.running}


class JobBody(BaseModel):
    every: int | None = None
    enabled: bool | None = None


@router.get("/api/backups", summary="💾 Backups: folder, how many are kept, the backups there")
async def backups(c: Container = Depends(get_container)):
    return c.backup.status()


@router.post("/api/backups", summary="💾 Back up now (queued on the scheduler)")
async def backup_now(c: Container = Depends(get_container)):
    c.scheduler.request("backup")
    return {"queued": True}


@router.patch("/api/updates/{key}", summary="Change how often a source is checked, or switch it off")
async def configure(key: str, body: JobBody, c: Container = Depends(get_container)):
    try:
        return c.scheduler.configure(key, body.every, body.enabled)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/updates/{key}/run", summary="Check this source now (queued; one job runs at a time)")
async def run_now(key: str, c: Container = Depends(get_container)):
    try:
        c.scheduler.request(key)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"queued": True, "running": c.scheduler.running}
