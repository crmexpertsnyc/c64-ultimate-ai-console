"""'How to play' guides: how to start a game, its controls, joystick port and tips — researched on the
web (Brave, when a key is set) and summarised by the configured AI into a structured, sourced guide.

The guide is stored on the title (Game.extra["guide"]) so it is only researched once. Its machine-usable
parts feed the rest of the app: the joystick port (Auto port / Controls card) and the start keys
(smart start can step through them).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.ai.providers import AIProviderError, extract_json
from app.models.db import Game

from .ask import AskError, brave_search

log = logging.getLogger("c64.guide")

# Keys a guide may use for start steps (also what smart start can press).
START_KEYS = {"RETURN", "SPACE", "FIRE", "RUN/STOP", "RESTORE", "F1", "F3", "F5", "F7", "Y", "N",
              "UP", "DOWN", "LEFT", "RIGHT", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"}

SYSTEM = """You write short, practical "how to play" guides for Commodore 64 games, for someone playing
right now with a joystick or gamepad and a PC keyboard.
{sources_rule}
Respond with ONE JSON object only:
{{"summary": "<one or two sentences: what the game is>",
  "start": ["<ordered steps from loading to playing, e.g. 'Press F7 to begin'>"],
  "startKeys": ["<the keys pressed in those steps, in order, each one of: {keys}>"],
  "controls": [{{"action": "<e.g. Move, Jump, Fire>", "how": "<joystick direction / button / key>"}}],
  "joystickPort": <1 or 2 or null if unknown>,
  "players": "<e.g. 1, 1-2, 1-8, or null>",
  "tips": ["<up to 4 short tips>"],
  "sources": [<numbers of the search results you used>]}}
Keep every string short. If something is unknown, leave it out rather than guessing."""
WITH_SOURCES = ("Use the numbered web search results below (manuals, reviews, forums) as your source and do "
                "not invent controls or keys that are not supported by them.")
NO_SOURCES = "No web search results are available: use your own knowledge and leave out anything you are unsure of."


class GuideService:
    def __init__(self, ask_service, session_factory):  # noqa: ANN001
        self.ask = ask_service  # reuses its AI provider, Brave key and settings
        self.sf = session_factory

    def get(self, game_id: int) -> dict[str, Any] | None:
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            return (g.extra or {}).get("guide")

    async def generate(self, game_id: int) -> dict[str, Any]:
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            title, year, publisher = g.title, g.year, g.publisher
        provider = self.ask.provider
        if not provider.configured:
            raise AskError("Guides need an AI model — set one up under Settings → AI assistant")
        clean = re.sub(r"\s*\((?:EasyFlash|Cartridge|Preview|NTSC|PAL)\)\s*$", "", title)
        results: list[dict[str, str]] = []
        if self.ask.web_search_on:
            try:
                results = await brave_search(f'"{clean}" Commodore 64 how to play controls keys joystick instructions',
                                             self.ask.settings.BRAVE_API_KEY)
            except AskError as exc:
                log.info("guide search failed: %s", exc)
        numbered = "\n".join(f"[{n}] {r['title']} — {r['url']}\n{r['description']}" for n, r in enumerate(results, 1))
        system = SYSTEM.format(sources_rule=WITH_SOURCES if results else NO_SOURCES, keys=", ".join(sorted(START_KEYS)))
        about = clean + (f" ({year}" + (f", {publisher}" if publisher else "") + ")" if year else "")
        user = f"Game: {about} for the Commodore 64." + (f"\n\nWeb search results:\n{numbered}" if results else "")
        try:
            raw = await provider.complete(system, user, max_tokens=self.ask.settings.ASK_MAX_TOKENS)
            data = extract_json(raw)
        except (AIProviderError, ValueError) as exc:
            raise AskError(f"The AI model could not write a guide: {exc}") from exc
        guide = _clean(data, results)
        guide.update(generatedAt=time.time(), webSearch=bool(results), model=getattr(provider, "model", None))
        with self.sf() as s:
            g = s.get(Game, game_id)
            g.extra = {**(g.extra or {}), "guide": guide}
            # Fill gaps in the title's metadata (never overwrite what is already known or user-set).
            if guide.get("joystickPort") and not g.joystick_port:
                g.joystick_port = guide["joystickPort"]
            if guide.get("players") and not g.players:
                g.players = guide["players"]
            s.commit()
        return guide


def _strs(v: Any, limit: int, length: int = 160) -> list[str]:
    return [str(x).strip()[:length] for x in (v or []) if str(x).strip()][:limit]


def _clean(data: dict[str, Any], results: list[dict[str, str]]) -> dict[str, Any]:
    raw_keys = _strs(data.get("startKeys"), 12, 12)
    keys = [k.strip().upper().replace("RUNSTOP", "RUN/STOP").replace("ENTER", "RETURN") for k in raw_keys]
    port = data.get("joystickPort")
    used = {int(n) for n in (data.get("sources") or []) if str(n).isdigit()}
    return {
        "summary": str(data.get("summary") or "").strip()[:400],
        "start": _strs(data.get("start"), 10),
        "startKeys": [k for k in keys if k in START_KEYS],
        "controls": [{"action": str(c.get("action", ""))[:40], "how": str(c.get("how", ""))[:80]}
                     for c in (data.get("controls") or []) if isinstance(c, dict) and c.get("action")][:10],
        "joystickPort": port if port in (1, 2) else None,
        "players": (str(data["players"]).strip()[:10] or None) if data.get("players") else None,
        "tips": _strs(data.get("tips"), 4, 200),
        "sources": [{"n": n, "title": r["title"], "url": r["url"]} for n, r in enumerate(results, 1) if n in used],
    }
