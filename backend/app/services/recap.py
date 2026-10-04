"""📊 Your week: play time, most-played games, days active and new discoveries over the last 7 days (from the taste
signals), with an AI-written summary and three challenges for the games you played most ("reach round 20 in
Bubble Bobble"). Cached per ISO week; challenges can be ticked off."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.models.db import Game, TasteEvent
from app.profiles import DEFAULT, profile_id

from .ai_json import ask_json
from .ask import AskError

log = logging.getLogger("c64.recap")

SYSTEM = """You write a short, upbeat weekly recap for a Commodore 64 player, like a friendly gaming buddy.
From the stats, write a headline and a two-sentence summary, then three fun, achievable challenges for the coming
week — each for one of the games they played (or a close match), concrete and checkable by the player
(a score, a level/round, beating a friend, finishing an event). Add a one-line tip for each.
Only use goals and mechanics that really exist in that game — for a text adventure, a story milestone (reach a
place, solve a known first puzzle); never invent levels, units or features. If unsure about a game, pick another.
Respond with ONE JSON object only:
{"headline": "<max 8 words>", "summary": "<two sentences>",
 "challenges": [{"game": "<title>", "challenge": "<max 15 words>", "tip": "<max 15 words>"}]}"""


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts


class RecapService:
    def __init__(self, ask_service, session_factory, data_path):  # noqa: ANN001
        self.ask = ask_service
        self.sf = session_factory
        self._data_path = data_path

    def _path(self, now: datetime) -> Path:
        y, w, _ = now.isocalendar()
        pid = profile_id()
        folder = Path(self._data_path()) / "recaps" / ("" if pid == DEFAULT else f"p{pid}")
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{y}-W{w:02d}.json"

    def stats(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        start = now - timedelta(days=7)
        minutes: dict[str, float] = defaultdict(float)
        plays: dict[str, int] = defaultdict(int)
        names: dict[str, str] = {}
        ids: dict[str, int] = {}
        days: set[str] = set()
        searches = 0
        with self.sf() as s:
            first_seen: dict[str, datetime] = {}
            for e in s.scalars(select(TasteEvent).where(TasteEvent.profile_id == profile_id()).order_by(TasteEvent.timestamp)):
                ts = _utc(e.timestamp)
                if e.title_key:
                    first_seen.setdefault(e.title_key, ts)
                if ts < start:
                    continue
                days.add(ts.date().isoformat())
                if e.kind in ("search", "ask"):
                    searches += 1
                if not e.title_key:
                    continue
                names[e.title_key] = e.title or e.title_key
                if e.game_id:
                    ids[e.title_key] = e.game_id
                if e.kind == "session":
                    minutes[e.title_key] += e.value
                elif e.kind in ("play", "play_browser"):
                    plays[e.title_key] += 1
            keys = set(minutes) | set(plays)
            top = sorted(keys, key=lambda k: (-minutes[k], -plays[k]))
            games = []
            for k in top[:8]:
                g = s.get(Game, ids[k]) if k in ids else None
                games.append({"title": names[k], "gameId": ids.get(k), "minutes": round(minutes[k]),
                              "plays": plays[k], "coverUrl": g.cover_url if g else None})
            new = [names[k] for k in keys if first_seen.get(k, now) >= start]
        return {"from": start.isoformat(), "to": now.isoformat(), "minutes": round(sum(minutes.values())),
                "plays": sum(plays.values()), "daysActive": len(days), "searches": searches,
                "games": games, "newGames": sorted(new)[:10]}

    async def get(self, refresh: bool = False, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        path = self._path(now)
        if not refresh:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if time.time() - cached.get("generatedAt", 0) < 6 * 3600:
                    return cached
                done = [c.get("done") for c in cached.get("challenges", [])]
            except (OSError, ValueError):
                done = []
        else:
            done = []
        st = self.stats(now)
        recap: dict[str, Any] = {"stats": st, "headline": None, "summary": None, "challenges": [], "ai": False,
                                 "note": None, "generatedAt": time.time()}
        if not st["games"]:
            recap["note"] = "No games played in the last 7 days yet — play something and check back."
        elif self.ask.provider.configured:
            lines = [f"Last 7 days: {st['minutes']} minutes played in the browser, {st['plays']} game launches, "
                     f"active on {st['daysActive']} days, {st['searches']} searches or questions."]
            lines.append("Most played: " + "; ".join(
                f"{g['title']} ({g['minutes']} min, {g['plays']} plays)" for g in st["games"][:5]))
            if st["newGames"]:
                lines.append("New this week: " + "; ".join(st["newGames"]))
            try:
                data, _ = await ask_json(self.ask, SYSTEM, "\n".join(lines), max_tokens=1500, what="weekly recap")
                recap.update(headline=str(data.get("headline") or "").strip()[:80] or None,
                             summary=str(data.get("summary") or "").strip()[:400] or None, ai=True)
                by_title = {g["title"].lower(): g["gameId"] for g in st["games"]}
                for n, c in enumerate((data.get("challenges") or [])[:3]):
                    if isinstance(c, dict) and c.get("challenge"):
                        game = str(c.get("game") or "").strip()[:120]
                        recap["challenges"].append({
                            "game": game, "gameId": by_title.get(game.lower()),
                            "challenge": str(c["challenge"]).strip()[:140], "tip": str(c.get("tip") or "").strip()[:140],
                            "done": bool(done[n]) if n < len(done) else False})
            except AskError as exc:
                recap["note"] = str(exc)
        else:
            recap["note"] = "Set up an AI model (Settings → AI assistant) for a written recap and weekly challenges."
        path.write_text(json.dumps(recap, ensure_ascii=False), encoding="utf-8")
        return recap

    def set_done(self, index: int, done: bool, now: datetime | None = None) -> dict[str, Any]:
        path = self._path(now or datetime.now(UTC))
        try:
            recap = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LookupError("no recap this week yet") from exc
        if not 0 <= index < len(recap.get("challenges", [])):
            raise LookupError("no such challenge")
        recap["challenges"][index]["done"] = done
        path.write_text(json.dumps(recap, ensure_ascii=False), encoding="utf-8")
        return recap
