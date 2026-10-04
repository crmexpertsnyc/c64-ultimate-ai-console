"""One call shape for the AI features (details, coach, co-pilot, playlists, recap): optional Brave web search,
then the configured model answering with one JSON object. Uses the Ask service's provider and settings."""

from __future__ import annotations

import logging
from typing import Any

from app.ai.providers import AIProviderError, extract_json

from .ask import AskError, brave_search

log = logging.getLogger("c64.ai")


async def ask_json(ask, system: str, user: str, *, search: str | None = None, count: int = 5,  # noqa: ANN001
                   max_tokens: int | None = None, timeout: float | None = None,
                   what: str = "answer") -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Returns (reply, web results). Raises AskError with a user-friendly message."""
    provider = ask.provider
    if not provider.configured:
        raise AskError("This needs an AI model — set one up under Settings → AI assistant")
    results: list[dict[str, str]] = []
    if search and ask.web_search_on:
        try:
            results = await brave_search(search, ask.settings.BRAVE_API_KEY, count=count)
        except AskError as exc:
            log.info("web search for %s failed: %s", what, exc)
    if results:
        user += "\n\nNumbered web search results:\n" + "\n".join(
            f"[{n}] {r['title']}\n{r['description'][:300]}" for n, r in enumerate(results, 1))
    st = ask.settings
    raw = ""
    try:
        raw = await provider.complete(system, user, max_tokens=max_tokens or st.ASK_MAX_TOKENS,
                                      timeout=timeout or st.RECOMMEND_TIMEOUT)
        data = extract_json(raw)
    except (AIProviderError, ValueError) as exc:
        log.info("%s: model reply not usable (%d chars, ends %r)", what, len(raw or ""), (raw or "")[-160:])
        raise AskError(f"The AI model could not make the {what}: {exc}") from exc
    if not isinstance(data, dict):
        raise AskError(f"The AI model could not make the {what}: unexpected reply")
    return data, results


def cited(data: dict[str, Any], results: list[dict[str, str]]) -> list[dict[str, Any]]:
    """The web results the model says it used (its "sources": [numbers])."""
    used = {int(n) for n in (data.get("sources") or []) if str(n).isdigit()}
    return [{"n": n, "title": r["title"], "url": r["url"]} for n, r in enumerate(results, 1) if n in used]


def strs(v: Any, limit: int, length: int = 160) -> list[str]:
    return [str(x).strip()[:length] for x in (v or []) if str(x).strip()][:limit]
