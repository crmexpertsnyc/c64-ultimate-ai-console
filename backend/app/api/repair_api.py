"""🔧 Repair & diagnostics assistant: symptoms → causes, checks, parts, manuals and videos."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.container import Container

from .deps import get_container

router = APIRouter(tags=["repair"])


@router.get("/api/repair/kb", summary="Machines, symptoms and references the assistant knows")
async def kb(c: Container = Depends(get_container)):
    return c.repair.kb()


class DiagnoseBody(BaseModel):
    machine: str = Field(..., max_length=30)
    text: str = Field("", max_length=600)
    symptom: str | None = Field(None, max_length=40)


@router.post("/api/repair/diagnose", summary="What's wrong? Known causes, checks, parts and videos")
async def diagnose(body: DiagnoseBody, c: Container = Depends(get_container)):
    try:
        return await c.repair.diagnose(body.machine, body.text, body.symptom)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
