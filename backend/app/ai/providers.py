"""Optional LLM providers. The app works fully without one (AI_PROVIDER=none).

* ``openai`` / ``vllm`` / ``openwebui`` — OpenAI-compatible ``POST {base}/chat/completions``
  (vLLM: ``http://spark:8000/v1``; OpenWebUI: ``http://host:3000/api``)
* ``ollama`` — ``POST {base}/api/chat`` with ``format: json``
* ``anthropic`` — Anthropic Messages API ``POST {base}/v1/messages``

Providers only ever return text; the engine extracts JSON and validates it into an
``Intent``. Nothing a provider returns is executed directly.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import Settings

log = logging.getLogger("c64.ai")


class AIProviderError(Exception):
    pass


class AIProvider:
    name = "none"

    def __init__(self, settings: Settings):
        self.base_url = settings.AI_BASE_URL.rstrip("/")
        self.model = settings.AI_MODEL
        self._key = settings.AI_API_KEY
        self.timeout = settings.AI_TIMEOUT
        self.max_tokens = settings.AI_MAX_TOKENS
        self.reasoning_effort = settings.AI_REASONING_EFFORT.strip().lower()

    @property
    def configured(self) -> bool:
        return bool(self.model)

    async def complete(self, system: str, user: str, max_tokens: int | None = None,
                       timeout: float | None = None) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    async def ping(self) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        local = bool(re.search(r"//(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|[^/.]+(:|/|$))",
                               self.base_url or ""))
        return {"provider": self.name, "model": self.model, "baseUrl": self.base_url, "configured": self.configured,
                "local": local}


class NoProvider(AIProvider):
    name = "none"

    @property
    def configured(self) -> bool:
        return False

    async def complete(self, system: str, user: str, max_tokens: int | None = None,
                       timeout: float | None = None) -> str:
        raise AIProviderError("no AI provider configured")

    async def ping(self) -> dict[str, Any]:
        return {"ok": False, "detail": "AI_PROVIDER=none"}


class OpenAICompatibleProvider(AIProvider):
    name = "openai"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"} if self._key else {}

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def complete(self, system: str, user: str, max_tokens: int | None = None,
                       timeout: float | None = None) -> str:
        payload = {"model": self.model, "temperature": 0, "max_tokens": max_tokens or self.max_tokens,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if self.reasoning_effort in ("low", "medium", "high"):
            payload["reasoning_effort"] = self.reasoning_effort
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            try:
                r = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
            except httpx.HTTPError as exc:
                raise AIProviderError(f"{self.name} unreachable: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise AIProviderError(f"{self.name} HTTP {r.status_code}: {r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise AIProviderError("unexpected response shape") from exc

    async def ping(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                r = await client.get(f"{self.base_url}/models", headers=self._headers())
            except httpx.HTTPError as exc:
                return {"ok": False, "detail": f"unreachable: {type(exc).__name__}"}
        if r.status_code != 200:
            return {"ok": False, "detail": f"HTTP {r.status_code}"}
        try:
            models = [m.get("id") for m in r.json().get("data", [])]
        except ValueError:
            models = []
        return {"ok": True, "models": models[:50], "modelAvailable": self.model in models if models else None}


class VLLMProvider(OpenAICompatibleProvider):
    name = "vllm"


class OpenWebUIProvider(OpenAICompatibleProvider):
    name = "openwebui"


class OllamaProvider(AIProvider):
    name = "ollama"

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def complete(self, system: str, user: str, max_tokens: int | None = None,
                       timeout: float | None = None) -> str:
        payload = {"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            try:
                r = await client.post(f"{self.base_url}/api/chat", json=payload)
            except httpx.HTTPError as exc:
                raise AIProviderError(f"ollama unreachable: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise AIProviderError(f"ollama HTTP {r.status_code}: {r.text[:200]}")
        return (r.json().get("message") or {}).get("content", "")

    async def ping(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                r = await client.get(f"{self.base_url}/api/tags")
            except httpx.HTTPError as exc:
                return {"ok": False, "detail": f"unreachable: {type(exc).__name__}"}
        models = [m.get("name") for m in r.json().get("models", [])] if r.status_code == 200 else []
        return {"ok": r.status_code == 200, "models": models, "modelAvailable": self.model in models}


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.base_url = self.base_url or "https://api.anthropic.com"

    @property
    def configured(self) -> bool:
        return bool(self.model and self._key)

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    async def complete(self, system: str, user: str, max_tokens: int | None = None,
                       timeout: float | None = None) -> str:
        payload = {"model": self.model, "max_tokens": max_tokens or self.max_tokens, "system": system,
                   "messages": [{"role": "user", "content": user}]}
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            try:
                r = await client.post(f"{self.base_url}/v1/messages", json=payload, headers=self._headers())
            except httpx.HTTPError as exc:
                raise AIProviderError(f"anthropic unreachable: {type(exc).__name__}") from exc
        if r.status_code != 200:
            raise AIProviderError(f"anthropic HTTP {r.status_code}: {r.text[:200]}")
        blocks = r.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    async def ping(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            try:
                r = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
            except httpx.HTTPError as exc:
                return {"ok": False, "detail": f"unreachable: {type(exc).__name__}"}
        return {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code}"}


PROVIDERS: dict[str, type[AIProvider]] = {
    "none": NoProvider, "openai": OpenAICompatibleProvider, "vllm": VLLMProvider,
    "openwebui": OpenWebUIProvider, "ollama": OllamaProvider, "anthropic": AnthropicProvider,
}


def make_provider(settings: Settings) -> AIProvider:
    return PROVIDERS.get(settings.AI_PROVIDER, NoProvider)(settings)


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AIProviderError("model did not return JSON")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise AIProviderError("model returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise AIProviderError("model JSON is not an object")
    return data
