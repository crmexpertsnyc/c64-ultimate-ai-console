"""✨ The daily C64 moment on Home: a game from your library, a classic SID tune, a magazine from this month in
history, and a BBS to call. The same picks all day (seeded by the date), different tomorrow. Nothing is fetched
from the internet — it's stitched together from what the console already has.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select

# famous tunes (title as in HVSC, composer) — opened as a Jukebox search, so the right HVSC entry is picked there
CLASSIC_TUNES = [
    ("Commando", "Rob Hubbard"), ("Monty on the Run", "Rob Hubbard"), ("Sanxion", "Rob Hubbard"),
    ("Delta", "Rob Hubbard"), ("Lightforce", "Rob Hubbard"), ("International Karate", "Rob Hubbard"),
    ("Thing on a Spring", "Rob Hubbard"), ("Crazy Comets", "Rob Hubbard"), ("Spellbound", "Rob Hubbard"),
    ("Warhawk", "Rob Hubbard"), ("Wizball", "Martin Galway"), ("Parallax", "Martin Galway"),
    ("Times of Lore", "Martin Galway"), ("Arkanoid", "Martin Galway"), ("Rambo First Blood Part II", "Martin Galway"),
    ("Cybernoid II", "Jeroen Tel"), ("Myth", "Jeroen Tel"), ("Supremacy", "Jeroen Tel"),
    ("Turrican", "Chris Hülsbeck"), ("Great Giana Sisters", "Chris Hülsbeck"), ("Ghosts'n Goblins", "Mark Cooksey"),
    ("The Last Ninja", "Ben Daglish"), ("Deflektor", "Ben Daglish"), ("Ocean Loader 3", "Martin Galway"),
]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def _pick(rng: random.Random, items: list[Any]) -> Any:
    return rng.choice(items) if items else None


def daily_moment(c, today: date | None = None) -> dict[str, Any]:  # noqa: ANN001, C901
    today = today or datetime.now().date()
    seed = today.toordinal()
    out: dict[str, Any] = {"date": today.isoformat()}

    def rng(part: str) -> random.Random:              # one stream per section: adding games doesn't reshuffle tunes
        return random.Random(f"{seed}:{part}")

    # 🎮 a game from your library (not utilities like the terminal program)
    try:
        from app.models.db import Game
        with c.sf() as s:
            games = s.execute(select(Game.id, Game.title, Game.cover_url, Game.year, Game.publisher)
                              .order_by(Game.id)).all()
        games = [g for g in games if "ccgms" not in (g.title or "").lower() and "terminal" not in (g.title or "").lower()]
        g = _pick(rng("game"), games)
        if g:
            review = None
            mags = getattr(c, "magazines", None)
            if mags is not None:
                rv = (mags.get_reviews(g.id) or {}).get("reviews") or []
                best = next((r for r in rv if r.get("score")), None)
                if best:
                    review = {"magazine": best.get("magazine"), "score": best.get("score"), "date": best.get("date"),
                              "coverUrl": best.get("coverUrl")}
            out["game"] = {"id": g.id, "title": g.title, "coverUrl": g.cover_url, "year": g.year,
                           "publisher": g.publisher, "review": review}
    except Exception:  # noqa: BLE001 - one section must not blank the card
        pass

    # 🎵 a classic SID tune
    title, composer = _pick(rng("tune"), CLASSIC_TUNES)
    out["tune"] = {"title": title, "composer": composer}

    # 📚 this month in history: an issue from the same month, decades ago
    try:
        from app.models.magazines import MagazineIssue
        month = f"-{today.month:02d}"
        with c.sf() as s:
            issues = s.execute(select(MagazineIssue.key, MagazineIssue.series, MagazineIssue.identifier,
                                      MagazineIssue.stem, MagazineIssue.title, MagazineIssue.date)
                               .where(MagazineIssue.date.like(f"%{month}%")).order_by(MagazineIssue.key)).all()
        issues = [i for i in issues if i.date and i.date[4:7] == month]
        i = _pick(rng("magazine"), issues)
        if i:
            from app.services.magazines import cover_url, reader_url
            year = int(i.date[:4])
            out["magazine"] = {"key": i.key, "title": i.title, "series": i.series, "date": i.date,
                               "yearsAgo": today.year - year, "month": MONTHS[today.month - 1], "year": year,
                               "coverUrl": cover_url(i.key), "readerUrl": reader_url(i.identifier, i.stem)}
    except Exception:  # noqa: BLE001
        pass

    # 📟 a BBS to call: approved, and it answered in the last month (boards with a picture first)
    try:
        bbs = getattr(c, "bbs", None)
        if bbs is not None:
            from app.models.bbs import BbsBoard
            since = datetime.now(UTC) - timedelta(days=30)
            with c.sf() as s:
                rows = s.scalars(select(BbsBoard).where(BbsBoard.review == "approved", BbsBoard.last_ok_at >= since)
                                 .order_by(BbsBoard.id)).all()
                boards = [(b.id, b.name, b.description, b.petscii, b.host, b.port) for b in rows]
            b = _pick(rng("bbs"), boards)
            if b:
                f = bbs.art_file(b[0])
                out["bbs"] = {"id": b[0], "name": b[1], "description": b[2], "petscii": b[3] != "unknown",
                              "address": f"{b[4]}:{b[5]}",
                              "thumbUrl": f"/api/bbs/art/{b[0]}?v={int(f.stat().st_mtime)}" if f else None}
    except Exception:  # noqa: BLE001
        pass
    return out
