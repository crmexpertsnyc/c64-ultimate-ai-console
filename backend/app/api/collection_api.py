"""📦 Collection manager: physical items, value guide, ⭐ wishlist with price watches, CSV / insurance report."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from app.container import Container
from app.services.shop import EbayError

from .deps import get_container

router = APIRouter(tags=["collection"])


class ItemBody(BaseModel):
    kind: str | None = Field(None, max_length=20)
    title: str | None = Field(None, max_length=200)
    platform: str | None = Field(None, max_length=30)
    edition: str | None = Field(None, max_length=120)
    condition: str | None = Field(None, max_length=20)
    boxed: bool | None = None
    complete: bool | None = None
    quantity: int | None = Field(None, ge=1, le=9999)
    serial: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=2000)
    location: str | None = Field(None, max_length=80)
    purchase_price: float | None = Field(None, ge=0, lt=10_000_000)
    purchase_date: str | None = Field(None, pattern=r"^\d{4}(-\d{2}(-\d{2})?)?$")
    value: float | None = Field(None, ge=0, lt=10_000_000)
    currency: str | None = Field(None, pattern=r"^[A-Za-z]{3}$")
    game_id: int | None = None
    wishlist: bool | None = None
    target_price: float | None = Field(None, gt=0, lt=10_000_000)

    def data(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


def _errors(fn):  # noqa: ANN001, ANN202
    try:
        return fn()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/collection", summary="Your collection (and wishlist), with totals")
async def items(kind: str | None = None, q: str | None = Query(None, max_length=60), wishlist: bool | None = None,
                c: Container = Depends(get_container)):
    return c.collection.list(kind, q, wishlist)


@router.post("/api/collection", summary="Add an item")
async def create(body: ItemBody, c: Container = Depends(get_container)):
    return _errors(lambda: c.collection.create(body.data()))


@router.post("/api/collection/from-library/{game_id}", summary="Add a game you own from the library (box art + title)")
async def from_library(game_id: int, body: ItemBody, c: Container = Depends(get_container)):
    return _errors(lambda: c.collection.from_library(game_id, body.data()))


@router.patch("/api/collection/{item_id}")
async def update(item_id: int, body: ItemBody, c: Container = Depends(get_container)):
    return _errors(lambda: c.collection.update(item_id, body.data()))


@router.delete("/api/collection/{item_id}")
async def delete(item_id: int, c: Container = Depends(get_container)):
    _errors(lambda: c.collection.delete(item_id))
    return {"ok": True}


@router.post("/api/collection/{item_id}/estimate", summary="💲 Value guide from current eBay asking prices")
async def estimate(item_id: int, c: Container = Depends(get_container)):
    _errors(lambda: c.collection.get(item_id))
    try:
        return await c.collection.estimate(item_id)
    except EbayError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/collection/export.csv", summary="Spreadsheet export")
async def export_csv(c: Container = Depends(get_container)):
    name = f"c64-collection-{datetime.now(UTC):%Y-%m-%d}.csv"
    return Response("﻿" + c.collection.export_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/collection/report", response_class=HTMLResponse, summary="Printable inventory for insurance")
async def report(c: Container = Depends(get_container)):
    return HTMLResponse(c.collection.report_html())
