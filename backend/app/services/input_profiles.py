"""Per-game input profiles for the browser emulator: how to start a game and which joystick port it uses.

A profile is assembled from three layers, most reliable first:
  1. learned  — what worked before on this console (a startup key the player pressed right before the game
                moved on; the port they settled on). Stored on the title and tied to the SHA-256 of the file
                that was booted, so a different release (other crack, other trainer) does not inherit it.
  2. builtin  — a small hand-checked list (app/data/input_profiles.json), matched by file hash first and
                then by title.
  3. guide    — the researched "How to play" guide (start keys, controls, port), if one was made.
Runtime detection in the player (screen text, joystick-register reads) comes on top of this, in the browser.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from app.library.titles import title_key
from app.models.db import Game, Media

from .guide import START_KEYS

# Startup keys a profile may contain: the guide's keys, plus typing RUN (after a BASIC loader stops at
# READY.) and "any key".
PROFILE_KEYS = START_KEYS | {"RUN", "ANY", "FIRE+SPACE"}
BUILTIN_PATH = Path(__file__).resolve().parent.parent / "data" / "input_profiles.json"


def _load_builtin() -> list[dict[str, Any]]:
    try:
        return json.loads(BUILTIN_PATH.read_text(encoding="utf-8")).get("profiles", [])
    except (OSError, ValueError):
        return []


def boot_media(g: Game) -> Media | None:
    local = [m for m in g.media if m.storage == "local" and not m.missing]
    return sorted(local, key=lambda m: (m.disk_number, Path(m.path).name.lower()))[0] if local else None


def media_sha256(m: Media) -> str | None:
    """SHA-256 of a media file, cached in Media.info (keyed by size + mtime so a changed file is rehashed)."""
    info = dict(m.info or {})
    stamp = f"{m.size}:{int(m.mtime or 0)}"
    if info.get("sha256") and info.get("sha256Stamp") == stamp:
        return info["sha256"]
    try:
        digest = hashlib.sha256(Path(m.path).read_bytes()).hexdigest()
    except OSError:
        return None
    info.update(sha256=digest, sha256Stamp=stamp)
    m.info = info
    return digest


def clean_steps(steps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for st in (steps or [])[:8]:
        key = str(st.get("key", "")).strip().upper()
        if key not in PROFILE_KEYS:
            continue
        step: dict[str, Any] = {"key": key}
        if st.get("when"):
            step["when"] = str(st["when"])[:80]   # the prompt it answers, e.g. "PRESS RUN/STOP"
        out.append(step)
    return out


class InputProfiles:
    def __init__(self, session_factory):  # noqa: ANN001
        self.sf = session_factory
        self._builtin = _load_builtin()

    def _builtin_for(self, title: str, sha: str | None) -> dict[str, Any] | None:
        key = title_key(title)
        by_hash = [p for p in self._builtin if sha and sha in (p.get("sha256") or [])]
        if by_hash:
            return by_hash[0]
        # Title matches only carry what is true for every release (port, controls) — never start keys,
        # which depend on the crack / trainer in front of the game.
        for p in self._builtin:
            if key in {title_key(t) for t in [p.get("game", ""), *(p.get("aliases") or [])]}:
                return {k: v for k, v in p.items() if k != "startupSequence"}
        return None

    def get(self, game_id: int) -> dict[str, Any]:
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            m = boot_media(g)
            sha = media_sha256(m) if m else None
            s.commit()  # keeps a freshly computed hash
            extra = g.extra or {}
            learned = extra.get("inputProfile")
            if learned and learned.get("sha256") != sha:
                learned = None  # learned on another file (a different release): not applicable
            builtin = self._builtin_for(g.title, sha)
            guide = extra.get("guide") or {}
            title, meta_port = g.title, g.joystick_port

        startup, start_source = [], None
        for source, steps in (("learned", (learned or {}).get("startupSequence")),
                              ("builtin", (builtin or {}).get("startupSequence")),
                              ("guide", [{"key": k} for k in guide.get("startKeys") or []])):
            steps = clean_steps(steps)
            if steps:
                startup, start_source = steps, source
                break
        port, port_source = None, None
        for source, p in (("learned", (learned or {}).get("joystickPort")), ("builtin", (builtin or {}).get("joystickPort")),
                          ("guide", guide.get("joystickPort")), ("library", meta_port)):
            if p in (1, 2):
                port, port_source = p, source
                break
        controls = (builtin or {}).get("controls") or {c["action"]: c["how"] for c in guide.get("controls") or []}
        return {"game": title, "platform": "C64", "gameId": game_id, "sha256": sha,
                "startupSequence": startup, "startupSource": start_source,
                "joystickPort": port, "joystickPortSource": port_source, "controls": controls,
                "layers": {"learned": bool(learned), "builtin": bool(builtin), "guide": bool(guide)}}

    def learn(self, game_id: int, sha256: str | None, startup: list[dict[str, Any]] | None = None,
              joystick_port: int | None = None) -> dict[str, Any]:
        """Remember what worked. Only for the file that is actually booted (the hash must match)."""
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            m = boot_media(g)
            sha = media_sha256(m) if m else None
            if not sha or sha != sha256:
                raise ValueError("this profile was learned on a different file")
            cur = dict((g.extra or {}).get("inputProfile") or {})
            if cur.get("sha256") != sha:
                cur = {}
            cur["sha256"] = sha
            if startup is not None:
                cur["startupSequence"] = clean_steps(startup)
            if joystick_port in (1, 2):
                cur["joystickPort"] = joystick_port
            cur["updatedAt"] = time.time()
            g.extra = {**(g.extra or {}), "inputProfile": cur}
            s.commit()
        return self.get(game_id)

    def forget(self, game_id: int) -> None:
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            g.extra = {k: v for k, v in (g.extra or {}).items() if k != "inputProfile"}
            s.commit()
