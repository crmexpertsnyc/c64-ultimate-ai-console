"""✨ Fill in details: genre, players, year, publisher, joystick port, a one-line description and style tags
("co-op", "great music", "relaxing" …) for library games — researched on the web (Brave) and summarised by the
AI. Only empty fields are filled; anything already known or edited by the user is never overwritten.

The tags make the library searchable by feel ("2-player relaxing") and give the recommendation engine
genres to work with.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from sqlalchemy import select

from app.models.db import Game

from .ai_json import ask_json, cited, strs
from .ask import AskError

log = logging.getLogger("c64.enrich")
PAUSE = 1.1  # seconds between games — Brave's free plan allows one search per second

GENRES = ["Platform", "Shoot 'em up", "Action", "Adventure", "Text adventure", "Puzzle", "Sport", "Racing",
          "Fighting", "Strategy", "Simulation", "RPG", "Arcade", "Maze", "Educational", "Music", "Compilation"]
TAGS = ["co-op", "versus", "single-player", "party", "relaxing", "fast-paced", "hard", "family-friendly",
        "great music", "great graphics", "story", "short sessions", "long adventure", "classic", "hidden gem",
        "puzzle-heavy", "exploration", "keyboard needed", "joystick waggling", "homebrew"]

SYSTEM = """You fill in catalogue details for a Commodore 64 game in a player's library.
{sources_rule}
Respond with ONE JSON object only:
{{"year": <release year or null>, "publisher": "<original publisher or null>",
  "genre": "<one of: {genres}>", "players": "<e.g. 1, 1-2, 2, 1-4>",
  "joystickPort": <1 or 2 or null>, "description": "<one sentence, max 25 words, what the game is>",
  "tags": [<2-6 of: {tags}>], "sources": [<numbers of the results you used>]}}
Only state what you are confident about for the C64 version; use null when unsure."""
WITH_SOURCES = "Use the numbered web search results as your source; do not invent details they don't support."
NO_SOURCES = "No web results are available: use your own knowledge and use null when unsure."


class EnrichService:
    def __init__(self, ask_service, session_factory):  # noqa: ANN001
        self.ask = ask_service
        self.sf = session_factory
        self.job: dict[str, Any] = {"running": False, "done": 0, "total": 0, "filled": 0, "errors": 0, "current": None}
        self._task: asyncio.Task | None = None

    async def enrich(self, game_id: int) -> dict[str, Any]:
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            title, year, publisher = g.title, g.year, g.publisher
        clean = re.sub(r"\s*\((?:EasyFlash|Cartridge|Preview|NTSC|PAL|Disk \d+)\)\s*$", "", title)
        about = clean + (f" ({year}" + (f", {publisher}" if publisher else "") + ")" if year else "")
        system_rule = WITH_SOURCES if self.ask.web_search_on else NO_SOURCES
        data, results = await ask_json(
            self.ask, SYSTEM.format(sources_rule=system_rule, genres=", ".join(GENRES), tags=", ".join(TAGS)),
            f"Game: {about} — Commodore 64.", search=f'"{clean}" Commodore 64 game genre publisher year players',
            max_tokens=1500, what="game details")
        return self._apply(game_id, data, results)

    def _apply(self, game_id: int, data: dict[str, Any], results: list[dict[str, str]]) -> dict[str, Any]:
        year = data.get("year")
        year = int(year) if isinstance(year, (int, str)) and str(year).isdigit() and 1981 <= int(year) <= 2100 else None
        genre = next((x for x in GENRES if x.lower() == str(data.get("genre") or "").strip().lower()), None)
        players = str(data.get("players") or "").strip()[:10]
        players = players if re.fullmatch(r"\d(?:-\d)?", players) else None
        port = data.get("joystickPort") if data.get("joystickPort") in (1, 2) else None
        publisher = (str(data.get("publisher") or "").strip()[:120] or None)
        tags = [t for t in (x.lower() for x in strs(data.get("tags"), 6, 30)) if t in TAGS]
        description = str(data.get("description") or "").strip()[:240] or None
        filled: list[str] = []
        with self.sf() as s:
            g = s.get(Game, game_id)
            for field, value in (("year", year), ("publisher", publisher), ("genre", genre), ("players", players),
                                 ("joystick_port", port)):
                if value is not None and not getattr(g, field):
                    setattr(g, field, value)
                    filled.append(field)
            g.extra = {**(g.extra or {}), "details": {
                "description": description, "tags": tags, "filled": filled, "at": time.time(),
                "webSearch": bool(results), "model": getattr(self.ask.provider, "model", None),
                "sources": cited(data, results)}}
            s.commit()
        return {"gameId": game_id, "filled": filled, "description": description, "tags": tags, "genre": genre,
                "sources": cited(data, results)}

    # ---------------------------------------------------------------- the whole library, in the background
    def start_all(self, only_missing: bool = True) -> dict[str, Any]:
        if self._task and not self._task.done():
            return self.job
        with self.sf() as s:
            games = [g for g in s.scalars(select(Game).where(Game.category == "game"))
                     if not only_missing or not (g.extra or {}).get("details")]
            ids = [g.id for g in games]
        self.job = {"running": True, "done": 0, "total": len(ids), "filled": 0, "errors": 0, "current": None,
                    "lastError": None}
        self._task = asyncio.create_task(self._run(ids))
        return self.job

    async def _run(self, ids: list[int]) -> None:
        for gid in ids:
            with self.sf() as s:
                g = s.get(Game, gid)
                self.job["current"] = g.title if g else None
            try:
                r = await self.enrich(gid)
                self.job["filled"] += 1 if (r["filled"] or r["tags"]) else 0
            except (AskError, LookupError) as exc:
                self.job["errors"] += 1
                self.job["lastError"] = str(exc)
                if "needs an AI model" in str(exc):
                    break
            self.job["done"] += 1
            await asyncio.sleep(PAUSE)
        self.job.update(running=False, current=None)
