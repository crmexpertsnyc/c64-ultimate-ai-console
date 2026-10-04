"""Library queries, fuzzy title matching and serialisation."""

from __future__ import annotations

import difflib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.db import Game, LibraryRoot, Media

from .scanner import normalize_key

EDITABLE_GAME_FIELDS = {
    "title", "alternate_names", "publisher", "year", "genre", "category", "joystick_port", "players",
    "preferred_launch", "load_command", "run_after_load", "reset_before_load", "startup_delay",
    "load_timeout", "needs_fire", "notes", "tags", "cover_url", "favorite",
}
LAUNCH_METHODS = {"auto", "run_prg", "load_prg", "run_crt", "mount_and_load", "mount_only", "dma_first_prg",
                  "t64_extract", "sid", "mod"}


def media_to_dict(m: Media) -> dict[str, Any]:
    return {"id": m.id, "path": m.path, "storage": m.storage, "format": m.format, "diskNumber": m.disk_number,
            "label": m.label, "size": m.size, "missing": m.missing, "info": m.info or {}}


def game_to_dict(g: Game, include_media: bool = True) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": g.id, "title": g.title, "alternateNames": g.alternate_names or [], "publisher": g.publisher,
        "year": g.year, "genre": g.genre, "category": g.category, "format": g.format, "numDisks": g.num_disks,
        "joystickPort": g.joystick_port, "players": g.players, "preferredLaunch": g.preferred_launch,
        "loadCommand": g.load_command, "runAfterLoad": g.run_after_load, "resetBeforeLoad": g.reset_before_load,
        "startupDelay": g.startup_delay, "loadTimeout": g.load_timeout, "needsFire": g.needs_fire,
        "notes": g.notes, "tags": g.tags or [], "coverUrl": g.cover_url, "favorite": g.favorite,
        "lastPlayed": g.last_played.isoformat() if g.last_played else None, "playCount": g.play_count,
        "sourceRoot": g.source_root,
        "details": (g.extra or {}).get("details"),  # ✨ Fill in details: description, style tags, sources
    }
    if include_media:
        d["media"] = [media_to_dict(m) for m in sorted(g.media, key=lambda m: (m.disk_number, m.path))]
    return d


_API_TO_FIELD = {
    "title": "title", "alternateNames": "alternate_names", "publisher": "publisher", "year": "year",
    "genre": "genre", "category": "category", "joystickPort": "joystick_port", "players": "players",
    "preferredLaunch": "preferred_launch", "loadCommand": "load_command", "runAfterLoad": "run_after_load",
    "resetBeforeLoad": "reset_before_load", "startupDelay": "startup_delay", "loadTimeout": "load_timeout",
    "needsFire": "needs_fire", "notes": "notes", "tags": "tags", "coverUrl": "cover_url", "favorite": "favorite",
}


class LibraryRepository:
    def __init__(self, session: Session):
        self.s = session

    def get(self, game_id: int) -> Game | None:
        return self.s.get(Game, game_id, options=[selectinload(Game.media)])

    def search(self, q: str = "", *, favorites: bool = False, recent: bool = False, fmt: str | None = None,
               publisher: str | None = None, year: int | None = None, genre: str | None = None,
               category: str | None = None, multiplayer: bool | None = None, joystick_port: int | None = None,
               limit: int = 60, offset: int = 0) -> tuple[list[Game], int]:
        stmt = select(Game).options(selectinload(Game.media))
        if q:
            like = f"%{q.lower()}%"
            nk = normalize_key(q)
            conds = [func.lower(Game.title).like(like), func.lower(Game.publisher).like(like),
                     func.lower(Game.genre).like(like),
                     # ✨ details: style tags + description (SQLite JSON)
                     func.lower(func.coalesce(func.json_extract(Game.extra, "$.details"), "")).like(like)]
            if nk:
                conds.append(Game.normalized_title.like(f"%{nk}%"))
            stmt = stmt.where(or_(*conds))
        if favorites:
            stmt = stmt.where(Game.favorite.is_(True))
        if recent:
            stmt = stmt.where(Game.last_played.is_not(None))
        if fmt:
            stmt = stmt.where(Game.format == fmt)
        if publisher:
            stmt = stmt.where(func.lower(Game.publisher) == publisher.lower())
        if year:
            stmt = stmt.where(Game.year == year)
        if genre:
            stmt = stmt.where(func.lower(Game.genre) == genre.lower())
        if category:
            stmt = stmt.where(Game.category == category)
        if joystick_port:
            stmt = stmt.where(Game.joystick_port == joystick_port)
        if multiplayer is not None:
            multi = or_(Game.players.like("%2%"), Game.players.like("%3%"), Game.players.like("%4%"),
                        Game.players.like("%-%"))
            stmt = stmt.where(multi if multiplayer else or_(Game.players.is_(None), ~multi))
        total = self.s.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        order = [Game.last_played.desc()] if recent else [Game.title.asc()]
        games = list(self.s.scalars(stmt.order_by(*order).limit(limit).offset(offset)).unique())
        return games, total

    def find_best(self, query: str, category: str | None = None, limit: int = 5) -> list[tuple[Game, float]]:
        """Fuzzy match a spoken/typed title against titles and alternate names."""
        key = normalize_key(query)
        if not key:
            return []
        stmt = select(Game).options(selectinload(Game.media))
        if category:
            stmt = stmt.where(Game.category == category)
        scored: list[tuple[Game, float]] = []
        for g in self.s.scalars(stmt):
            names = [g.normalized_title] + [normalize_key(a) for a in (g.alternate_names or [])]
            best = 0.0
            for n in names:
                if not n:
                    continue
                if n == key:
                    score = 1.0
                elif n.startswith(key) or key.startswith(n):
                    score = 0.9 * min(len(n), len(key)) / max(len(n), len(key)) + 0.08
                elif key in n:
                    score = 0.75
                else:
                    score = difflib.SequenceMatcher(None, key, n).ratio() * 0.85
                best = max(best, score)
            if best >= 0.55:
                scored.append((g, best))
        scored.sort(key=lambda t: (-t[1], -(t[0].play_count or 0), t[0].title))
        return scored[:limit]

    def update(self, game: Game, changes: dict[str, Any]) -> Game:
        for api_key, value in changes.items():
            field = _API_TO_FIELD.get(api_key, api_key)
            if field not in EDITABLE_GAME_FIELDS:
                raise ValueError(f"field {api_key} is not editable")
            if field == "preferred_launch" and value not in LAUNCH_METHODS:
                raise ValueError(f"preferredLaunch must be one of {sorted(LAUNCH_METHODS)}")
            if field == "joystick_port" and value not in (None, 1, 2):
                raise ValueError("joystickPort must be 1, 2 or null")
            if field == "load_command" and value and (len(value) > 80 or "\n" in value):
                raise ValueError("loadCommand must be a single line of at most 80 characters")
            setattr(game, field, value)
            if field == "cover_url":  # a cover chosen by the user is never replaced automatically
                game.extra = {**(game.extra or {}), "coverSource": "user" if value else None}
            if field == "title":
                game.normalized_title = normalize_key(value)
        self.s.commit()
        return game

    def record_play(self, game: Game) -> None:
        game.play_count = (game.play_count or 0) + 1
        game.last_played = datetime.now(UTC)
        self.s.commit()

    def facets(self) -> dict[str, Any]:
        def distinct(col):  # noqa: ANN001
            return [v for v in self.s.scalars(select(col).distinct().where(col.is_not(None)).order_by(col)) if v != ""]
        return {"formats": distinct(Game.format), "publishers": distinct(Game.publisher), "years": distinct(Game.year),
                "genres": distinct(Game.genre), "categories": distinct(Game.category)}

    def stats(self) -> dict[str, Any]:
        return {
            "games": self.s.scalar(select(func.count(Game.id))) or 0,
            "media": self.s.scalar(select(func.count(Media.id))) or 0,
            "favorites": self.s.scalar(select(func.count(Game.id)).where(Game.favorite.is_(True))) or 0,
            "roots": [{"path": r.path, "lastScan": r.last_scan.isoformat() if r.last_scan else None,
                       "fileCount": r.file_count, "lastError": r.last_error}
                      for r in self.s.scalars(select(LibraryRoot))],
        }
