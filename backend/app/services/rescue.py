"""🛟 When a game hangs in Browser Play: other versions of the same game that are likely to start.

Some releases never get past their loader in the emulator (e.g. a crack with a custom fast loader). Browser Play
notices a screen that stays frozen while loading and asks here for alternatives: other copies of the same title
in the library (an EasyFlash cartridge first), then one-file releases from the catalog (EasyFlash / OneLoad64 —
no custom disk loader). A hang is remembered on the game so the next visit can warn up front.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from sqlalchemy import select

from app.library.formats import browser_playable
from app.library.titles import title_key
from app.models.db import Game

from .catalog import is_one_file_release

log = logging.getLogger("c64.rescue")


def base_key(title: str) -> str:
    """"The Last Ninja (EasyFlash)" and "Last Ninja, The" → the same key."""
    t = re.sub(r"\s*[(\[].*?[)\]]", "", title)
    t = re.sub(r",\s*the$", "", t, flags=re.I)
    t = re.sub(r"^the\s+", "", t, flags=re.I)
    return title_key(t)


class RescueService:
    def __init__(self, session_factory, catalog):  # noqa: ANN001
        self.sf = session_factory
        self.catalog = catalog

    def report_hang(self, game_id: int) -> int:
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            hangs = dict((g.extra or {}).get("hangs") or {})
            hangs["count"] = int(hangs.get("count", 0)) + 1
            hangs["last"] = time.time()
            g.extra = {**(g.extra or {}), "hangs": hangs}
            s.commit()
            return hangs["count"]

    async def alternatives(self, game_id: int) -> dict[str, Any]:
        with self.sf() as s:
            g = s.get(Game, game_id)
            if not g:
                raise LookupError("game not found")
            key, title = base_key(g.title), g.title
            hangs = int(((g.extra or {}).get("hangs") or {}).get("count", 0))
            library = []
            for other in s.scalars(select(Game).where(Game.id != game_id)):
                if base_key(other.title) != key or not browser_playable(other.format, other.category):
                    continue
                cart = other.format == "crt"
                library.append({"kind": "library", "gameId": other.id, "title": other.title, "format": other.format,
                                "why": "EasyFlash cartridge in your library — starts instantly" if cart
                                else f"Another copy in your library ({other.format.upper()})",
                                "score": 0 if cart else 1,
                                "hangs": int(((other.extra or {}).get("hangs") or {}).get("count", 0))})
        library.sort(key=lambda x: (x["hangs"] > 0, x["score"]))
        online = []
        if self.catalog is not None and self.catalog.configured:
            name = re.sub(r"\s*[(\[].*?[)\]]", "", title).strip()
            try:
                found = await self.catalog.find_for_play(name, "games", target="browser")
            except Exception as exc:  # noqa: BLE001 - the catalog is optional here
                log.info("catalog alternatives for %s failed: %s", title, exc)
                found = []
            for r in found:
                if base_key(r.get("name") or "") != key or not is_one_file_release(r):
                    continue
                online.append({"kind": "catalog", "catalog": r, "title": r.get("name"),
                               "why": f"One-file release ({r.get('source')}) — no custom disk loader"})
                if len(online) >= 3:
                    break
        return {"gameId": game_id, "title": title, "hangs": hangs,
                "alternatives": [{k: v for k, v in x.items() if k != "score"} for x in library] + online}
