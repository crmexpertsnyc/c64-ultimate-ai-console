"""Command interpretation: rules first, optional LLM second, always validated.

    text → rule parser ──(UNKNOWN)──→ LLM → JSON → Intent.model_validate → router
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from .intents import INTENT_HELP, Intent, IntentType
from .parser import parse_command
from .providers import AIProvider, AIProviderError, extract_json

log = logging.getLogger("c64.ai.engine")

_FIELDS_DOC = """Fields (omit when not needed):
  intent: one of {intents}
  game: title for PLAY_GAME / SEARCH_GAME / PLAY_SID / PLAY_MOD (string, the title only)
  use_selected: true when the user says "this"/"it" meaning the currently selected game
  disk: disk number for MOUNT_DISK (integer 1-20)
  key: for PRESS_KEY — one of return, space, run_stop, restore, f1..f8, home, clr, del, inst,
       commodore, ctrl, shift, cursor_up, cursor_down, cursor_left, cursor_right, or a single letter/digit
  text: text for TYPE_TEXT (max 200 chars); press_return: true if the user asked to press return/run it.
        For ASK: the user's whole question (questions, research, recommendations, "tell me about …")
  joystick: list from [up, down, left, right, fire, fire2, fire3] for JOYSTICK_INPUT
  port: 1 or 2 (joystick port)
  transition: tap (default), press (hold), release
  menu_action: for MENU_NAVIGATE — up, down, left, right, return, back, exit, home, page_up, page_down
  confidence: 0..1
  reason: short explanation"""

SYSTEM_PROMPT = """You translate commands for a Commodore 64 Ultimate console into ONE JSON object.
Respond with JSON only — no prose. You cannot call URLs, write memory or run code; you only
choose an intent from the list. If the request is unsafe, unclear or not in the list, return
{{"intent": "UNKNOWN", "reason": "..."}}.

{fields}

Examples:
{examples}
"""


def _system_prompt() -> str:
    intents = ", ".join(i.value for i in IntentType)
    examples = "\n".join(f'  "{ex}" → intent {it.value}' for it, ex in INTENT_HELP.items())
    return SYSTEM_PROMPT.format(fields=_FIELDS_DOC.format(intents=intents), examples=examples)


class CommandEngine:
    def __init__(self, provider: AIProvider):
        self.provider = provider

    async def interpret(self, text: str, context: dict[str, Any] | None = None,
                        use_ai: bool = True) -> tuple[Intent, dict[str, Any]]:
        meta: dict[str, Any] = {"rules": None, "llm": None}
        intent = parse_command(text, context)
        meta["rules"] = intent.intent.value
        if intent.intent != IntentType.UNKNOWN or not use_ai or not self.provider.configured:
            if intent.intent == IntentType.UNKNOWN and not self.provider.configured:
                meta["note"] = "no rule matched and no AI provider is configured"
            return intent, meta
        try:
            ctx = {k: v for k, v in (context or {}).items() if k in ("menuOpen", "currentGame", "selectedGame")}
            user = f"Context: {json.dumps(ctx)}\nCommand: {text}" if ctx else f"Command: {text}"
            raw = await self.provider.complete(_system_prompt(), user)
            data = extract_json(raw)
            data["source"] = "llm"
            data.setdefault("confidence", 0.7)
            llm_intent = Intent.model_validate(data)
            meta["llm"] = {"provider": self.provider.name, "model": self.provider.model,
                           "intent": llm_intent.intent.value}
            return llm_intent, meta
        except (AIProviderError, ValidationError, ValueError) as exc:
            meta["llm"] = {"provider": self.provider.name, "error": str(exc)[:300]}
            log.info("LLM interpretation rejected: %s", exc)
            return intent, meta

    async def identify_title(self, description: str, kind: str = "games") -> dict[str, str] | None:
        """Turn a description ('the karate game with the yellow jumpsuit') into a real C64 title
        ('Bruce Lee'). The answer is only used as a catalog search term, never executed."""
        if not self.provider.configured or not description.strip():
            return None
        what = "SID music tune" if kind == "music" else "game or program"
        system = (f"You are an expert on Commodore 64 software. Name the single C64 {what} that best matches the "
                  "user's words. If the user already gave an exact title, return it unchanged. If they only name a "
                  "composer, pick that composer's best-known C64 tune. Reply with JSON only: "
                  '{"title": "<official title or empty if unsure>", "author": "<composer for music, else empty>", '
                  '"confidence": <0..1>}')
        try:
            data = extract_json(await self.provider.complete(system, f"Description: {description[:200]}"))
        except AIProviderError as exc:
            log.info("title identification failed: %s", exc)
            return None
        title = str(data.get("title") or "").strip()
        author = str(data.get("author") or "").strip()
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        if not title or len(title) > 80 or confidence < 0.3 or not any(c.isalnum() for c in title):
            return None
        return {"title": title, "author": author[:60], "confidence": f"{confidence:.2f}"}

    async def suggest_title(self, query: str, candidates: list[str]) -> str | None:
        """Ask the LLM to pick the best library title for a vague query (optional)."""
        if not self.provider.configured or not candidates:
            return None
        system = ("Pick the single best matching title from the list for the user's description. "
                  'Reply with JSON {"title": "<exact title from list or empty>"}.')
        user = f"Description: {query}\nTitles: {json.dumps(candidates[:300])}"
        try:
            data = extract_json(await self.provider.complete(system, user))
        except AIProviderError:
            return None
        title = str(data.get("title") or "")
        return title if title in candidates else None
