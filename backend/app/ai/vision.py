"""AI vision control mode — EXPERIMENTAL, off by default.

Pipeline:
    video stream → FrameSampler → VisionPlanner (vision LLM) → validated action
                 → VisionGovernor (limits) → InputController → C64

Guarantees:
* Disabled unless VISION_ENABLED=true AND the user explicitly starts a session with a goal.
* Hard limits: actions/second, actions/session, session duration (all configurable, capped).
* Only a whitelist of tap actions (joystick directions/fire, RETURN, SPACE, F1-F7).
  No typing of arbitrary text, no memory access, no resets, no menu, no power.
* Any error, limit hit or stop request ends the session and sends release_all.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings

from .providers import AIProviderError, extract_json

log = logging.getLogger("c64.vision")

ALLOWED_KEYS = {"return", "space", "f1", "f3", "f5", "f7"}
ALLOWED_JOY = {"up", "down", "left", "right", "fire"}


class VisionAction(BaseModel):
    kind: Literal["joystick", "key", "wait", "done"]
    inputs: list[str] = Field(default_factory=list, max_length=2)
    key: str | None = None
    observation: str = Field("", max_length=300)

    def validate_whitelist(self) -> None:
        if self.kind == "joystick" and (not self.inputs or any(i not in ALLOWED_JOY for i in self.inputs)):
            raise ValueError(f"joystick inputs not allowed: {self.inputs}")
        if self.kind == "key" and self.key not in ALLOWED_KEYS:
            raise ValueError(f"key not allowed: {self.key}")


@dataclass
class VisionSession:
    goal: str
    port: int
    started_at: float = field(default_factory=time.time)
    actions: int = 0
    status: str = "running"  # running | done | stopped | limit | error
    log: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


class VisionGovernor:
    def __init__(self, max_per_second: float, max_actions: int, max_seconds: int):
        self.max_per_second = max(0.2, min(max_per_second, 5.0))
        self.max_actions = max(1, min(max_actions, 1000))
        self.max_seconds = max(5, min(max_seconds, 600))
        self._recent: deque[float] = deque()

    def check(self, session: VisionSession) -> str | None:
        if time.time() - session.started_at > self.max_seconds:
            return f"session time limit ({self.max_seconds}s) reached"
        if session.actions >= self.max_actions:
            return f"action limit ({self.max_actions}) reached"
        return None

    async def throttle(self) -> None:
        now = time.monotonic()
        while self._recent and now - self._recent[0] > 1.0:
            self._recent.popleft()
        if len(self._recent) >= self.max_per_second:
            await asyncio.sleep(1.0 - (now - self._recent[0]))
        interval = 1.0 / self.max_per_second
        if self._recent and time.monotonic() - self._recent[-1] < interval:
            await asyncio.sleep(interval - (time.monotonic() - self._recent[-1]))
        self._recent.append(time.monotonic())


PLANNER_PROMPT = """You are controlling a Commodore 64 through a joystick in port {port}.
Goal: {goal}
Look at the screen and choose ONE next action as JSON:
  {{"kind": "joystick", "inputs": ["fire"], "observation": "..."}}
  {{"kind": "key", "key": "return", "observation": "..."}}
  {{"kind": "wait", "observation": "..."}}
  {{"kind": "done", "observation": "goal reached"}}
Allowed joystick inputs: up, down, left, right, fire. Allowed keys: return, space, f1, f3, f5, f7.
Reply with JSON only."""


class VisionPlanner:
    """Sends a frame to a vision-capable model (OpenAI-compatible or Anthropic)."""

    def __init__(self, settings: Settings):
        self.provider = settings.AI_PROVIDER
        self.base_url = settings.AI_BASE_URL.rstrip("/")
        self.model = settings.AI_MODEL
        self._key = settings.AI_API_KEY
        self.timeout = settings.AI_TIMEOUT

    @property
    def supported(self) -> bool:
        return self.provider in ("openai", "vllm", "openwebui", "anthropic") and bool(self.model)

    async def plan(self, jpeg: bytes, goal: str, port: int) -> VisionAction:
        prompt = PLANNER_PROMPT.format(goal=goal, port=port)
        b64 = base64.b64encode(jpeg).decode()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if self.provider == "anthropic":
                r = await client.post(
                    f"{self.base_url or 'https://api.anthropic.com'}/v1/messages",
                    headers={"x-api-key": self._key, "anthropic-version": "2023-06-01"},
                    json={"model": self.model, "max_tokens": 200, "messages": [{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                        {"type": "text", "text": prompt}]}]})
                text = "".join(b.get("text", "") for b in r.json().get("content", [])) if r.status_code == 200 else ""
            else:
                headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
                r = await client.post(f"{self.base_url}/chat/completions", headers=headers, json={
                    "model": self.model, "temperature": 0, "max_tokens": 200, "messages": [{"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]})
                text = r.json()["choices"][0]["message"]["content"] if r.status_code == 200 else ""
        if r.status_code != 200:
            raise AIProviderError(f"vision model HTTP {r.status_code}")
        action = VisionAction.model_validate(extract_json(text))
        action.validate_whitelist()
        return action


class VisionController:
    def __init__(self, settings_provider, device, hub):  # noqa: ANN001
        self._settings = settings_provider
        self.device = device
        self.hub = hub
        self.session: VisionSession | None = None
        self._task: asyncio.Task | None = None

    def status(self) -> dict[str, Any]:
        s: Settings = self._settings()
        planner = VisionPlanner(s)
        sess = self.session
        return {
            "enabled": s.VISION_ENABLED,
            "plannerSupported": planner.supported,
            "limits": {"maxActionsPerSecond": s.VISION_MAX_ACTIONS_PER_SECOND,
                       "maxSessionSeconds": s.VISION_MAX_SESSION_SECONDS,
                       "maxActionsPerSession": s.VISION_MAX_ACTIONS_PER_SESSION},
            "session": None if sess is None else {"goal": sess.goal, "status": sess.status, "actions": sess.actions,
                                                  "startedAt": sess.started_at, "error": sess.error,
                                                  "log": sess.log[-20:]},
        }

    async def start(self, goal: str, port: int = 2) -> dict[str, Any]:
        s: Settings = self._settings()
        if not s.VISION_ENABLED:
            raise PermissionError("vision mode is disabled (set VISION_ENABLED=true to allow it)")
        planner = VisionPlanner(s)
        if not planner.supported:
            raise PermissionError("vision mode needs a vision-capable OpenAI-compatible or Anthropic model")
        if not self.device.caps.supported("directJoystick"):
            raise PermissionError("vision mode needs REST joystick input")
        if self._task and not self._task.done():
            raise PermissionError("a vision session is already running")
        if not goal.strip() or len(goal) > 200:
            raise ValueError("goal must be 1..200 characters")
        self.session = VisionSession(goal=goal.strip(), port=port)
        gov = VisionGovernor(s.VISION_MAX_ACTIONS_PER_SECOND, s.VISION_MAX_ACTIONS_PER_SESSION,
                             s.VISION_MAX_SESSION_SECONDS)
        self._task = asyncio.create_task(self._loop(self.session, planner, gov), name="vision")
        return self.status()

    async def stop(self, reason: str = "stopped by user") -> dict[str, Any]:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        if self.session and self.session.status == "running":
            self.session.status, self.session.error = "stopped", reason
        await self.device.release_all_inputs(reason="vision stop")
        return self.status()

    async def _frame(self) -> bytes | None:
        dev = self.device
        if dev.simulator is not None:
            from app.ultimate.streams import render_text_frame
            return render_text_frame(dev.simulator.lines)
        return dev.streams.latest_jpeg()

    async def _loop(self, sess: VisionSession, planner: VisionPlanner, gov: VisionGovernor) -> None:
        inputs = self.device.inputs
        try:
            while True:
                limit = gov.check(sess)
                if limit:
                    sess.status, sess.error = "limit", limit
                    break
                frame = await self._frame()
                if frame is None:
                    raise RuntimeError("no video frame available (start the video stream first)")
                try:
                    action = await planner.plan(frame, sess.goal, sess.port)
                except (ValidationError, ValueError) as exc:
                    sess.log.append({"t": time.time(), "rejected": str(exc)[:200]})
                    await asyncio.sleep(1.0)
                    continue
                sess.log.append({"t": time.time(), "action": action.model_dump()})
                self.hub.publish("vision", self.status())
                if action.kind == "done":
                    sess.status = "done"
                    break
                await gov.throttle()
                if action.kind == "joystick":
                    await inputs.tap_joystick(action.inputs, sess.port)
                elif action.kind == "key":
                    await inputs.tap_key(action.key or "return")
                else:
                    await asyncio.sleep(0.5)
                    continue
                sess.actions += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            sess.status, sess.error = "error", str(exc)
        finally:
            await self.device.release_all_inputs(reason="vision end")
            self.hub.publish("vision", self.status())
