"""AI features: ✨ Fill in details, 💡 hints, 🧭 text adventure co-pilot, 🛟 hang rescue, 🎉 playlists, 📊 your week."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.container import Container
from app.services.ask import AskError

from .deps import get_container

router = APIRouter(tags=["ai"])


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, AskError):
        return HTTPException(409, str(exc))
    return HTTPException(400, str(exc))


# ---------------------------------------------------------------- ✨ fill in details
@router.post("/api/games/{game_id}/details", summary="Fill in genre, players, year, publisher, tags (web + AI)")
async def fill_details(game_id: int, c: Container = Depends(get_container)):
    try:
        return await c.enrich.enrich(game_id)
    except (LookupError, AskError) as exc:
        raise _fail(exc) from exc


class EnrichAllBody(BaseModel):
    onlyMissing: bool = True


@router.post("/api/library/details", summary="Fill in details for the whole library (runs in the background)")
async def fill_all(body: EnrichAllBody | None = None, c: Container = Depends(get_container)):
    if not c.ask.provider.configured:
        raise HTTPException(409, "This needs an AI model — set one up under Settings → AI assistant")
    return c.enrich.start_all(only_missing=body.onlyMissing if body else True)


@router.get("/api/library/details", summary="Progress of the library-wide Fill in details")
async def fill_all_status(c: Container = Depends(get_container)):
    return c.enrich.job


# ---------------------------------------------------------------- 💡 hints · 🧭 co-pilot
class HintBody(BaseModel):
    screen: str = Field("", max_length=4000)
    question: str = Field("", max_length=200)
    level: int = Field(1, ge=1, le=3)
    previous: list[str] = Field(default_factory=list, max_length=5)


@router.post("/api/games/{game_id}/hint", summary="A spoiler-free hint for what is on screen (levels 1-3)")
async def hint(game_id: int, body: HintBody, c: Container = Depends(get_container)):
    try:
        return await c.coach.hint(game_id, screen=body.screen, question=body.question, level=body.level,
                                  previous=body.previous)
    except (LookupError, AskError) as exc:
        raise _fail(exc) from exc


class CopilotBody(BaseModel):
    transcript: str = Field(..., max_length=8000)
    notes: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/games/{game_id}/copilot", summary="Text adventure co-pilot: room, inventory, map, next commands")
async def copilot(game_id: int, body: CopilotBody, c: Container = Depends(get_container)):
    try:
        return await c.coach.copilot(game_id, transcript=body.transcript, notes=body.notes)
    except (LookupError, AskError, ValueError) as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------- 🛟 hang rescue
@router.get("/api/games/{game_id}/alternatives", summary="Other versions of this game likely to start in the browser")
async def alternatives(game_id: int, c: Container = Depends(get_container)):
    try:
        return await c.rescue.alternatives(game_id)
    except LookupError as exc:
        raise _fail(exc) from exc


@router.post("/api/games/{game_id}/hang", summary="Browser Play: this version got stuck while loading")
async def hang(game_id: int, c: Container = Depends(get_container)):
    try:
        return {"hangs": c.rescue.report_hang(game_id)}
    except LookupError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------- 🎉 playlists
class PlaylistBody(BaseModel):
    prompt: str = Field(..., min_length=3, max_length=300)


@router.get("/api/playlists", summary="Game nights and playlists")
async def playlists(c: Container = Depends(get_container)):
    from app.services.playlists import SUGGESTIONS
    return {"playlists": c.playlists.list(), "suggestions": SUGGESTIONS}


@router.post("/api/playlists", summary="Make a playlist / game night from a request (AI)")
async def make_playlist(body: PlaylistBody, c: Container = Depends(get_container)):
    try:
        return await c.playlists.create(body.prompt)
    except (AskError, ValueError) as exc:
        raise _fail(exc) from exc


@router.get("/api/playlists/{pid}")
async def get_playlist(pid: int, c: Container = Depends(get_container)):
    try:
        return c.playlists.get(pid)
    except LookupError as exc:
        raise _fail(exc) from exc


class ItemBody(BaseModel):
    done: bool | None = None
    remove: bool = False


@router.patch("/api/playlists/{pid}/items/{index}", summary="Tick off or remove a game")
async def update_item(pid: int, index: int, body: ItemBody, c: Container = Depends(get_container)):
    try:
        return c.playlists.update_item(pid, index, done=body.done, remove=body.remove)
    except LookupError as exc:
        raise _fail(exc) from exc


@router.delete("/api/playlists/{pid}")
async def delete_playlist(pid: int, c: Container = Depends(get_container)):
    c.playlists.delete(pid)
    return {"ok": True}


# ---------------------------------------------------------------- 📊 your week
@router.get("/api/recap", summary="Your week: play time, top games, AI summary and challenges")
async def recap(c: Container = Depends(get_container)):
    return await c.recap.get()


@router.post("/api/recap/refresh")
async def recap_refresh(c: Container = Depends(get_container)):
    return await c.recap.get(refresh=True)


class DoneBody(BaseModel):
    done: bool


@router.post("/api/recap/challenges/{index}", summary="Tick off a weekly challenge")
async def challenge(index: int, body: DoneBody, c: Container = Depends(get_container)):
    try:
        return c.recap.set_done(index, body.done)
    except LookupError as exc:
        raise _fail(exc) from exc


# ---------------------------------------------------------------- 🐞 compatibility log
class IssueBody(BaseModel):
    gameId: int | None = None
    title: str | None = Field(None, max_length=255)
    category: str = Field(..., max_length=20)
    source: str = Field("user", pattern="^(auto|user)$")
    note: str | None = Field(None, max_length=1000)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    screenshot: str | None = Field(None, max_length=900_000)  # data:image/png;base64,…


@router.post("/api/issues", summary="Report a game that did not work in Browser Play (auto or by the player)")
async def report_issue(body: IssueBody, c: Container = Depends(get_container)):
    try:
        return c.issues.report(body.gameId, category=body.category, source=body.source, note=body.note,
                               diagnostics=body.diagnostics, screenshot_b64=body.screenshot, title=body.title)
    except (LookupError, ValueError) as exc:
        raise _fail(exc) from exc


@router.get("/api/issues", summary="The compatibility log (status: active | open | investigating | fixed | wontfix)")
async def list_issues(status: str | None = None, gameId: int | None = None, c: Container = Depends(get_container)):  # noqa: N803
    from app.services.issues import CATEGORIES
    return {"issues": c.issues.list(status, gameId), "categories": CATEGORIES}


@router.get("/api/issues/export.csv", summary="The compatibility log as a spreadsheet")
async def export_issues(c: Container = Depends(get_container)):
    return Response(c.issues.export_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="c64-compatibility-log.csv"'})


class IssueUpdate(BaseModel):
    status: str | None = Field(None, pattern="^(open|investigating|fixed|wontfix)$")
    resolution: str | None = Field(None, max_length=1000)
    note: str | None = Field(None, max_length=1000)


@router.patch("/api/issues/{issue_id}", summary="Set status / resolution / note")
async def update_issue(issue_id: int, body: IssueUpdate, c: Container = Depends(get_container)):
    try:
        return c.issues.update(issue_id, body.status, body.resolution, body.note)
    except (LookupError, ValueError) as exc:
        raise _fail(exc) from exc


@router.delete("/api/issues/{issue_id}")
async def delete_issue(issue_id: int, c: Container = Depends(get_container)):
    c.issues.delete(issue_id)
    return {"ok": True}


@router.get("/api/issues/{issue_id}/screenshot", include_in_schema=False)
async def issue_screenshot(issue_id: int, c: Container = Depends(get_container)):
    p = c.issues.screenshot(issue_id)
    if not p:
        raise HTTPException(404, "no screenshot")
    return FileResponse(p, media_type="image/png")


@router.post("/api/issues/{issue_id}/investigate", summary="🔍 AI analysis: likely cause and next steps")
async def investigate_issue(issue_id: int, c: Container = Depends(get_container)):
    try:
        return await c.issues.investigate(issue_id)
    except (LookupError, AskError) as exc:
        raise _fail(exc) from exc


@router.post("/api/games/{game_id}/worked", summary="Browser Play reached gameplay: note it on open reports")
async def game_worked(game_id: int, c: Container = Depends(get_container)):
    return {"updated": c.issues.mark_worked(game_id)}





# ---------------------------------------------------------------- 🏆 achievements & verified scores
class ProgressBody(BaseModel):
    sample: dict[str, Any] = Field(default_factory=dict)   # Browser Play's screen sample (rows, rawRows, mode)
    playing: bool = True                                    # the analyzer thinks the game is being played


@router.post("/api/games/{game_id}/progress", summary="Browser Play: what the game shows now → scores, achievements")
async def game_progress(game_id: int, body: ProgressBody, c: Container = Depends(get_container)):
    sample = {k: body.sample.get(k) for k in ("rows", "rawRows", "mode", "gameplay")}
    try:
        return c.achievements.progress(game_id, sample, source="browser", playing=body.playing)
    except LookupError as exc:
        raise _fail(exc) from exc


@router.post("/api/device/progress", summary="Real C64: read the screen and update scores / achievements")
async def device_progress(c: Container = Depends(get_container)):
    """The console reads the real C64's screen itself (read-only), so these scores can't be typed in."""
    from app.api.device import screen_sample
    game_id = c.launcher.session.game_id
    sample = await screen_sample(c)
    if not game_id:
        return {"gameId": None, "metrics": {}, "unlocked": [], "newBest": None, "sample": sample}
    out = c.achievements.progress(game_id, sample, source="c64", playing=True)
    return {"gameId": game_id, **out, "sample": sample}


@router.get("/api/games/{game_id}/achievements", summary="This game's achievements, who unlocked them, progress")
async def game_achievements(game_id: int, c: Container = Depends(get_container)):
    try:
        return {**c.achievements.for_game(game_id), "scores": c.achievements.scores(game_id)}
    except LookupError as exc:
        raise _fail(exc) from exc


class GenerateBody(BaseModel):
    metrics: list[str] | None = None


@router.post("/api/games/{game_id}/achievements/generate", summary="🤖 Draft achievements from what this game shows")
async def make_achievements(game_id: int, body: GenerateBody | None = None, c: Container = Depends(get_container)):
    try:
        return await c.achievements.generate(game_id, body.metrics if body else None)
    except (LookupError, AskError) as exc:
        raise _fail(exc) from exc


@router.get("/api/achievements/recent", summary="Recently unlocked achievements (the whole family)")
async def recent_achievements(c: Container = Depends(get_container)):
    return {"recent": c.achievements.recent()}
