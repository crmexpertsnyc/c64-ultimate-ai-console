"""In-game help from what the C64 is actually showing (Browser Play reads the screen memory).

* ``hint`` — 💡 "I'm stuck": spoiler-free help in three levels (a nudge → a direction → the solution), from the
  screen text, the player's own question, the game's how-to-play guide and (from level 2) a web search.
* ``copilot`` — 🧭 text adventure co-pilot: from the recent screen transcript it tracks the room, exits and
  inventory, keeps a small map, and suggests the next few commands. Commands are only typed into the C64
  when the player clicks one; they are plain words, checked here.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.db import Game

from .ai_json import ask_json, cited, strs

LEVELS = {
    1: "Give a gentle NUDGE only: point attention to something on screen or a general strategy. No solution.",
    2: "Give a clear DIRECTION: say what to do next in general terms, still without the exact solution.",
    3: "Give the SOLUTION for this spot: the exact steps or commands.",
}
HINT_SYSTEM = """You are a friendly, spoiler-free game coach for Commodore 64 games.
{level}
{sources_rule}
Base the help on the current screen text (read from the C64's memory), the player's question and the game guide.
If the screen text is empty the game is showing graphics; rely on the question and your knowledge.
Keep it under 60 words. Respond with ONE JSON object only:
{{"hint": "<the help>", "confidence": "high|medium|low", "sources": [<numbers of results used>]}}"""
WITH_SOURCES = "Use the numbered web results (walkthroughs, manuals) when they match this game and spot."
NO_SOURCES = ""

COPILOT_SYSTEM = """You are a co-pilot for a classic Commodore 64 text adventure (Scott Adams / Infocom style
two-word parser: VERB NOUN, e.g. GO NORTH, GET LAMP, LOOK, INVENTORY).
From the recent screen transcript (newest at the end) and the notes so far, work out where the player is and
suggest what to try next. Suggest only commands that fit what is on screen; never spoil puzzles beyond the next
sensible step.
Respond with ONE JSON object only:
{{"room": "<current location or null>", "exits": ["N","S","E","W","U","D"…],
  "inventory": ["<items the player carries, if known>"],
  "goal": "<the current aim in a few words, or null>",
  "suggestions": [{{"command": "<2-3 words, capitals>", "why": "<max 10 words>"}}],
  "note": "<one short tip or null>"}}
Give 3-5 suggestions."""

_COMMAND = re.compile(r"^[A-Z0-9][A-Z0-9 ,.'-]{0,29}$")


def _game_context(sf, game_id: int) -> tuple[str, str]:  # noqa: ANN001
    with sf() as s:
        g = s.get(Game, game_id)
        if not g:
            raise LookupError("game not found")
        guide = (g.extra or {}).get("guide") or {}
        title = re.sub(r"\s*\((?:EasyFlash|Cartridge|Preview|NTSC|PAL)\)\s*$", "", g.title)
    lines = []
    if guide.get("summary"):
        lines.append("Guide: " + guide["summary"])
    if guide.get("controls"):
        lines.append("Controls: " + "; ".join(f"{c['action']}: {c['how']}" for c in guide["controls"][:8]))
    if guide.get("tips"):
        lines.append("Tips: " + "; ".join(guide["tips"][:4]))
    return title, "\n".join(lines)


class CoachService:
    def __init__(self, ask_service, session_factory):  # noqa: ANN001
        self.ask = ask_service
        self.sf = session_factory

    async def hint(self, game_id: int, *, screen: str = "", question: str = "", level: int = 1,
                   previous: list[str] | None = None) -> dict[str, Any]:
        title, guide = _game_context(self.sf, game_id)
        level = min(3, max(1, level))
        screen = re.sub(r"\s+", " ", screen).strip()[:1500]
        question = question.strip()[:200]
        search = None
        if level >= 2 or question:
            words = question or " ".join(re.findall(r"[A-Z]{4,}", screen.upper())[:6])
            search = f'"{title}" C64 walkthrough {words}'.strip()
        user = f"Game: {title} (Commodore 64)\nScreen text now: {screen or '(graphics — no readable text)'}"
        if question:
            user += f"\nPlayer asks: {question}"
        if guide:
            user += "\n" + guide
        if previous:
            user += "\nHints already given (go further than these): " + " | ".join(p[:200] for p in previous[-3:])
        data, results = await ask_json(
            self.ask, HINT_SYSTEM.format(level=LEVELS[level], sources_rule=WITH_SOURCES if search else NO_SOURCES),
            user, search=search, max_tokens=1200, what="hint")
        confidence = data.get("confidence") if data.get("confidence") in ("high", "medium", "low") else "medium"
        return {"hint": str(data.get("hint") or "").strip()[:500] or "No hint — try asking a question.",
                "level": level, "confidence": confidence,
                "sources": cited(data, results), "webSearch": bool(results)}

    async def copilot(self, game_id: int, *, transcript: str, notes: dict[str, Any] | None = None) -> dict[str, Any]:
        title, guide = _game_context(self.sf, game_id)
        transcript = transcript.strip()[-4000:]
        if not transcript:
            raise ValueError("no text on screen yet")
        notes = notes or {}
        user = f"Game: {title} (Commodore 64 text adventure)\n"
        if notes.get("map"):
            user += "Map so far: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in list(notes["map"].items())[:30]) + "\n"
        if notes.get("inventory"):
            user += "Inventory so far: " + ", ".join(notes["inventory"][:20]) + "\n"
        if guide:
            user += guide + "\n"
        user += "Recent screen transcript:\n" + transcript
        data, _ = await ask_json(self.ask, COPILOT_SYSTEM, user, max_tokens=1200, what="co-pilot suggestions")
        suggestions = []
        for s in data.get("suggestions") or []:
            if not isinstance(s, dict):
                continue
            cmd = re.sub(r"\s+", " ", str(s.get("command") or "")).strip().upper()
            if _COMMAND.match(cmd) and cmd not in (x["command"] for x in suggestions):
                suggestions.append({"command": cmd, "why": str(s.get("why") or "").strip()[:80]})
        exits = [e.upper()[:10] for e in strs(data.get("exits"), 8, 10)]
        room = (str(data.get("room") or "").strip()[:60] or None)
        # The map grows with every room seen (room → exits); the client keeps it between calls.
        mp = dict(notes.get("map") or {})
        if room:
            mp[room] = exits
        return {"room": room, "exits": exits, "inventory": strs(data.get("inventory"), 20, 40),
                "goal": (str(data.get("goal") or "").strip()[:80] or None), "suggestions": suggestions[:5],
                "note": (str(data.get("note") or "").strip()[:160] or None), "map": mp}
