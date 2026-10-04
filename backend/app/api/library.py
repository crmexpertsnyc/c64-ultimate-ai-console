"""Library, games and session endpoints."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.container import Container
from app.library.formats import DISK_FORMATS, EMULATOR_FORMATS
from app.library.repository import LibraryRepository, game_to_dict

from .deps import get_container, get_source

router = APIRouter(tags=["library"])


class ScanBody(BaseModel):
    paths: list[str] = Field(default_factory=list, description="Directories to scan; empty = LIBRARY_PATHS")
    save: bool = True


class PlayBody(BaseModel):
    disk: int | None = Field(None, ge=1, le=20)
    method: str | None = None
    wait: bool = False


class MountGameBody(BaseModel):
    disk: int = Field(1, ge=1, le=20)


@router.get("/api/library", summary="Search / browse the library")
async def library(
    q: str = "", favorites: bool = False, recent: bool = False, format: str | None = None,
    publisher: str | None = None, year: int | None = None, genre: str | None = None, category: str | None = None,
    multiplayer: bool | None = None, joystick_port: int | None = Query(None, ge=1, le=2),
    limit: int = Query(60, ge=1, le=500), offset: int = Query(0, ge=0), c: Container = Depends(get_container),
):
    with c.sf() as s:
        games, total = LibraryRepository(s).search(
            q, favorites=favorites, recent=recent, fmt=format, publisher=publisher, year=year, genre=genre,
            category=category, multiplayer=multiplayer, joystick_port=joystick_port, limit=limit, offset=offset)
        return {"total": total, "items": [game_to_dict(g, include_media=True) for g in games]}


@router.get("/api/library/facets")
async def facets(c: Container = Depends(get_container)):
    with c.sf() as s:
        return LibraryRepository(s).facets()


@router.get("/api/library/stats")
async def stats(c: Container = Depends(get_container)):
    with c.sf() as s:
        return LibraryRepository(s).stats()


@router.post("/api/library/scan", summary="Recursively import game folders (never modifies source files)")
async def scan(body: ScanBody, c: Container = Depends(get_container)):
    paths = body.paths or c.settings.library_paths
    if not paths:
        raise HTTPException(400, "no paths given and LIBRARY_PATHS is empty")
    missing = [p for p in paths if not Path(p).expanduser().is_dir()]
    if missing:
        raise HTTPException(400, f"not a directory (or not reachable): {', '.join(missing)}")
    if body.save and body.paths:
        merged = list(dict.fromkeys(c.settings.library_paths + body.paths))
        c.config.update({"LIBRARY_PATHS": ";".join(merged)})
    try:
        return c.start_scan(paths)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/library/scan")
async def scan_status(c: Container = Depends(get_container)):
    return c.scan_state


@router.get("/api/fs/dirs", summary="List sub-directories (setup wizard folder picker; read-only)")
async def list_dirs(path: str = "", c: Container = Depends(get_container)):
    if not path:
        if os.name == "nt":
            import string
            roots = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
        else:
            roots = ["/"]
        return {"path": "", "parent": None, "dirs": roots}
    p = Path(path).expanduser()
    if not p.is_dir():
        raise HTTPException(404, "not a directory")
    try:
        dirs = sorted(str(d) for d in p.iterdir() if d.is_dir() and not d.name.startswith("."))[:500]
    except OSError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "dirs": dirs}


@router.get("/api/games/{game_id}")
async def get_game(game_id: int, c: Container = Depends(get_container)):
    with c.sf() as s:
        g = LibraryRepository(s).get(game_id)
        if not g:
            raise HTTPException(404, "game not found")
        return game_to_dict(g)


@router.patch("/api/games/{game_id}", summary="Edit metadata")
async def edit_game(game_id: int, changes: dict[str, Any], c: Container = Depends(get_container)):
    with c.sf() as s:
        repo = LibraryRepository(s)
        g = repo.get(game_id)
        if not g:
            raise HTTPException(404, "game not found")
        try:
            repo.update(g, changes)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return game_to_dict(repo.get(game_id))  # type: ignore[arg-type]


@router.post("/api/games/{game_id}/favorite")
async def toggle_favorite(game_id: int, c: Container = Depends(get_container)):
    with c.sf() as s:
        repo = LibraryRepository(s)
        g = repo.get(game_id)
        if not g:
            raise HTTPException(404, "game not found")
        repo.update(g, {"favorite": not g.favorite})
        if g.favorite:
            c.taste.record("favorite", game_id=g.id, title=g.title)
        return {"favorite": g.favorite}


@router.post("/api/games/{game_id}/play", summary="Launch a game (returns a launch job)")
async def play(game_id: int, body: PlayBody | None = None, c: Container = Depends(get_container),
               source: str = Depends(get_source)):
    body = body or PlayBody()
    if not c.device.connected:
        raise HTTPException(503, f"C64 Ultimate not connected: {c.device.last_error or 'offline'}")
    job = await c.launcher.launch(game_id, disk=body.disk, method=body.method, source=source, wait=body.wait)
    return job.to_dict()


@router.post("/api/games/{game_id}/mount", summary="Mount a disk of this game without launching")
async def mount_game(game_id: int, body: MountGameBody, c: Container = Depends(get_container),
                     source: str = Depends(get_source)):
    if not c.device.connected:
        raise HTTPException(503, "C64 Ultimate not connected")
    job = await c.launcher.launch(game_id, disk=body.disk, method="mount_only", source=source, wait=True)
    if job.status == "failed":
        raise HTTPException(409, job.error or "mount failed")
    return {"job": job.to_dict(), "session": c.launcher.session.to_dict()}


# ------------------------------------------------------------------- session
@router.get("/api/current-session", summary="Current game, disk and launch job")
async def current_session(c: Container = Depends(get_container)):
    job = c.launcher.current_job() or (list(c.launcher.jobs.values())[-1] if c.launcher.jobs else None)
    return {"session": c.launcher.session.to_dict(), "job": job.to_dict() if job else None}


@router.post("/api/session/disk/{number}", summary="Insert disk N of the current game")
async def session_disk(number: int, c: Container = Depends(get_container), source: str = Depends(get_source)):
    return await c.launcher.mount_disk(number, source)


@router.post("/api/session/next-disk")
async def session_next(c: Container = Depends(get_container), source: str = Depends(get_source)):
    return await c.launcher.next_disk(source)


@router.post("/api/session/previous-disk")
async def session_prev(c: Container = Depends(get_container), source: str = Depends(get_source)):
    return await c.launcher.previous_disk(source)


@router.get("/api/jobs")
async def jobs(c: Container = Depends(get_container)):
    return [j.to_dict() for j in reversed(list(c.launcher.jobs.values()))]


@router.get("/api/jobs/{job_id}")
async def job(job_id: str, c: Container = Depends(get_container)):
    j = c.launcher.jobs.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j.to_dict()


# ------------------------------------------------------------ browser emulator


@router.get("/api/games/{game_id}/emulator", summary="Files for playing a title in the browser emulator")
async def emulator_files(game_id: int, c: Container = Depends(get_container)):
    from app.models.db import Game

    with c.sf() as s:
        g = s.get(Game, game_id)
        if not g:
            raise HTTPException(404, "game not found")
        files = [{"mediaId": m.id, "name": Path(m.path).name, "format": m.format.lower(), "disk": m.disk_number,
                  "url": f"/api/media/{m.id}/file/{Path(m.path).name}"}
                 for m in g.media
                 if m.storage == "local" and not m.missing and m.format.lower() in EMULATOR_FORMATS]
        if not files:
            raise HTTPException(409, "this title has no file the browser emulator can run "
                                     f"(formats: {', '.join(sorted(EMULATOR_FORMATS))})")
        files.sort(key=lambda f: (f["disk"], f["name"].lower()))
        # Several disks: one ZIP with an .m3u playlist, so the emulator can swap disks live.
        # ?v= changes with the files, so the emulator's own download cache never serves a stale bundle.
        stamp = sum(int(m.mtime or 0) + m.size for m in g.media) % 1_000_000
        bundle = (f"/api/games/{g.id}/emulator.zip?v={BUNDLE_VERSION}-{stamp}"
                  if len([f for f in files if f["format"] in DISK_FORMATS]) > 1 else None)
        extra = g.extra or {}
        out = {"id": g.id, "title": g.title, "category": g.category, "files": files, "bundleUrl": bundle,
               "joystickPort": g.joystick_port, "smartStart": extra.get("smartStart"), "guide": extra.get("guide"),
               "genre": g.genre, "styleTags": (extra.get("details") or {}).get("tags") or [],
               "hangs": int((extra.get("hangs") or {}).get("count", 0))}
    out["inputProfile"] = c.input_profiles.get(game_id)  # own session (it may cache the file hash)
    open_issues = c.issues.list("active", game_id)  # 🐞 reported problems still open for this game
    out["issues"] = [{"id": i["id"], "category": i["categoryLabel"], "occurrences": i["occurrences"]} for i in open_issues[:5]]
    return out


BUNDLE_VERSION = 2


@router.api_route("/api/games/{game_id}/emulator.zip", methods=["GET", "HEAD"], include_in_schema=False)
async def emulator_bundle(game_id: int, c: Container = Depends(get_container)):
    """All disks of a multi-disk title plus a playlist (disks.m3u), for live disk swapping in the emulator."""
    import io
    import zipfile

    from fastapi.responses import Response

    from app.models.db import Game

    with c.sf() as s:
        g = s.get(Game, game_id)
        if not g:
            raise HTTPException(404, "game not found")
        disks = sorted(((m.disk_number, Path(m.path)) for m in g.media
                        if m.storage == "local" and not m.missing and m.format.lower() in DISK_FORMATS),
                       key=lambda d: (d[0], d[1].name.lower()))
    if not disks:
        raise HTTPException(409, "no disk images")
    buf = io.BytesIO()
    names: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for n, (_, path) in enumerate(disks, start=1):
            if not path.is_file():
                raise HTTPException(404, f"{path.name} is missing")
            name = f"{n}-{path.name}"  # unique and in disk order
            z.write(path, name)
            names.append(name)
        playlist = "".join(f"{name}\n" for name in names)  # plain names: VICE rejects "file|label" lines
        z.writestr("disks.m3u", playlist)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.api_route("/api/media/{media_id}/file/{name}", methods=["GET", "HEAD"], include_in_schema=False)
async def media_file(media_id: int, name: str, c: Container = Depends(get_container)):
    """Serve one local game file (read-only) to the browser emulator. Only files the library indexed."""
    from fastapi.responses import FileResponse

    from app.models.db import Media

    with c.sf() as s:
        m = s.get(Media, media_id)
        if not m or m.storage != "local" or m.missing or m.format.lower() not in EMULATOR_FORMATS:
            raise HTTPException(404, "not found")
        path = Path(m.path)
    if not path.is_file():
        raise HTTPException(404, "file is missing")
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Cache-Control": "private, max-age=86400"})


# --------------------------------------------------------- browser emulator saves
# One "resume" save per title, kept on the console so any device can continue where another stopped.
MAX_SAVE_BYTES = 16 * 1024 * 1024


def _save_paths(c: Container, game_id: int) -> tuple[Path, Path, Path]:
    from app.profiles import DEFAULT, profile_id
    pid = profile_id()  # 👪 each family member has their own saves (profile 1 keeps the original folder)
    folder = c.settings.data_path / "savestates" / ("" if pid == DEFAULT else f"p{pid}")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{game_id}.state", folder / f"{game_id}.json", folder / f"{game_id}.png"


def _game_exists(c: Container, game_id: int) -> None:
    from app.models.db import Game
    with c.sf() as s:
        if not s.get(Game, game_id):
            raise HTTPException(404, "game not found")


@router.get("/api/saves", summary="Browser Play: this profile's games with a save to continue (newest first)")
async def list_saves(c: Container = Depends(get_container)):
    import json

    from app.models.db import Game
    from app.profiles import DEFAULT, profile_id
    pid = profile_id()
    folder = c.settings.data_path / "savestates" / ("" if pid == DEFAULT else f"p{pid}")
    out = []
    for state in folder.glob("*.state") if folder.is_dir() else []:
        if not state.stem.isdigit():
            continue
        meta = state.with_suffix(".json")
        info = json.loads(meta.read_text()) if meta.is_file() else {}
        with c.sf() as s:
            g = s.get(Game, int(state.stem))
            if not g:
                continue
            out.append({"gameId": g.id, "title": g.title, "coverUrl": g.cover_url, "savedAt": info.get("savedAt"),
                        "device": info.get("device"), "thumb": f"/api/games/{g.id}/save/thumb"
                        if state.with_suffix(".png").is_file() else None})
    out.sort(key=lambda x: -(x["savedAt"] or 0))
    return {"saves": out}


@router.get("/api/games/{game_id}/save/info", summary="Browser Play: is there a save to resume?")
async def save_info(game_id: int, c: Container = Depends(get_container)):
    import json
    state, meta, thumb = _save_paths(c, game_id)
    if not state.is_file():
        return {"exists": False}
    info = json.loads(meta.read_text()) if meta.is_file() else {}
    return {"exists": True, "size": state.stat().st_size, "hasThumb": thumb.is_file(), **info}


@router.put("/api/games/{game_id}/save", summary="Browser Play: store the emulator state (raw bytes)")
async def put_save(game_id: int, request: Request, c: Container = Depends(get_container)):
    import json
    import time
    _game_exists(c, game_id)
    data = await request.body()
    if not data or len(data) > MAX_SAVE_BYTES:
        raise HTTPException(413 if data else 400, "save state missing or too large")
    state, meta, thumb = _save_paths(c, game_id)
    # Keep the previous save as a backup (an automatic save must never destroy a good manual one).
    for p in (state, meta, thumb):
        if p.is_file():
            p.replace(p.with_name(p.stem + ".prev" + p.suffix))
    tmp = state.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(state)  # never leave a half-written save behind
    meta.write_text(json.dumps({
        "savedAt": time.time(),
        "device": (request.headers.get("x-save-device") or "")[:60],
        "core": (request.headers.get("x-save-core") or "")[:60],
        "kind": "auto" if request.headers.get("x-save-kind") == "auto" else "manual",
    }))
    return {"ok": True, "size": len(data)}


@router.put("/api/games/{game_id}/save/thumb", include_in_schema=False)
async def put_save_thumb(game_id: int, request: Request, c: Container = Depends(get_container)):
    data = await request.body()
    if not data.startswith(b"\x89PNG") or len(data) > 2 * 1024 * 1024:
        raise HTTPException(400, "PNG expected")
    _save_paths(c, game_id)[2].write_bytes(data)
    return {"ok": True}


@router.get("/api/games/{game_id}/save", include_in_schema=False)
async def get_save(game_id: int, c: Container = Depends(get_container)):
    from fastapi.responses import FileResponse
    state = _save_paths(c, game_id)[0]
    if not state.is_file():
        raise HTTPException(404, "no save")
    return FileResponse(state, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


@router.get("/api/games/{game_id}/save/thumb", include_in_schema=False)
async def get_save_thumb(game_id: int, c: Container = Depends(get_container)):
    from fastapi.responses import FileResponse
    thumb = _save_paths(c, game_id)[2]
    if not thumb.is_file():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(thumb, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.delete("/api/games/{game_id}/save", summary="Browser Play: forget the save (start fresh)")
async def delete_save(game_id: int, c: Container = Depends(get_container)):
    for p in _save_paths(c, game_id):
        p.unlink(missing_ok=True)
    return {"ok": True}


# -------------------------------------------------------- games from other sources
@router.post("/api/library/import", summary="Add a game file (raw body; ?filename=…&title=…) to the library")
async def import_file(request: Request, filename: str = Query(..., max_length=200),
                      title: str | None = Query(None, max_length=120),
                      c: Container = Depends(get_container), source: str = Depends(get_source)):
    from app.services.imports import ImportError_
    data = await request.body()
    try:
        async with c.audit.action(source, "library.import", filename) as rec:
            game_id = await c.imports.import_bytes(filename, data, title=title, source="upload")
            rec.set_response({"gameId": game_id})
    except ImportError_ as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"gameId": game_id}


class ImportUrlBody(BaseModel):
    url: str = Field(..., max_length=500)
    title: str | None = Field(None, max_length=120)


@router.post("/api/library/import-url", summary="Add a game from a direct CSDb download link")
async def import_url(body: ImportUrlBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    from app.services.imports import ImportError_
    try:
        async with c.audit.action(source, "library.import_url", body.url) as rec:
            game_id = await c.imports.import_url(body.url, body.title)
            rec.set_response({"gameId": game_id})
    except ImportError_ as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"gameId": game_id}


@router.get("/api/sources/find", summary="Find a C64 game on itch.io, CSDb and Lemon64 (web search)")
async def find_sources(title: str = Query(..., min_length=1, max_length=120), c: Container = Depends(get_container)):
    from app.services.imports import find_elsewhere
    if not c.settings.BRAVE_API_KEY:
        raise HTTPException(409, "Searching other sources needs a Brave Search API key (Settings → AI assistant)")
    try:
        return {"title": title, "results": await find_elsewhere(title, c.settings.BRAVE_API_KEY, c.catalog)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"search failed: {exc}") from exc


@router.get("/api/sources/search", summary="Search online C64 archives (Internet Archive, C64.com, Games That Weren't)")
async def search_sources(q: str = Query(..., min_length=1, max_length=80), c: Container = Depends(get_container)):
    from app.services.sources import SourceError, search_all
    c.taste.record("search", text=q)
    try:
        return await search_all(q)
    except SourceError as exc:
        raise HTTPException(400, str(exc)) from exc


class ImportSourceBody(BaseModel):
    source: str = Field(..., pattern="^(archive|c64com|gtw)$")
    id: str = Field(..., min_length=1, max_length=128)
    title: str | None = Field(None, max_length=120)


@router.post("/api/library/import-source", summary="Add a game from an online archive search result")
async def import_source(body: ImportSourceBody, c: Container = Depends(get_container), source: str = Depends(get_source)):
    from app.services.imports import ImportError_
    from app.services.sources import SourceError, fetch
    try:
        async with c.audit.action(source, "library.import_source", f"{body.source}:{body.id}") as rec:
            name, data, page = await fetch(body.source, body.id)
            game_id = await c.imports.import_bytes(name, data, title=body.title, source=body.source, url=page)
            rec.set_response({"gameId": game_id})
    except (SourceError, ImportError_) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - network trouble at the archive
        raise HTTPException(502, f"the archive could not be reached: {type(exc).__name__}") from exc
    return {"gameId": game_id}


# ------------------------------------------------------------- how-to-play guides
@router.get("/api/games/{game_id}/guide", summary="The title's how-to-play guide (if researched)")
async def get_guide(game_id: int, c: Container = Depends(get_container)):
    try:
        guide = c.guides.get(game_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"guide": guide}


@router.post("/api/games/{game_id}/guide", summary="Research a how-to-play guide (web + AI) and store it")
async def make_guide(game_id: int, c: Container = Depends(get_container), source: str = Depends(get_source)):
    from app.services.ask import AskError
    try:
        async with c.audit.action(source, "guide.generate", str(game_id)):
            return {"guide": await c.guides.generate(game_id)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except AskError as exc:
        raise HTTPException(409, str(exc)) from exc


# ------------------------------------------------------------------- smart start
class StartStep(BaseModel):
    key: str = Field(..., max_length=20)          # KeyboardEvent.key, or FIRE for the joystick button
    code: str = Field("", max_length=20)          # KeyboardEvent.code
    keyCode: int = Field(0, ge=0, le=255)
    at: int = Field(0, ge=0, le=600_000)          # ms after the game started (recorded) or 0 (step-by-step)
    shift: bool = False


class SmartStartBody(BaseModel):
    steps: list[StartStep] = Field(..., max_length=60)
    auto: bool = True                              # play automatically when the game starts in Browser Play
    source: str = Field("recorded", pattern="^(recorded|guide)$")


@router.put("/api/games/{game_id}/smart-start", summary="Store a start key sequence (skips intros/trainers)")
async def put_smart_start(game_id: int, body: SmartStartBody, c: Container = Depends(get_container)):
    from app.models.db import Game
    with c.sf() as s:
        g = s.get(Game, game_id)
        if not g:
            raise HTTPException(404, "game not found")
        g.extra = {**(g.extra or {}), "smartStart": body.model_dump()}
        s.commit()
    return {"ok": True}


@router.delete("/api/games/{game_id}/smart-start")
async def delete_smart_start(game_id: int, c: Container = Depends(get_container)):
    from app.models.db import Game
    with c.sf() as s:
        g = s.get(Game, game_id)
        if not g:
            raise HTTPException(404, "game not found")
        g.extra = {k: v for k, v in (g.extra or {}).items() if k != "smartStart"}
        s.commit()
    return {"ok": True}


class LearnBody(BaseModel):
    sha256: str = Field(..., min_length=64, max_length=64)
    startupSequence: list[dict] | None = Field(None, max_length=8)
    joystickPort: int | None = Field(None, ge=1, le=2)


@router.get("/api/games/{game_id}/input-profile", summary="How to start this game and which joystick port it uses")
async def input_profile(game_id: int, c: Container = Depends(get_container)):
    try:
        return c.input_profiles.get(game_id)
    except LookupError:
        raise HTTPException(404, "game not found") from None


@router.put("/api/games/{game_id}/input-profile/learned", summary="Remember a startup key / port that worked")
async def learn_input_profile(game_id: int, body: LearnBody, c: Container = Depends(get_container)):
    try:
        return c.input_profiles.learn(game_id, body.sha256, body.startupSequence, body.joystickPort)
    except LookupError:
        raise HTTPException(404, "game not found") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.delete("/api/games/{game_id}/input-profile/learned")
async def forget_input_profile(game_id: int, c: Container = Depends(get_container)):
    try:
        c.input_profiles.forget(game_id)
    except LookupError:
        raise HTTPException(404, "game not found") from None
    return {"ok": True}
