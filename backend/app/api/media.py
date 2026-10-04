"""Screenshots, box art, recordings and live streaming endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.container import Container
from app.library.repository import LibraryRepository
from app.models.db import Game

from .deps import get_container, get_source

router = APIRouter(tags=["media"])


class ScreenshotBody(BaseModel):
    game_id: int | None = None
    title: str | None = Field(None, max_length=120)


class CoverBody(BaseModel):
    screenshot: str = Field(..., max_length=120)


# ---------------------------------------------------------------- screenshots
@router.post("/api/screenshots", summary="Save a pixel-perfect PNG of the C64 screen")
async def take_screenshot(body: ScreenshotBody | None = None, c: Container = Depends(get_container),
                          source: str = Depends(get_source)):
    body = body or ScreenshotBody()
    if not c.device.connected:
        raise HTTPException(503, "C64 Ultimate not connected")
    title, game_id = body.title, body.game_id
    if not title and c.launcher.session.title:
        title, game_id = c.launcher.session.title, game_id or c.launcher.session.game_id
    try:
        async with c.audit.action(source, "screenshot.capture", title) as rec:
            shot = await c.screenshots.capture(title or "", game_id)
            rec.set_response({"name": shot["name"]})
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    c.hub.publish("screenshot", shot)
    return shot


@router.get("/api/screen.png", summary="The current C64 screen as PNG (not saved)")
async def screen_png(c: Container = Depends(get_container)):
    from fastapi.responses import Response
    if not c.device.connected:
        raise HTTPException(503, "C64 Ultimate not connected")
    try:
        png = await c.screenshots.grab_png()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/api/screenshots", summary="Screenshot gallery (newest first)")
async def list_screenshots(include_auto: bool = True, c: Container = Depends(get_container)):
    return c.screenshots.list(include_auto)


@router.get("/api/screenshots/{name}", include_in_schema=False)
async def get_screenshot(name: str, download: bool = False, c: Container = Depends(get_container)):
    try:
        path = c.screenshots.path(name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "not found") from exc
    return FileResponse(path, media_type="image/png", filename=name if download else None,
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.delete("/api/screenshots/{name}")
async def delete_screenshot(name: str, c: Container = Depends(get_container)):
    try:
        c.screenshots.delete(name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "not found") from exc
    url = f"/api/screenshots/{name}"
    with c.sf() as s:  # a deleted screenshot must not stay behind as a broken cover
        for g in s.query(Game).filter(Game.cover_url == url):
            g.cover_url = None
        s.commit()
    return {"ok": True}


@router.post("/api/games/{game_id}/cover", summary="Use a screenshot as a title's cover art")
async def set_cover(game_id: int, body: CoverBody, c: Container = Depends(get_container)):
    try:
        c.screenshots.path(body.screenshot)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "screenshot not found") from exc
    with c.sf() as s:
        repo = LibraryRepository(s)
        g = repo.get(game_id)
        if not g:
            raise HTTPException(404, "game not found")
        repo.update(g, {"coverUrl": f"/api/screenshots/{body.screenshot}"})
        return {"coverUrl": g.cover_url}


class CoverRefreshBody(BaseModel):
    game_ids: list[int] | None = None
    force: bool = False


@router.post("/api/library/covers", summary="Find cover art (box art, title screen, CSDB) for the library")
async def refresh_covers(body: CoverRefreshBody | None = None, c: Container = Depends(get_container)):
    body = body or CoverRefreshBody()
    try:
        return c.start_cover_refresh(body.game_ids, force=body.force)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/library/covers", summary="Progress of the cover art search")
async def cover_refresh_status(c: Container = Depends(get_container)):
    return c.cover_state


@router.post("/api/games/{game_id}/cover/find", summary="Look up box art for one title now")
async def find_cover(game_id: int, force: bool = True, c: Container = Depends(get_container)):
    return await c.ensure_cover(game_id, force=force)


@router.get("/api/art/file/{name}", include_in_schema=False)
async def art_file(name: str, c: Container = Depends(get_container)):
    import re
    if not re.fullmatch(r"libretro-(boxart|title|snap)-[a-z0-9-]{1,90}\.png", name):
        raise HTTPException(404, "not found")
    path = c.boxart.folder / name
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=2592000"})


@router.get("/api/art/title", include_in_schema=False)
async def title_art(name: str = Query(..., min_length=1, max_length=120), csdb: str | None = Query(None, pattern=r"^\d{1,9}$"),
                    c: Container = Depends(get_container)):
    """Box art for any game title (recommendations, playlists): libretro box art → title screen → the CSDb
    release's screenshot. Downloaded once, then served from the art folder."""
    try:
        found = await c.boxart.art_for(name)
    except Exception:  # noqa: BLE001 - art is optional
        found = None
    if found:
        return FileResponse(found[1], media_type="image/png", headers={"Cache-Control": "public, max-age=2592000"})
    if csdb:
        path = await c.screenshots.csdb_art(csdb)
        if path is not None:
            media = {"png": "image/png", "gif": "image/gif", "jpg": "image/jpeg"}[path.suffix.lstrip(".")]
            return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=604800"})
    raise HTTPException(404, "no art found")


@router.get("/api/art/csdb/{release_id}", include_in_schema=False)
async def csdb_art(release_id: str, c: Container = Depends(get_container)):
    path = await c.screenshots.csdb_art(release_id)
    if path is None:
        raise HTTPException(404, "no CSDB screenshot")
    media = {"png": "image/png", "gif": "image/gif", "jpg": "image/jpeg"}[path.suffix.lstrip(".")]
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=604800"})


# ------------------------------------------------------------ live / recording
@router.get("/api/live", summary="Live streaming / recording status")
async def live_status(c: Container = Depends(get_container)):
    return c.live.status()


@router.post("/api/live/{mode}/start", summary="Go live (RTMP) or start recording to MP4")
async def live_start(mode: Literal["live", "record"], c: Container = Depends(get_container),
                     source: str = Depends(get_source)):
    if not c.device.connected:
        raise HTTPException(503, "C64 Ultimate not connected")
    if source == "mcp":
        raise HTTPException(403, "streaming cannot be started by MCP clients")
    try:
        async with c.audit.action(source, f"live.{mode}.start"):
            return await c.live.start(mode)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/api/live/stop")
async def live_stop(c: Container = Depends(get_container), source: str = Depends(get_source)):
    async with c.audit.action(source, "live.stop"):
        return await c.live.stop()


@router.get("/api/recordings")
async def recordings(c: Container = Depends(get_container)):
    return c.live.recordings()


@router.get("/api/recordings/{name}", include_in_schema=False)
async def get_recording(name: str, download: bool = False, c: Container = Depends(get_container)):
    try:
        path = c.live.recording_path(name)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "not found") from exc
    return FileResponse(path, media_type="video/mp4", filename=name if download else None)


@router.delete("/api/recordings/{name}")
async def delete_recording(name: str, c: Container = Depends(get_container)):
    try:
        c.live.recording_path(name).unlink()
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "not found") from exc
    return {"ok": True}
