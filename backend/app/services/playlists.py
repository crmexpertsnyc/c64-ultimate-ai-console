"""🎉 Game nights & playlists: "a 4-player party pack for Friday" → an ordered list of real C64 games with the
player count, a time guide and a note for each (controller setup, why it fits), made by the AI from the request,
the player's library and taste. Each game is resolved like an Ask answer so it plays straight away."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.library.titles import title_key
from app.models.db import Game, Playlist

from .ai_json import ask_json

log = logging.getLogger("c64.playlists")

SYSTEM = """You plan Commodore 64 game sessions: game nights, playlists and themed selections.
From the request, make an ordered list of 4-10 real C64 games (use full official titles). Prefer games from the
player's library and ones matching their taste when they fit the request; add well-known or great games otherwise.
Order them so the session flows well (e.g. an easy warm-up first, a crowd-pleaser last).
For multiplayer requests, only include games that really support that many players on the C64 (simultaneous or
taking turns — say which in the note).
Respond with ONE JSON object only:
{"name": "<short catchy name>", "description": "<one sentence>",
 "items": [{"title": "<game>", "players": "<e.g. 1-4>", "minutes": <typical session length>,
            "note": "<max 20 words: why it fits, turns or simultaneous, controllers needed>"}]}"""

SUGGESTIONS = ["4-player party pack for Friday night", "30-minute lunch break", "Best C64 soundtracks to play",
               "Games for kids on a rainy afternoon", "Two-player head-to-head classics", "Hidden gems I've never played"]


class PlaylistService:
    def __init__(self, ask_service, taste, session_factory):  # noqa: ANN001
        self.ask = ask_service
        self.taste = taste
        self.sf = session_factory

    async def create(self, prompt: str) -> dict[str, Any]:
        prompt = prompt.strip()[:300]
        if len(prompt) < 3:
            raise ValueError("describe the playlist or game night")
        with self.sf() as s:
            library = [g.title for g in s.scalars(select(Game).where(Game.category == "game"))][:60]
        profile = self.taste.profile()
        user = f"Request: {prompt}\n"
        if library:
            user += "Games in the player's library: " + "; ".join(library) + "\n"
        if profile["liked"]:
            user += "Games they like: " + "; ".join(x["title"] for x in profile["liked"][:10]) + "\n"
        if profile["disliked"]:
            user += "Games they dislike (never include): " + "; ".join(profile["disliked"][:10]) + "\n"
        data, _ = await ask_json(self.ask, SYSTEM, user, max_tokens=3000, what="playlist")
        disliked = {title_key(t) for t in profile["disliked"]}
        need = _players_needed(prompt)
        items: list[dict[str, Any]] = []
        for it in data.get("items") or []:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()[:120]
            if not title or title_key(title) in disliked or title_key(title) in {title_key(x["title"]) for x in items}:
                continue
            players = str(it.get("players") or "").strip()[:10] or None
            if need and _max_players(players) is not None and _max_players(players) < need:
                continue  # "4-player party pack": a 2-player game does not fit, whatever the model says
            minutes = it.get("minutes")
            items.append({"title": title, "players": players,
                          "minutes": int(minutes) if isinstance(minutes, (int, float)) and 0 < minutes < 600 else None,
                          "note": str(it.get("note") or "").strip()[:160] or None, "done": False})
            if len(items) >= 10:
                break
        if not items:
            raise ValueError("the AI model did not suggest any games — try describing it differently")
        await self._resolve(items)
        with self.sf() as s:
            pl = Playlist(name=str(data.get("name") or prompt).strip()[:120], prompt=prompt,
                          description=str(data.get("description") or "").strip()[:300] or None, items=items)
            s.add(pl)
            s.commit()
            return _dict(pl)

    async def _resolve(self, items: list[dict[str, Any]]) -> None:
        import asyncio

        from .sources import archive_matches
        matched = await self.ask._match_games([i["title"] for i in items])  # noqa: SLF001 - shared matcher
        for i, m in zip(items, matched, strict=True):
            i.update(gameId=m["gameId"], catalog=m["catalog"], browserOk=m["browserOk"], coverUrl=m.get("coverUrl"),
                     matchedTitle=m["title"])
        todo = [i for i in items if not i["gameId"] and not i["catalog"]][:5]
        found = await asyncio.gather(*(archive_matches(i["title"]) for i in todo), return_exceptions=True)
        for i, hits in zip(todo, found, strict=True):
            i["elsewhere"] = [] if isinstance(hits, BaseException) else hits

    def list(self) -> list[dict[str, Any]]:
        with self.sf() as s:
            return [_dict(p, full=False) for p in s.scalars(select(Playlist).order_by(Playlist.created_at.desc()))]

    def get(self, pid: int) -> dict[str, Any]:
        with self.sf() as s:
            p = s.get(Playlist, pid)
            if not p:
                raise LookupError("playlist not found")
            return _dict(p)

    def update_item(self, pid: int, index: int, *, done: bool | None = None, remove: bool = False) -> dict[str, Any]:
        with self.sf() as s:
            p = s.get(Playlist, pid)
            if not p:
                raise LookupError("playlist not found")
            items = list(p.items or [])
            if not 0 <= index < len(items):
                raise LookupError("no such game in the playlist")
            if remove:
                items.pop(index)
            elif done is not None:
                items[index] = {**items[index], "done": done}
            p.items = items
            s.commit()
            return _dict(p)

    def delete(self, pid: int) -> None:
        with self.sf() as s:
            p = s.get(Playlist, pid)
            if p:
                s.delete(p)
                s.commit()


_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8}


def _players_needed(prompt: str) -> int | None:
    """"4-player party", "for four players", "8 players" → 4 / 4 / 8 (None when the request doesn't say)."""
    import re
    m = re.search(r"\b(\d|two|three|four|five|six|eight)[\s-]*(?:player|people|friends|of us)", prompt.lower())
    if not m:
        return None
    n = _WORDS.get(m.group(1)) or int(m.group(1))
    return n if n >= 2 else None


def _max_players(players: str | None) -> int | None:
    import re
    nums = [int(x) for x in re.findall(r"\d+", players or "")]
    return max(nums) if nums else None


def _dict(p: Playlist, full: bool = True) -> dict[str, Any]:
    items = p.items or []
    out = {"id": p.id, "name": p.name, "prompt": p.prompt, "description": p.description,
           "createdAt": p.created_at.isoformat() if p.created_at else None, "count": len(items),
           "done": sum(1 for i in items if i.get("done")),
           "minutes": sum(i.get("minutes") or 0 for i in items),
           "covers": [i.get("coverUrl") for i in items if i.get("coverUrl")][:4]}
    if full:
        out["items"] = items
    return out
