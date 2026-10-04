"""🛒 Hardware shop (link-out catalog), setup-aware suggestions, 🤖 starter kit, eBay listings and 💰 price watches."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.container import Container
from app.services.ask import AskError
from app.services.shop import EbayError

from .deps import get_container

router = APIRouter(tags=["shop"])


@router.get("/api/shop/catalog", summary="The hardware catalog (built-in or from your website)")
async def catalog(category: str | None = None, tag: str | None = None, q: str | None = Query(None, max_length=60),
                  c: Container = Depends(get_container)):
    await c.shop.refresh_remote()
    cat = c.shop.catalog()
    return {**cat, "items": c.shop.items(category, [tag] if tag else None, q)}


@router.post("/api/shop/catalog/refresh", summary="Fetch the website's catalog now")
async def refresh_catalog(c: Container = Depends(get_container)):
    await c.shop.refresh_remote(force=True)
    cat = c.shop.catalog()
    return {"source": cat["source"], "error": cat["error"], "items": len(cat["items"])}


@router.get("/api/shop/suggest", summary="Hardware suggestions that know your setup")
async def suggest(context: Literal["game", "controller", "repair", "setup"], game_id: int | None = None,
                  using: str | None = Query(None, max_length=20), tags: str | None = Query(None, max_length=200),
                  c: Container = Depends(get_container)):
    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()][:12]
    return c.shop.suggest(context, game_id=game_id, using=using, tags=tag_list)


class AdvisorBody(BaseModel):
    prompt: str = Field("I just got a C64 Ultimate — what else do I need?", min_length=3, max_length=400)
    budget: float | None = Field(None, gt=0, lt=100000)


@router.post("/api/shop/advisor", summary="🤖 Build a starter kit from the catalog")
async def advisor(body: AdvisorBody, c: Container = Depends(get_container)):
    try:
        return await c.shop.advisor(body.prompt, body.budget)
    except AskError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/shop/ebay", summary="Used & rare gear: live eBay listings (or a search link)")
async def ebay(q: str = Query(..., min_length=2, max_length=100), max_price: float | None = Query(None, gt=0),
               c: Container = Depends(get_container)):
    try:
        return await c.shop.listings(q, max_price)
    except EbayError as exc:
        raise HTTPException(502, str(exc)) from exc


class WatchBody(BaseModel):
    query: str = Field(..., min_length=2, max_length=120)
    max_price: float | None = Field(None, gt=0, lt=1000000)


@router.get("/api/shop/watches", summary="💰 Price watches and recent deals")
async def watches(c: Container = Depends(get_container)):
    return {"watches": c.shop.watches(), "deals": c.shop.deals(), "ebayConfigured": c.shop.ebay.configured}


@router.post("/api/shop/watches", summary="Watch an eBay search for a price")
async def add_watch(body: WatchBody, c: Container = Depends(get_container)):
    return c.shop.add_watch(body.query, body.max_price)


@router.delete("/api/shop/watches/{watch_id}")
async def remove_watch(watch_id: int, c: Container = Depends(get_container)):
    if not c.shop.remove_watch(watch_id):
        raise HTTPException(404, "no such watch")
    return {"ok": True}


@router.post("/api/shop/watches/check", summary="Check the price watches now")
async def check_watches(c: Container = Depends(get_container)):
    try:
        return {"found": await c.shop.check_watches()}
    except EbayError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/shop/images/{item_id}", summary="A product photo (downloaded once from the seller's page)")
async def image(item_id: str, c: Container = Depends(get_container)):
    f = c.shop.image_file(item_id)
    if f is None:
        raise HTTPException(404, "no image yet")
    return FileResponse(f, headers={"Cache-Control": "public, max-age=604800"})


@router.post("/api/shop/images/refresh", summary="Find and download missing product photos now")
async def refresh_images(force: bool = False, c: Container = Depends(get_container)):
    return await c.shop.fetch_images(force=force)
