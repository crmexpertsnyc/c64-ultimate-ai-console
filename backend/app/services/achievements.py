"""🏆 Verified scores and achievements, read from what the game itself shows.

Most C64 games print their score, round and lives on screen. The screen sample (browser emulator or the real
C64's memory, read-only) gives the text of every row; ``read_metrics`` finds labelled numbers — "SCORE 012340",
"ROUND 5", "LIVES 3", or the number printed under its label — and maps them to metrics:
score · level · lives · time. A value only counts when it is the same in two samples in a row (no flicker, no
half-drawn digits), and a score may not jump by more than a sane amount between samples.

Each game gets achievements: a few built-ins for every game (into the game, an hour played, three different
days) and, on request or once its HUD has been seen, 4–6 drafted by the AI from the metrics this game actually
shows. They unlock by themselves; best scores go on a per-person leaderboard. On the real C64 the console reads
the screen itself, so those scores are fully verified; in the browser the player's own page reports what it read.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.models.db import Achievement, AchievementUnlock, Game, HighScore, Profile, TasteEvent
from app.profiles import profile_id

from .ai_json import ask_json
from .ask import AskError

log = logging.getLogger("c64.achievements")

LABELS = {
    "score": ["SCORE", "POINTS", "PTS", "1UP", "P1", "PLAYER 1", "PLAYER ONE"],
    "hiscore": ["HIGH SCORE", "HI-SCORE", "HISCORE", "HI", "TOP", "BEST", "RECORD"],  # the table's record, not yours
    "lives": ["LIVES", "MEN", "SHIPS", "LIFE", "LEFT"],
    "level": ["LEVEL", "LVL", "ROUND", "STAGE", "WAVE", "SCREEN", "ZONE", "SECTOR", "AREA", "ROOM"],
    "time": ["TIME"],
}
# The longest label first, so "HIGH SCORE" is not read as "SCORE".
_ORDER = sorted(((lab, m) for m, labs in LABELS.items() for lab in labs), key=lambda x: -len(x[0]))
MAX_DIGITS = {"score": 9, "hiscore": 9, "lives": 2, "level": 3, "time": 4}
METRICS = ("score", "level", "lives", "time", "minutes", "days", "gameplay")
BUILTINS = [
    {"key": "into-the-game", "title": "Into the game", "description": "Reach the game itself (past intros and menus).",
     "icon": "🎮", "metric": "gameplay", "target": 1},
    {"key": "hour-of-power", "title": "Hour of power", "description": "Play this game for 60 minutes in total.",
     "icon": "⏱", "metric": "minutes", "target": 60},
    {"key": "regular", "title": "Regular", "description": "Play this game on 3 different days.",
     "icon": "📅", "metric": "days", "target": 3},
]

SYSTEM = """You design achievements for a Commodore 64 game. The console can only check numbers it reads from the
game's own screen: {metrics}. Design {n} achievements using ONLY those metrics, from easy to hard, with
realistic targets for this game on the C64 (a beginner should get the first one in a few minutes; the last
should take real skill). "score" = points, "level" = level/round/stage reached, "lives" = lives remaining
(e.g. "reach round 5 with 3 lives" is not possible — one metric per achievement), "time" = the game's own timer.
Respond with ONE JSON object only:
{{"achievements": [{{"title": "<max 4 words, fun>", "description": "<max 14 words>", "icon": "<one emoji>",
   "metric": "<one of the metrics>", "target": <whole number>}}]}}"""


def _number_after(text: str, start: int, digits: int) -> int | None:
    m = re.match(r"[\s:.=\-]*(\d{1,%s})(?!\d)".replace("%s", str(digits)), text[start:])
    return int(m.group(1)) if m else None


def read_metrics(rows: list[str], raw_rows: list[str] | None = None) -> dict[str, int]:
    """Labelled numbers on screen → {"score": 12340, "level": 5, …}. First match per metric wins (top to bottom)."""
    out: dict[str, int] = {}
    raw_rows = raw_rows or []
    for r, text in enumerate(rows):
        t = text.upper()
        used: list[tuple[int, int]] = []
        for label, metric in _ORDER:
            for m in re.finditer(r"(?<![A-Z])" + re.escape(label) + r"(?![A-Z])", t):
                if any(a <= m.start() < b for a, b in used) or metric in out:
                    continue
                used.append((m.start(), m.end()))
                n = _number_after(t, m.end(), MAX_DIGITS[metric])
                if n is None and metric in ("lives", "level"):  # "3 LIVES", "5 ROUND"? (number before)
                    before = re.search(r"(\d{1,3})\s*$", t[:m.start()])
                    n = int(before.group(1)) if before else None
                if n is None and r + 1 < len(raw_rows) and r < len(raw_rows):
                    # the number printed under its label (same columns)
                    col = raw_rows[r].upper().find(label)
                    if col >= 0:
                        below = raw_rows[r + 1][max(0, col - 2):col + len(label) + 4]
                        mm = re.search(r"\d{1,%s}".replace("%s", str(MAX_DIGITS[metric])), below)
                        n = int(mm.group(0)) if mm else None
                if n is not None:
                    out[metric] = n
    return out


class AchievementService:
    def __init__(self, ask_service, session_factory):  # noqa: ANN001
        self.ask = ask_service
        self.sf = session_factory
        # (profile, game, source) → recent readings, so a value counts only when seen twice in a row
        self._last: dict[tuple[int, int, str], dict[str, int]] = {}
        self._seen: dict[int, dict[str, int]] = defaultdict(dict)  # game → metric → highest value seen (for the AI)
        self._ensure_builtins()

    def _ensure_builtins(self) -> None:
        with self.sf() as s:
            have = {a.key for a in s.scalars(select(Achievement).where(Achievement.game_id.is_(None)))}
            for b in BUILTINS:
                if b["key"] not in have:
                    s.add(Achievement(game_id=None, source="builtin", **b))
            s.commit()

    # ---------------------------------------------------------------- progress
    def progress(self, game_id: int, sample: dict[str, Any], *, source: str, playing: bool = True) -> dict[str, Any]:
        """One screen sample from a game being played → stable metrics, new best score, newly unlocked achievements."""
        pid = profile_id()
        rows = [str(x) for x in (sample.get("rows") or [])][:25]
        raw = [str(x) for x in (sample.get("rawRows") or [])][:25]
        found = read_metrics(rows, raw) if sample.get("mode") == "text" else {}
        key = (pid, game_id, source)
        prev = self._last.get(key, {})
        stable = {m: v for m, v in found.items() if prev.get(m) == v}
        # A score that leaps absurdly between two readings is a misread (another number on screen), not points.
        if "score" in stable and prev.get("_score_ok") is not None and stable["score"] > prev["_score_ok"] * 50 + 100_000:
            stable.pop("score")
        self._last[key] = {**found, "_score_ok": stable.get("score", prev.get("_score_ok", 0))}
        for m, v in stable.items():
            self._seen[game_id][m] = max(v, self._seen[game_id].get(m, 0))
        new_best = None
        unlocked: list[dict[str, Any]] = []
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            if playing and stable.get("score"):
                best = s.scalar(select(func.max(HighScore.score))
                                .where(HighScore.game_id == game_id, HighScore.profile_id == pid))
                if best is None or stable["score"] > best:
                    hs = s.scalars(select(HighScore).where(HighScore.game_id == game_id, HighScore.profile_id == pid,
                                                           HighScore.source == source)
                                   .order_by(HighScore.achieved_at.desc())).first()
                    when = None
                    if hs:
                        when = hs.achieved_at.replace(tzinfo=UTC) if hs.achieved_at.tzinfo is None else hs.achieved_at
                    recent = bool(when) and (datetime.now(UTC) - when).total_seconds() < 6 * 3600
                    if recent:  # the same session climbing: update it rather than flooding the table
                        hs.score, hs.level, hs.achieved_at = stable["score"], stable.get("level"), datetime.now(UTC)
                    else:
                        s.add(HighScore(game_id=game_id, profile_id=pid, score=stable["score"], level=stable.get("level"),
                                        source=source))
                    new_best = stable["score"]
            if playing:
                values = dict(stable)
                values["gameplay"] = 1 if (stable or sample.get("gameplay")) else 0
                values.update(self._play_stats(s, game_id, pid))
                done = set(s.scalars(select(AchievementUnlock.achievement_id).where(
                    AchievementUnlock.game_id == game_id, AchievementUnlock.profile_id == pid)))
                for a in self._for_game(s, game_id):
                    if a.id in done or a.metric not in values:
                        continue
                    if values[a.metric] >= a.target:
                        s.add(AchievementUnlock(achievement_id=a.id, game_id=game_id, profile_id=pid,
                                                value=values[a.metric], source=source))
                        unlocked.append(_ach(a, unlocked=True))
            s.commit()
        return {"metrics": stable, "read": found, "newBest": new_best, "unlocked": unlocked}

    def _play_stats(self, s, game_id: int, pid: int) -> dict[str, int]:  # noqa: ANN001
        minutes = s.scalar(select(func.coalesce(func.sum(TasteEvent.value), 0)).where(
            TasteEvent.game_id == game_id, TasteEvent.profile_id == pid, TasteEvent.kind == "session")) or 0
        days = s.scalar(select(func.count(func.distinct(func.date(TasteEvent.timestamp)))).where(
            TasteEvent.game_id == game_id, TasteEvent.profile_id == pid,
            TasteEvent.kind.in_(("play", "play_browser", "session")))) or 0
        return {"minutes": int(minutes), "days": int(days)}

    def _for_game(self, s, game_id: int) -> list[Achievement]:  # noqa: ANN001
        return list(s.scalars(select(Achievement).where((Achievement.game_id == game_id) | Achievement.game_id.is_(None))
                              .order_by(Achievement.game_id.is_(None).desc(), Achievement.id)))

    # ---------------------------------------------------------------- read
    def for_game(self, game_id: int) -> dict[str, Any]:
        pid = profile_id()
        with self.sf() as s:
            if not s.get(Game, game_id):
                raise LookupError("game not found")
            people = {p.id: p for p in s.scalars(select(Profile))}
            unlocks = defaultdict(list)
            for u in s.scalars(select(AchievementUnlock).where(AchievementUnlock.game_id == game_id)):
                p = people.get(u.profile_id)
                unlocks[u.achievement_id].append({"profileId": u.profile_id, "name": p.name if p else "?",
                                                  "emoji": p.emoji if p else "🙂", "at": _iso(u.unlocked_at),
                                                  "source": u.source})
            items = []
            for a in self._for_game(s, game_id):
                d = _ach(a, unlocked=any(x["profileId"] == pid for x in unlocks[a.id]))
                d["unlockedBy"] = unlocks[a.id]
                items.append(d)
            stats = self._play_stats(s, game_id, pid)
        return {"achievements": items, "seen": self._seen.get(game_id, {}), "progress": stats,
                "hasGameAchievements": any(i["gameId"] for i in items)}

    def scores(self, game_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with self.sf() as s:
            people = {p.id: p for p in s.scalars(select(Profile))}
            rows = list(s.scalars(select(HighScore).where(HighScore.game_id == game_id)
                                  .order_by(HighScore.score.desc(), HighScore.achieved_at).limit(limit)))
            return [{"score": h.score, "level": h.level, "source": h.source, "at": _iso(h.achieved_at), "profileId": h.profile_id,
                     "name": people[h.profile_id].name if h.profile_id in people else "?",
                     "emoji": people[h.profile_id].emoji if h.profile_id in people else "🙂"} for h in rows]

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        with self.sf() as s:
            people = {p.id: p for p in s.scalars(select(Profile))}
            out = []
            for u in s.scalars(select(AchievementUnlock).order_by(AchievementUnlock.unlocked_at.desc()).limit(limit)):
                a, g = s.get(Achievement, u.achievement_id), s.get(Game, u.game_id)
                if a and g:
                    p = people.get(u.profile_id)
                    out.append({**_ach(a, unlocked=True), "game": g.title, "gameId": g.id, "at": _iso(u.unlocked_at),
                                "name": p.name if p else "?", "emoji": p.emoji if p else "🙂", "source": u.source})
            return out

    # ---------------------------------------------------------------- 🤖 draft
    async def generate(self, game_id: int, metrics: list[str] | None = None) -> dict[str, Any]:
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            title, guide = g.title, (g.extra or {}).get("guide") or {}
        seen = self._seen.get(game_id, {})
        usable = [m for m in (metrics or list(seen)) if m in ("score", "level", "lives", "time")]
        if not usable:
            raise AskError("Play the game for a moment first — I need to see its score or round on screen to make "
                           "achievements I can check.")
        user = f"Game: {title} (Commodore 64)\nNumbers seen on its screen so far: " + \
            ", ".join(f"{m} up to {seen.get(m, '?')}" for m in usable)
        if guide.get("summary"):
            user += f"\nAbout the game: {guide['summary']}"
        data, _ = await ask_json(self.ask, SYSTEM.format(metrics=", ".join(usable), n=5), user, max_tokens=1500,
                                 what="achievements")
        made = []
        with self.sf() as s:
            for old in s.scalars(select(Achievement).where(Achievement.game_id == game_id, Achievement.source == "ai")):
                if not s.scalars(select(AchievementUnlock).where(AchievementUnlock.achievement_id == old.id)).first():
                    s.delete(old)  # replace drafts nobody has earned yet
            for i, a in enumerate((data.get("achievements") or [])[:6]):
                if not isinstance(a, dict) or a.get("metric") not in usable:
                    continue
                try:
                    target = int(a.get("target"))
                except (TypeError, ValueError):
                    continue
                if not 1 <= target <= 10 ** 8:
                    continue
                row = Achievement(game_id=game_id, key=f"ai-{int(time.time())}-{i}", source="ai",
                                  title=str(a.get("title") or "Achievement").strip()[:80],
                                  description=str(a.get("description") or "").strip()[:200],
                                  icon=(str(a.get("icon") or "🏆").strip() or "🏆")[:8], metric=a["metric"], target=target)
                s.add(row)
                made.append(row)
            s.commit()
            out = [_ach(a, unlocked=False) for a in made]
        if not out:
            raise AskError("The AI model did not suggest achievements this game can check — try again later.")
        return {"achievements": out}


def _iso(ts: datetime | None) -> str | None:
    if ts is None:
        return None
    return (ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts).isoformat()


def _ach(a: Achievement, unlocked: bool) -> dict[str, Any]:
    return {"id": a.id, "gameId": a.game_id, "title": a.title, "description": a.description, "icon": a.icon,
            "metric": a.metric, "target": a.target, "source": a.source, "unlocked": unlocked}
