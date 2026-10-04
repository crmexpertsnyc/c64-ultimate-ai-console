"""👪 Family profiles: who is playing.

The browser sends the chosen profile with every request (header ``X-C64-Profile``, or ``?profile=`` where a
header can't be set). ``ProfileContext`` puts it in a context variable for the request, so taste signals,
ratings, recommendations, the weekly recap and Browser Play saves all belong to that person — including work
started by the request (a launch's after-hooks run with the same context). Unknown ids fall back to profile 1,
which always exists and owns everything made before profiles existed.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any

from sqlalchemy import delete, select

from app.models.db import Profile, Rating, TasteEvent

log = logging.getLogger("c64.profiles")

DEFAULT = 1
current_profile: contextvars.ContextVar[int] = contextvars.ContextVar("current_profile", default=DEFAULT)
EMOJIS = ["🙂", "😎", "🦸", "👧", "👦", "👩", "👨", "👵", "👴", "🐱", "🐶", "🦄", "🐉", "🤖", "👾", "🎮"]


def profile_id() -> int:
    return current_profile.get()


class ProfileService:
    def __init__(self, session_factory):  # noqa: ANN001
        self.sf = session_factory
        self._ids: set[int] = set()
        self.ensure_default()

    def ensure_default(self) -> None:
        with self.sf() as s:
            if not s.get(Profile, DEFAULT):
                s.add(Profile(id=DEFAULT, name="Player 1", emoji="🙂"))
                s.commit()
            self._ids = set(s.scalars(select(Profile.id)))

    def valid(self, pid: int) -> bool:
        return pid in self._ids

    def list(self) -> list[dict[str, Any]]:
        with self.sf() as s:
            return [_dict(p) for p in s.scalars(select(Profile).order_by(Profile.id))]

    def get(self, pid: int) -> dict[str, Any]:
        with self.sf() as s:
            p = s.get(Profile, pid)
            if not p:
                raise LookupError("profile not found")
            return _dict(p)

    def create(self, name: str, emoji: str | None = None, color: str | None = None, kids: bool = False) -> dict[str, Any]:
        name = name.strip()[:40]
        if not name:
            raise ValueError("a name is needed")
        with self.sf() as s:
            if s.scalars(select(Profile).where(Profile.name == name)).first():
                raise ValueError(f"there is already a profile called {name}")
            if len(self._ids) >= 12:
                raise ValueError("up to 12 profiles")
            p = Profile(name=name, emoji=(emoji or "🙂")[:8], color=_color(color), kids=kids)
            s.add(p)
            s.commit()
            self._ids.add(p.id)
            return _dict(p)

    def update(self, pid: int, **fields: Any) -> dict[str, Any]:
        with self.sf() as s:
            p = s.get(Profile, pid)
            if not p:
                raise LookupError("profile not found")
            if fields.get("name") is not None:
                name = str(fields["name"]).strip()[:40]
                if not name:
                    raise ValueError("a name is needed")
                p.name = name
            if fields.get("emoji") is not None:
                p.emoji = str(fields["emoji"])[:8] or "🙂"
            if fields.get("color") is not None:
                p.color = _color(fields["color"])
            if fields.get("kids") is not None:
                p.kids = bool(fields["kids"])
            s.commit()
            return _dict(p)

    def delete(self, pid: int) -> None:
        """Remove a profile and its taste history (its saves stay on disk in their own folder)."""
        if pid == DEFAULT:
            raise ValueError("the first profile can't be deleted")
        with self.sf() as s:
            p = s.get(Profile, pid)
            if not p:
                return
            s.execute(delete(TasteEvent).where(TasteEvent.profile_id == pid))
            s.execute(delete(Rating).where(Rating.profile_id == pid))
            s.delete(p)
            s.commit()
        self._ids.discard(pid)

    def kids(self, pid: int) -> bool:
        with self.sf() as s:
            p = s.get(Profile, pid)
            return bool(p and p.kids)


def _color(c: Any) -> str:
    c = str(c or "#7c70da")
    return c if len(c) in (4, 7) and c.startswith("#") and all(x in "0123456789abcdefABCDEF" for x in c[1:]) else "#7c70da"


def _dict(p: Profile) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "emoji": p.emoji, "color": p.color, "kids": p.kids}


class ProfileContext:
    """ASGI middleware: the request's profile (header X-C64-Profile or ?profile=) → ``current_profile``."""

    def __init__(self, app, service_provider):  # noqa: ANN001
        self.app = app
        self.service_provider = service_provider

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        raw = dict(scope.get("headers") or []).get(b"x-c64-profile", b"").decode("latin-1")
        if not raw:
            from urllib.parse import parse_qs
            raw = (parse_qs(scope.get("query_string", b"").decode("latin-1")).get("profile") or [""])[0]
        pid = DEFAULT
        if raw.isdigit():
            svc = self.service_provider()
            if svc is not None and svc.valid(int(raw)):
                pid = int(raw)
        token = current_profile.set(pid)
        try:
            return await self.app(scope, receive, send)
        finally:
            current_profile.reset(token)
