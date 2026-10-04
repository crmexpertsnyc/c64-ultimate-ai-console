"""The player's taste: what they search for, ask about, play (and for how long), favorite and rate.

Signals are small rows in ``taste_events`` plus thumbs up / down in ``ratings``. ``profile()`` turns them into
a weighted, decaying picture of what the player likes — liked titles with the reasons, disliked titles,
recent searches, and the genres / publishers / decades of the games they enjoy. The recommendation engine
(recommend.py) reads only this profile. Everything stays on this console; ``clear()`` forgets it all.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select

from app.library.titles import title_key
from app.models.db import Game, Rating, TasteEvent
from app.profiles import profile_id

log = logging.getLogger("c64.taste")

KINDS = {"search", "ask", "play", "play_browser", "session", "favorite"}
# How much one event says about liking a game (before time decay). "session" is per minute played.
WEIGHTS = {"play": 2.0, "play_browser": 2.0, "favorite": 3.0, "search": 0.5, "ask": 0.5}
SESSION_PER_MIN, SESSION_MAX = 0.1, 4.0
THUMBS_UP = 6.0
HALF_LIFE_DAYS = 45.0


def _age_days(ts: datetime, now: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (now - ts).total_seconds() / 86400)


class TasteService:
    def __init__(self, session_factory):  # noqa: ANN001
        self.sf = session_factory

    # ---------------------------------------------------------------- signals
    def record(self, kind: str, *, title: str | None = None, game_id: int | None = None, text: str | None = None,
               value: float = 1.0) -> None:
        """Remember one signal. Never raises: taste tracking must not break the action that caused it."""
        if kind not in KINDS:
            return
        try:
            with self.sf() as s:
                if game_id and not title:
                    g = s.get(Game, game_id)
                    title = g.title if g else None
                s.add(TasteEvent(kind=kind, game_id=game_id, profile_id=profile_id(), title=(title or None) and title[:255],
                                 title_key=title_key(title) if title else None,
                                 text=(text or None) and text.strip()[:300], value=float(value)))
                s.commit()
        except Exception as exc:  # noqa: BLE001
            log.info("taste event %s not recorded: %s", kind, exc)

    def rate(self, title: str, value: int, game_id: int | None = None) -> dict[str, Any]:
        """👍 (+1), 👎 (-1) or clear (0) a game."""
        key = title_key(title)
        if not key:
            raise ValueError("title required")
        with self.sf() as s:
            pid = profile_id()
            r = s.scalars(select(Rating).where(Rating.profile_id == pid, Rating.title_key == key)).first()
            if value == 0:
                if r:
                    s.delete(r)
            elif r:
                r.value, r.title, r.game_id = value, title[:255], game_id or r.game_id
            else:
                s.add(Rating(profile_id=pid, title_key=key, title=title[:255], game_id=game_id, value=value))
            s.commit()
        return {"title": title, "value": value}

    def rating(self, title: str) -> int:
        with self.sf() as s:
            r = s.scalars(select(Rating).where(Rating.profile_id == profile_id(),
                                              Rating.title_key == title_key(title))).first()
            return r.value if r else 0

    def ratings(self) -> dict[str, int]:
        with self.sf() as s:
            return {r.title_key: r.value for r in s.scalars(select(Rating).where(Rating.profile_id == profile_id()))}

    def clear(self) -> None:
        with self.sf() as s:
            s.execute(delete(TasteEvent).where(TasteEvent.profile_id == profile_id()))
            s.execute(delete(Rating).where(Rating.profile_id == profile_id()))
            s.commit()

    def forget(self, title: str) -> None:
        """Forget everything about one game (its events and its rating)."""
        key = title_key(title)
        with self.sf() as s:
            s.execute(delete(TasteEvent).where(TasteEvent.profile_id == profile_id(), TasteEvent.title_key == key))
            s.execute(delete(Rating).where(Rating.profile_id == profile_id(), Rating.title_key == key))
            s.commit()

    # ---------------------------------------------------------------- profile
    def profile(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        score: dict[str, float] = defaultdict(float)
        names: dict[str, str] = {}
        ids: dict[str, int] = {}
        why: dict[str, Counter] = defaultdict(Counter)
        minutes: dict[str, float] = defaultdict(float)
        searches: list[str] = []
        with self.sf() as s:
            pid = profile_id()
            events = list(s.scalars(select(TasteEvent).where(TasteEvent.profile_id == pid)
                                    .order_by(TasteEvent.timestamp.desc()).limit(3000)))
            ratings = list(s.scalars(select(Rating).where(Rating.profile_id == pid)))
            for e in events:
                decay = 0.5 ** (_age_days(e.timestamp, now) / HALF_LIFE_DAYS)
                if e.kind in ("search", "ask") and e.text and e.text.lower() not in (x.lower() for x in searches):
                    searches.append(e.text)
                if not e.title_key:
                    continue
                names.setdefault(e.title_key, e.title or e.title_key)
                if e.game_id:
                    ids.setdefault(e.title_key, e.game_id)
                if e.kind == "session":
                    minutes[e.title_key] += e.value
                    score[e.title_key] += min(SESSION_MAX, e.value * SESSION_PER_MIN) * decay
                else:
                    score[e.title_key] += WEIGHTS.get(e.kind, 0) * decay
                    why[e.title_key][e.kind] += 1
            disliked = []
            for r in ratings:
                names[r.title_key] = r.title
                if r.game_id:
                    ids[r.title_key] = r.game_id
                if r.value > 0:
                    score[r.title_key] += THUMBS_UP
                    why[r.title_key]["thumbs_up"] += 1
                else:
                    disliked.append(r.title)
                    score.pop(r.title_key, None)
            disliked_keys = {title_key(t) for t in disliked}
            liked_keys = [k for k, v in sorted(score.items(), key=lambda kv: -kv[1]) if v >= 1.0 and k not in disliked_keys]
            liked = []
            for k in liked_keys[:15]:
                reasons = []
                c = why[k]
                if c["thumbs_up"]:
                    reasons.append("👍")
                plays = c["play"] + c["play_browser"]
                if plays:
                    reasons.append(f"played {plays}×")
                if minutes[k] >= 1:
                    reasons.append(f"{round(minutes[k])} min")
                if c["favorite"]:
                    reasons.append("favorite")
                if c["search"] + c["ask"] and not reasons:
                    reasons.append("searched for")
                liked.append({"title": names[k], "gameId": ids.get(k), "score": round(score[k], 2), "why": reasons})
            # What the liked games have in common (from the library's metadata).
            genres, publishers, decades = Counter(), Counter(), Counter()
            for item in liked:
                g = s.get(Game, item["gameId"]) if item["gameId"] else None
                if not g:
                    continue
                w = max(1, round(item["score"]))
                if g.genre:
                    genres[g.genre] += w
                if g.publisher:
                    publishers[g.publisher] += w
                if g.year:
                    decades[f"{g.year // 10 * 10}s" if g.year < 2000 else "new releases"] += w
        return {
            "liked": liked, "disliked": disliked[:30], "searches": searches[:15],
            "genres": [g for g, _ in genres.most_common(5)], "publishers": [p for p, _ in publishers.most_common(5)],
            "decades": [d for d, _ in decades.most_common(3)],
            "events": len(events), "ratings": len(ratings),
            "fingerprint": _fingerprint(liked, disliked, searches),
        }


def _fingerprint(liked: list[dict[str, Any]], disliked: list[str], searches: list[str]) -> str:
    """Changes when the profile changes enough to deserve new recommendations."""
    top = "|".join(sorted(title_key(x["title"]) for x in liked[:8]))
    return f"{top}#{len(disliked)}#{'|'.join(s.lower() for s in searches[:5])}#{math.floor(len(liked) / 3)}"
