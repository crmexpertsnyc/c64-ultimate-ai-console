"""👪 Family profiles: list, create, edit, delete. The chosen profile is sent by the browser with each request."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.container import Container
from app.profiles import EMOJIS, profile_id

from .deps import get_container

router = APIRouter(tags=["profiles"])


class ProfileBody(BaseModel):
    name: str | None = Field(None, max_length=40)
    emoji: str | None = Field(None, max_length=8)
    color: str | None = Field(None, max_length=9)
    kids: bool | None = None


@router.get("/api/profiles", summary="Family profiles, and which one this request is for")
async def profiles(c: Container = Depends(get_container)):
    return {"profiles": c.profiles.list(), "current": profile_id(), "emojis": EMOJIS}


@router.post("/api/profiles", summary="Add a family member")
async def create(body: ProfileBody, c: Container = Depends(get_container)):
    try:
        return c.profiles.create(body.name or "", body.emoji, body.color, bool(body.kids))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.patch("/api/profiles/{pid}")
async def update(pid: int, body: ProfileBody, c: Container = Depends(get_container)):
    try:
        return c.profiles.update(pid, **body.model_dump())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/profiles/{pid}", summary="Remove a family member and their taste history")
async def delete(pid: int, c: Container = Depends(get_container)):
    try:
        c.profiles.delete(pid)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}
