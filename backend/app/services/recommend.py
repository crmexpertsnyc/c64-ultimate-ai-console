"""Game recommendations from the player's taste profile (taste.py).

With an AI model configured, the model is given the profile — liked games and why, disliked games, recent
searches and questions, favourite genres / publishers / eras, and the library's unplayed titles — and asked
for a varied list of real C64 games with a short reason each (optionally grounded by a web search for
"games like …"). Every pick is then matched like an Ask answer: in the library, in the Assembly64 catalog,
or in the online archives, so it can be played straight away. Without AI, unplayed library games that share
genre, publisher or era with the liked ones are suggested instead.

Picks are cached (recommendations.json in the data folder) until the profile changes; 👎 on a pick hides it
at once and teaches the next round.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.ai.providers import AIProviderError, extract_json
from app.library.titles import title_key
from app.models.db import Game
from app.profiles import DEFAULT, profile_id

from .ask import AskError, brave_search

log = logging.getLogger("c64.recommend")

MAX_PICKS = 12
CACHE_HOURS = 12

SYSTEM = """You are the recommendation engine of a Commodore 64 console. From the player's taste profile,
recommend {n} real Commodore 64 games they are likely to love.
Rules:
- Only real games released for the C64 (classics, hidden gems and good modern homebrew all count).
- Use each game's full official title as it is best known on the C64.
- Never recommend a game in the "already liked", "disliked" or "exclude" lists.
- Mix it up: mostly close matches to what they like, plus 2-3 lesser-known gems and 1-2 "try something
  different" picks. Games from their library that they have not played yet are welcome.
- Each reason: one short sentence (max 16 words) that refers to what they like.
- "because": 0-2 titles from their liked list that the pick is most like.
{sources_rule}
Respond with ONE JSON object only:
{{"summary": "<one sentence describing their taste>",
  "picks": [{{"title": "<game>", "reason": "<why>", "because": ["<liked title>"], "kind": "match|gem|different"}}]}}"""
WITH_SOURCES = "Numbered web search results about similar games are included; use them to find good matches."
NO_SOURCES = ""
STARTER = """The player is new — there is no taste profile yet. Recommend {n} all-time great C64 games across
different genres (action, platform, sport, adventure, puzzle, racing, shoot 'em up) so they can discover what
they like. Use kind "match" for all."""


KIDS_RULE = """
This player is a child: recommend only family-friendly games (no gore, no horror, no war or violence themes
beyond cartoon action) that are easy to pick up."""


class RecommendService:
    def __init__(self, ask_service, taste, session_factory, data_path):  # noqa: ANN001
        self.ask = ask_service  # AI provider, Brave key and the Ask game matcher
        self.taste = taste
        self.sf = session_factory
        self._data_path = data_path
        self._lock = asyncio.Lock()
        self.kids = lambda: False  # set by the container: is the current profile a child's?

    @property
    def cache_path(self) -> Path:
        pid = profile_id()
        return Path(self._data_path()) / ("recommendations.json" if pid == DEFAULT else f"recommendations-p{pid}.json")

    # ---------------------------------------------------------------- read
    def cached(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def current(self) -> dict[str, Any]:
        """The last recommendations (without generating), with 👍/👎 applied and whether they are out of date."""
        profile = self.taste.profile()
        data = self.cached() or {"picks": [], "again": [], "generatedAt": 0}
        stale = (data.get("fingerprint") != profile["fingerprint"]
                 or time.time() - data.get("generatedAt", 0) > CACHE_HOURS * 3600)
        return self._apply_ratings({**data, "stale": stale, "profile": _public(profile)})

    def _apply_ratings(self, data: dict[str, Any]) -> dict[str, Any]:
        ratings = self.taste.ratings()
        picks = []
        for p in data.get("picks", []):
            r = ratings.get(title_key(p["title"]), 0)
            if r < 0:
                continue  # 👎 hides a pick right away
            picks.append({**p, "rating": r})
        return {**data, "picks": picks}

    # ---------------------------------------------------------------- generate
    async def refresh(self) -> dict[str, Any]:
        async with self._lock:
            profile = self.taste.profile()
            provider = self.ask.provider
            if provider.configured:
                try:
                    data = await self._with_ai(profile)
                except AskError as exc:
                    log.info("AI recommendations failed, using the library instead: %s", exc)
                    data = self._from_library(profile, note=str(exc))
            else:
                data = self._from_library(profile, note="Set up an AI model (Settings → AI assistant) for smarter picks.")
            data.update(generatedAt=time.time(), fingerprint=profile["fingerprint"], again=self._again(profile))
            try:
                self.cache_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except OSError as exc:
                log.info("recommendations not cached: %s", exc)
            return self._apply_ratings({**data, "stale": False, "profile": _public(profile)})

    async def _with_ai(self, profile: dict[str, Any]) -> dict[str, Any]:
        library = self._library_titles()
        played = {title_key(t) for t in library["played"]}
        exclude = {title_key(x["title"]) for x in profile["liked"]} | {title_key(t) for t in profile["disliked"]}
        new_player = not profile["liked"] and not profile["searches"]
        results: list[dict[str, str]] = []
        if self.ask.web_search_on and profile["liked"]:
            like = " ".join(f'"{x["title"]}"' for x in profile["liked"][:2])
            try:
                results = await brave_search(f"best Commodore 64 games like {like}", self.ask.settings.BRAVE_API_KEY, count=5)
            except AskError as exc:
                log.info("recommendation web search failed: %s", exc)
        system = SYSTEM.format(n=MAX_PICKS + 2, sources_rule=WITH_SOURCES if results else NO_SOURCES)
        if self.kids():
            system += KIDS_RULE
        if new_player:
            user = STARTER.format(n=MAX_PICKS + 2)
        else:
            lines = ["Player's taste profile:"]
            lines.append("Already liked (most first): " + "; ".join(
                f'{x["title"]} ({", ".join(x["why"])})' if x["why"] else x["title"] for x in profile["liked"][:12]))
            if profile["disliked"]:
                lines.append("Disliked: " + "; ".join(profile["disliked"][:15]))
            if profile["searches"]:
                lines.append("Recent searches and questions: " + "; ".join(profile["searches"][:10]))
            for label, key in (("Favourite genres", "genres"), ("Favourite publishers", "publishers"),
                               ("Favourite eras", "decades")):
                if profile[key]:
                    lines.append(f"{label}: " + ", ".join(profile[key]))
            if library["unplayed"]:
                lines.append("In their library but not played yet: " + "; ".join(library["unplayed"][:30]))
            if library["played"]:
                lines.append("Exclude (already played): " + "; ".join(library["played"][:30]))
            user = "\n".join(lines)
        if results:
            user += "\n\nWeb search results:\n" + "\n".join(
                f"[{n}] {r['title']}\n{r['description'][:200]}" for n, r in enumerate(results, 1))
        raw = ""
        try:
            st = self.ask.settings
            raw = await self.ask.provider.complete(system, user, max_tokens=max(st.ASK_MAX_TOKENS, 4000),
                                                   timeout=st.RECOMMEND_TIMEOUT)
            reply = extract_json(raw)
        except (AIProviderError, ValueError) as exc:
            log.info("recommendation reply not usable (%d chars, ends: %r)", len(raw or ""), (raw or "")[-200:])
            raise AskError(f"The AI model could not make recommendations: {exc}") from exc
        picks: list[dict[str, Any]] = []
        seen: set[str] = set()
        liked_names = {title_key(x["title"]): x["title"] for x in profile["liked"]}
        for p in reply.get("picks") or []:
            if not isinstance(p, dict):
                continue
            title = str(p.get("title") or "").strip()[:120]
            key = title_key(title)
            if not key or key in seen or key in exclude or key in played:
                continue
            seen.add(key)
            because = [liked_names[title_key(b)] for b in (p.get("because") or [])[:2]
                       if isinstance(b, str) and title_key(b) in liked_names]
            kind = p.get("kind") if p.get("kind") in ("match", "gem", "different") else "match"
            picks.append({"title": title, "reason": str(p.get("reason") or "").strip()[:200], "because": because,
                          "kind": kind})
            if len(picks) >= MAX_PICKS:
                break
        await self._resolve(picks)
        return {"summary": str(reply.get("summary") or "").strip()[:240] or None, "picks": picks, "ai": True,
                "webSearch": bool(results), "model": getattr(self.ask.provider, "model", None), "note": None}

    async def _resolve(self, picks: list[dict[str, Any]]) -> None:
        """Make each pick playable: library → Assembly64 catalog → online archives."""
        if not picks:
            return
        matched = await self.ask._match_games([p["title"] for p in picks])  # noqa: SLF001 - shared matcher
        for p, m in zip(picks, matched, strict=True):
            p.update(gameId=m["gameId"], catalog=m["catalog"], browserOk=m["browserOk"], coverUrl=m.get("coverUrl"),
                     matchedTitle=m["title"])
        from .sources import archive_matches
        todo = [p for p in picks if not p["gameId"] and not p["catalog"]][:5]
        found = await asyncio.gather(*(archive_matches(p["title"]) for p in todo), return_exceptions=True)
        for p, hits in zip(todo, found, strict=True):
            p["elsewhere"] = [] if isinstance(hits, BaseException) else hits

    def _library_titles(self) -> dict[str, list[str]]:
        with self.sf() as s:
            games = list(s.scalars(select(Game).where(Game.category == "game")))
        return {"played": [g.title for g in games if g.play_count], "unplayed": [g.title for g in games if not g.play_count]}

    def _from_library(self, profile: dict[str, Any], note: str | None = None) -> dict[str, Any]:
        """No AI: unplayed library games that share genre, publisher or era with the liked ones."""
        disliked = {title_key(t) for t in profile["disliked"]}
        liked_ids = {x["gameId"] for x in profile["liked"] if x["gameId"]}
        picks = []
        with self.sf() as s:
            liked_games = [g for g in (s.get(Game, i) for i in liked_ids) if g]
            for g in s.scalars(select(Game).where(Game.category == "game")):
                if g.id in liked_ids or title_key(g.title) in disliked or g.play_count:
                    continue
                reason, because, score = "In your library — not played yet.", [], 0
                for lg in liked_games:
                    if g.genre and g.genre == lg.genre:
                        reason, because, score = f"Another {g.genre.lower()} game, like {lg.title}.", [lg.title], 3
                        break
                    if g.publisher and g.publisher == lg.publisher and score < 2:
                        reason, because, score = f"Also by {g.publisher}, who made {lg.title}.", [lg.title], 2
                    elif g.year and lg.year and abs(g.year - lg.year) <= 2 and score < 1:
                        reason, because, score = f"From the same era as {lg.title}.", [lg.title], 1
                picks.append((score, {"title": g.title, "reason": reason, "because": because, "kind": "match",
                                      "gameId": g.id, "catalog": None, "coverUrl": g.cover_url,
                                      "browserOk": True, "matchedTitle": g.title}))
        picks.sort(key=lambda t: -t[0])
        return {"summary": None, "picks": [p for _, p in picks[:MAX_PICKS]], "ai": False, "webSearch": False,
                "model": None, "note": note}

    def _again(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        """Liked library games not played for a few days: "Play again"."""
        out = []
        cutoff = datetime.now(UTC) - timedelta(days=3)
        with self.sf() as s:
            for x in profile["liked"]:
                g = s.get(Game, x["gameId"]) if x["gameId"] else None
                if not g:
                    continue
                last = g.last_played.replace(tzinfo=UTC) if g.last_played and g.last_played.tzinfo is None else g.last_played
                if last and last > cutoff:
                    continue
                out.append({"title": g.title, "gameId": g.id, "coverUrl": g.cover_url, "why": x["why"]})
                if len(out) >= 6:
                    break
        return out


def _public(profile: dict[str, Any]) -> dict[str, Any]:
    return {k: profile[k] for k in ("liked", "disliked", "searches", "genres", "publishers", "decades", "events", "ratings")}

