"""Ask mode: answer questions about the C64 with the configured AI, optionally grounded in a Brave web search.

    question ──► Brave Search (if a key is set) ──► AI (JSON: answer + games it names + sources used)
             ──► each game matched to the library, else to the Assembly64 catalog (so it can be played)

Safety: this path only produces text for the user and a list of titles. It never performs a machine
action; playing a game is a separate, explicit click (📺 On my C64 / 💻 In browser).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.ai.providers import AIProvider, AIProviderError, extract_json
from app.config import Settings
from app.library.formats import browser_playable
from app.library.repository import LibraryRepository
from app.library.titles import title_key

log = logging.getLogger("c64.ask")

BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_GAMES = 10

SYSTEM = """You are the Commodore 64 expert inside a C64 console app. Answer the user's question about
the Commodore 64 (games, music, hardware, history, how to play something) accurately and concisely.
{sources_rule}
Respond with ONE JSON object only:
{{"answer": "<your answer in plain text; short paragraphs or '- ' bullet lines; **bold** allowed{cite}>",
  "games": ["<exact title of every C64 game you recommend or discuss, in order>"],
  "sources": [<numbers of the search results you used>]}}
Keep "answer" under 250 words. Only list real C64 games in "games" (max 10), using each game's full official
title exactly as released (never abbreviations or nicknames — "Quod Init Exit IIo", not "QIE2o")."""

WITH_SOURCES = """Use the numbered web search results below as your main source and cite them like [1] [2].
Only name games and facts that appear in the search results — never invent titles, sequels or details.
If the results do not answer the question, say so plainly and answer briefly from your own knowledge."""
NO_SOURCES = "No web search results are available: answer from your own knowledge."


class AskError(Exception):
    pass


async def brave_search(query: str, key: str, count: int = 8, timeout: float = 10) -> list[dict[str, str]]:
    """Brave Web Search → [{title, url, description}]."""
    headers = {"Accept": "application/json", "X-Subscription-Token": key}
    params = {"q": query, "count": count, "safesearch": "moderate", "text_decorations": "false"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            r = await client.get(BRAVE_URL, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise AskError(f"Brave Search unreachable: {type(exc).__name__}") from exc
    if r.status_code in (401, 403):
        raise AskError("Brave Search rejected the API key")
    if r.status_code == 429:
        raise AskError("Brave Search rate limit reached — try again in a moment")
    if r.status_code != 200:
        raise AskError(f"Brave Search HTTP {r.status_code}")
    results = (r.json().get("web") or {}).get("results") or []
    out = []
    for item in results[:count]:
        desc = re.sub(r"<[^>]+>", "", item.get("description") or "")
        extra = " ".join(re.sub(r"<[^>]+>", "", x) for x in (item.get("extra_snippets") or [])[:2])
        out.append({"title": item.get("title") or "", "url": item.get("url") or "",
                    "description": (desc + " " + extra).strip()[:600]})
    return out


class AskService:
    def __init__(self, settings_provider, provider_getter, session_factory, catalog=None):  # noqa: ANN001
        self._settings = settings_provider
        self._provider = provider_getter
        self.sf = session_factory
        self.catalog = catalog

    @property
    def settings(self) -> Settings:
        return self._settings()

    @property
    def provider(self) -> AIProvider:
        return self._provider()

    @property
    def web_search_on(self) -> bool:
        s = self.settings
        return bool(s.BRAVE_API_KEY) and s.ASK_WEB_SEARCH

    async def ask(self, question: str) -> dict[str, Any]:
        question = question.strip()[:300]
        if not question:
            raise AskError("Ask a question")
        if not self.provider.configured:
            raise AskError("Ask needs an AI model — set one up under Settings → AI assistant")
        results: list[dict[str, str]] = []
        search_note = None
        if self.web_search_on:
            query = question if re.search(r"\b(c64|commodore)\b", question, re.I) else f"Commodore 64 {question}"
            try:
                results = await brave_search(query, self.settings.BRAVE_API_KEY)
            except AskError as exc:
                search_note = str(exc)  # still answer, from the model's own knowledge
                log.warning("web search failed: %s", exc)
        numbered = "\n".join(f"[{n}] {r['title']} — {r['url']}\n{r['description']}" for n, r in enumerate(results, 1))
        system = SYSTEM.format(sources_rule=WITH_SOURCES if results else NO_SOURCES,
                               cite=", cite sources as [n]" if results else "")
        user = f"Question: {question}" + (f"\n\nWeb search results:\n{numbered}" if results else "")
        try:
            raw = await self.provider.complete(system, user, max_tokens=self.settings.ASK_MAX_TOKENS)
        except AIProviderError as exc:
            raise AskError(f"The AI model did not answer: {exc}") from exc
        try:
            data = extract_json(raw)
        except (ValueError, AIProviderError):
            data = {"answer": raw.strip(), "games": [], "sources": []}  # model ignored the format: keep its text
        answer = str(data.get("answer") or "").strip() or "The AI model returned no answer."
        titles = [str(t).strip() for t in (data.get("games") or []) if str(t).strip()][:MAX_GAMES]
        used = {int(n) for n in (data.get("sources") or []) if str(n).isdigit()}
        cited = {int(n) for n in re.findall(r"\[(\d{1,2})\]", answer)}
        sources = [{"n": n, "title": r["title"], "url": r["url"]} for n, r in enumerate(results, 1) if n in (used | cited)]
        games = await self._match_games(titles)
        await self._find_elsewhere(games)
        return {"question": question, "answer": answer, "games": games, "sources": sources,
                "webSearch": bool(results), "searchNote": search_note,
                "model": getattr(self.provider, "model", None)}

    async def _find_elsewhere(self, games: list[dict[str, Any]]) -> None:
        """Games in neither the library nor Assembly64: look in the online archives (Internet Archive, C64.com,
        Games That Weren't — no key needed), and on itch.io, CSDb and Lemon64 (web search)."""
        from .imports import find_elsewhere
        from .sources import archive_matches
        todo = [g for g in games if not g["gameId"] and not g["catalog"]][:5]
        found = await asyncio.gather(*(archive_matches(g["title"]) for g in todo), return_exceptions=True)
        for g, hits in zip(todo, found, strict=True):
            g["elsewhere"] = [] if isinstance(hits, BaseException) else hits
        if not self.web_search_on:
            return
        for n, g in enumerate(todo):
            if n:
                await asyncio.sleep(1.1)  # Brave's free plan allows one search per second
            try:
                web = await find_elsewhere(g["title"], self.settings.BRAVE_API_KEY, self.catalog)
                g["elsewhere"] = g.get("elsewhere", []) + web
            except Exception as exc:  # noqa: BLE001 - optional extra
                log.info("other-source lookup for %r failed: %s", g["title"], exc)
                continue
            csdb = next((o for o in g["elsewhere"] if o.get("catalog")), None)
            if csdb:
                g["catalog"] = csdb["catalog"]  # CSDb release mirrored by Assembly64: playable right away

    async def _match_games(self, titles: list[str]) -> list[dict[str, Any]]:
        """Each named game: in the library (gameId), else a playable Assembly64 entry (catalog item)."""
        out: list[dict[str, Any]] = []
        missing: list[int] = []
        with self.sf() as s:
            repo = LibraryRepository(s)
            for t in titles:
                entry: dict[str, Any] = {"title": t, "gameId": None, "catalog": None, "browserOk": True}
                best = [(g, sc) for g, sc in repo.find_best(t) if sc >= 0.9 and g.category != "music"]
                if best:
                    g = best[0][0]
                    entry.update(gameId=g.id, title=g.title, browserOk=browser_playable(g.format, g.category),
                                 coverUrl=g.cover_url)
                else:
                    missing.append(len(out))
                out.append(entry)
        if missing and self.catalog is not None and self.catalog.configured:
            async def lookup(i: int) -> None:
                title = out[i]["title"]
                # The catalog search trips over punctuation ("R-Type" finds nothing, "RType" does).
                spellings = list(dict.fromkeys([title, re.sub(r"[-:'’.!,]", "", title)]))
                for spelling in spellings:
                    try:
                        found = await asyncio.wait_for(self.catalog.find_for_play(spelling, "games"), 12)
                    except Exception as exc:  # noqa: BLE001 - catalog is optional here
                        log.info("catalog lookup for %r failed: %s", spelling, exc)
                        return
                    exact = [r for r in found if title_key(r["name"]) == title_key(title)]
                    if exact:
                        out[i]["catalog"] = exact[0]
                        return
            await asyncio.gather(*(lookup(i) for i in missing))
        return out
