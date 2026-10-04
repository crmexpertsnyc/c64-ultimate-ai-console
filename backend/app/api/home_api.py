"""◉ Home hub: one call for the front page — this week in C64, events near you, achievements, new hardware."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from app.container import Container

from .deps import get_container

router = APIRouter(tags=["home"])


def _safe(fn, default):  # noqa: ANN001, ANN202 - one failing section must not blank the page
    try:
        return fn()
    except Exception:  # noqa: BLE001
        return default


@router.get("/api/home", summary="The Home page hub: this week, events near you, achievements, new hardware")
async def home(c: Container = Depends(get_container)) -> dict[str, Any]:
    week = datetime.now(UTC) - timedelta(days=7)
    news = c.news

    def this_week() -> dict[str, Any]:
        releases = news.list("release", since=week, limit=4, sort="popular")["items"]
        if len(releases) < 4:                                   # not enough rated yet: newest fill in
            seen = {r["id"] for r in releases}
            releases += [r for r in news.list("release", limit=8)["items"] if r["id"] not in seen][:4 - len(releases)]
        return {"digest": news.digest, "releases": releases,
                "news": news.list("news", limit=3)["items"],
                "videos": news.list("video", since=datetime.now(UTC) - timedelta(days=14), limit=3, sort="popular")["items"]}

    def events() -> dict[str, Any]:
        svc = getattr(c, "events", None)
        if svc is None:
            return {"items": [], "live": [], "homeCountry": None}
        today = date.today()
        home_list = svc.list("home", date_from=today, date_to=today + timedelta(days=120))
        items = home_list["items"][:4]
        if not items:                                          # nothing at home soon: the next ones anywhere
            items = svc.list(None, date_from=today, date_to=today + timedelta(days=60))["items"][:4]
        return {"items": items, "live": home_list["live"][:2], "homeCountry": home_list["homeCountry"]}

    def hardware() -> dict[str, Any]:
        shop = getattr(c, "shop", None)
        return {"products": news.list("product", limit=4)["items"], "deals": shop.deals(limit=3) if shop else []}

    return {"thisWeek": _safe(this_week, {"digest": None, "releases": [], "news": [], "videos": []}),
            "events": _safe(events, {"items": [], "live": [], "homeCountry": None}),
            "achievements": _safe(lambda: c.achievements.recent(limit=5), []),
            "hardware": _safe(hardware, {"products": [], "deals": []})}


@router.get("/api/home/moment", summary="✨ Today's C64 moment: a game, a SID tune, a magazine from this month in history, a BBS")
async def moment(c: Container = Depends(get_container)) -> dict[str, Any]:
    from app.services.moment import daily_moment
    return daily_moment(c)
