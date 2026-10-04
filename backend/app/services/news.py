"""📰 C64 news, 🆕 new releases and 🎬 videos — monitored on an ongoing basis.

Feeds (all public RSS / Atom, read-only):

* CSDb latest releases — every new scene release (games, demos, music, graphics, tools) with its type, group,
  screenshot and download. Releases whose file is hosted on CSDb can be added to the library and played
  (💻 in the browser, 📺 on the C64) with one click — the download goes through ``ImportService.import_url``.
* itch.io newest games tagged Commodore 64 — homebrew; played / bought on itch.io (new tab).
* Indie Retro News (C64 label) — news articles.
* YouTube channels about the C64 — videos, watched right in the app (privacy-enhanced embed).

A background loop checks every ``INTERVAL`` seconds; new items are announced on the event hub ("news").
Nothing here downloads a game by itself: playing a release is always the user's click.
"""

from __future__ import annotations

import asyncio
import bisect
import html
import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from sqlalchemy import func, select

from app.models.db import NewsItem

from .ai_json import ask_json, strs
from .ask import AskError
from .imports import DOWNLOAD_HOSTS, IMPORT_EXT

log = logging.getLogger("c64.news")

INTERVAL = 30 * 60          # seconds between checks
KEEP_DAYS = 120             # older items are pruned
KEEP_DAYS_VIDEO = 365       # videos stay worth watching longer (a channel's feed only lists its latest 15)
UA = {"User-Agent": "C64UltimateAIConsole/1.0 (+news monitor)"}

# Videos from general retro channels only count when they are about the C64.
C64_WORDS = re.compile(r"\b(c64|c-64|commodore\s*64|commodore|1541|sid\s*chip|sid\b|vic-?ii|c128|ultimate\s*64|"
                       r"c64u|thec64|kernal|cbm|breadbin|petscii)\b", re.I)

FEEDS: list[dict[str, Any]] = [
    {"source": "csdb", "label": "CSDb", "kind": "release", "url": "https://csdb.dk/rss/latestreleases.php"},
    {"source": "itch", "label": "itch.io", "kind": "release", "url": "https://itch.io/games/newest/tag-commodore-64.xml"},
    {"source": "indieretronews", "label": "Indie Retro News", "kind": "news",
     "url": "https://www.indieretronews.com/feeds/posts/default/-/C64?alt=rss"},
    {"source": "psytronik", "label": "Psytronik (itch.io)", "kind": "release",
     "url": "https://itch.io/games/by-psytronik.xml"},
    {"source": "protovision-itch", "label": "Protovision (itch.io)", "kind": "release",
     "url": "https://itch.io/games/by-protovision.xml"},
    {"source": "oasisbbs", "label": "The Oasis BBS", "kind": "news", "url": "https://theoasisbbs.com/feed/"},
    {"source": "commodore", "label": "Commodore", "kind": "news", "url": "https://commodore.net/feed/"},
    {"source": "freeze64", "label": "FREEZE64", "kind": "news", "url": "https://freeze64.com/feed/"},
    {"source": "protovision", "label": "Protovision", "kind": "news",
     "url": "https://www.protovision.games/wordpress/category/english/feed/"},
    {"source": "tpug", "label": "TPUG (Toronto PET Users Group)", "kind": "news", "url": "https://www.tpug.ca/feed/"},
    {"source": "icomp", "label": "Individual Computers", "kind": "news", "url": "https://shop.icomp.de/share/rss_icomp_english.xml"},
    {"source": "reddit-c64", "label": "Reddit r/c64", "kind": "news", "url": "https://www.reddit.com/r/c64/new/.rss"},
    {"source": "vcf", "label": "Vintage Computer Federation", "kind": "news", "url": "https://vcfed.org/feed/"},
    {"source": "demoparty-news", "label": "demoparty.net", "kind": "news", "url": "https://www.demoparty.net/news.xml"},
    # YouTube: "c64only" channels are about the C64 anyway; the others are filtered with C64_WORDS
    {"source": "youtube:commodore", "label": "Official Commodore", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCVtHSIwgtd_ahsmNwCP9W5g"},
    {"source": "youtube:retrorecipes", "label": "Retro Recipes", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC6gARF3ICgaLfs3o2znuqXA"},
    {"source": "youtube:8bitshowandtell", "label": "8-Bit Show And Tell", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC3gRBswFkuteshdwMZAQafQ"},
    {"source": "youtube:janbeta", "label": "Jan Beta", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCftUpOO4h9EgH0eDOZtjzcA"},
    {"source": "youtube:noelsretrolab", "label": "Noel's Retro Lab", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC2-SP1bYi3ueKlVU7I75wFw"},
    {"source": "youtube:retrohour", "label": "The Retro Hour", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCcdZfNsQapuw8T3_DBs1XkA"},
    {"source": "youtube:protovision", "label": "Protovision", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCwNO_68pmpNWApxVgHSOYCw"},  # new C64 games from the publisher
    {"source": "youtube:retrogamernation", "label": "RetroGamerNation", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCnhr67lnXf3ZtcKLrr9-hSA"},  # new C64 games and reviews
    {"source": "youtube:freeze64", "label": "FREEZE64", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCcpWrosk3yNRv2QkUeHvLcA"},  # the C64 fanzine
    {"source": "youtube:highlander", "label": "The Highlander C64 Gaming", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCbMVWZfNUnUKhCbxVdrDEXg"},  # C64 homebrew and games
    {"source": "youtube:retroc64gaming", "label": "Retro C64 Gaming", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCFv4sj8simOi3glFiky_ZmQ"},  # C64 games
    {"source": "youtube:c64television", "label": "C64 Television", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCaRNvUOXqeh0U7IJflK5tbg"},  # C64 news and shows
    {"source": "youtube:c64longplays", "label": "Commodore-64 longplays", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCyDGyz4JGt5Rs4DxutYDTjg"},  # complete C64 games played through
    {"source": "youtube:transmission64", "label": "Transmission64 Demoparty", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC_XBzOlpkWq5xUTZ9t1ELbQ"},  # C64 demoscene party
    {"source": "youtube:everythingsid", "label": "EverythingSid", "kind": "video", "c64only": True,
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCDbAWy2ArsTKso-A0sFv_hA"},  # SID music
    {"source": "youtube:shallan", "label": "Shallan", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCFjZzzJO_rXmr4FeBSf2rcQ"},  # C64 music, news and coding
    {"source": "youtube:chicken64", "label": "Chicken 64", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC_1jSEEoDypVnw2hbb-wfgw"},  # C64 games and hardware
    {"source": "youtube:commodorerealm", "label": "Commodore Realm", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCcPa7CxzflnO2JxGwCd-OyQ"},  # Commodore games
    {"source": "youtube:lft", "label": "Linus Åkesson (lft)", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC8ge7La_vq48PVEmR-DJ5Wg"},  # C64 hacking and music
    {"source": "youtube:8bitguy", "label": "The 8-Bit Guy", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC8uT9cgJorJPWu7ITLGo9Ww"},  # retro computers
    {"source": "youtube:chinnyvision", "label": "ChinnyVision", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCifcRlP9KST8T0irCHyykrA"},  # retro games
    {"source": "youtube:20thcentury", "label": "20th Century Gaming", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCjQ8c81v9x0vXrePJZnfPsA"},  # retro games
    {"source": "youtube:adrian", "label": "Adrian's Digital Basement", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCE5dIscvDxrb7CD5uiJJOiw"},  # retro repairs
    {"source": "youtube:monroeworld", "label": "MonroeWorld", "kind": "video",
     "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCEHlQAWyRwVOunhw6FyHKdA"},  # Commodore hardware
]
LABELS = {f["source"]: f["label"] for f in FEEDS}

# CSDb release type → category shown in the app ("C64 Crack" releases are cracked games)
_CSDB_CATEGORY = [
    ("game", re.compile(r"game|crack|easyflash|reu release|cartridge", re.I)),
    ("demo", re.compile(r"demo|intro|invitation|4k|one-file|dentro|fake", re.I)),
    ("music", re.compile(r"music", re.I)),
    ("graphics", re.compile(r"graphics|charts|basic", re.I)),
    ("tool", re.compile(r"tool|utility|diskmag|mag|papers", re.I)),
]
_C64_STRICT = re.compile(r"\b(c64|c-64|commodore\s*64)\b", re.I)
_C64_PLATFORM = re.compile(r"^(C64|C128|REU|EasyFlash|1541|SCPU|C64DTV)", re.I)
_ATOM = "{http://www.w3.org/2005/Atom}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"
_MEDIA = "{http://search.yahoo.com/mrss/}"


def _text(el: ET.Element | None, tag: str) -> str:
    v = el.find(tag) if el is not None else None
    return (v.text or "").strip() if v is not None and v.text else ""


def _date(value: str) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _plain(fragment: str, limit: int = 400) -> str:
    """HTML → short plain text (summaries are shown as text, never as HTML)."""
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment or "", flags=re.S | re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= limit else t[:limit - 1].rsplit(" ", 1)[0] + "…"


def _https(url: str | None) -> str | None:
    return url if url and urlparse(url).scheme in ("http", "https") else None


def _parse(xml: str) -> ET.Element:
    if "<!DOCTYPE" in xml[:2000] or "<!ENTITY" in xml:
        raise ValueError("feed with a DOCTYPE / entities refused")
    return ET.fromstring(xml)


def csdb_category(release_type: str) -> str:
    for name, rx in _CSDB_CATEGORY:
        if rx.search(release_type):
            return name
    return "other"


def parse_csdb(xml: str) -> list[dict[str, Any]]:
    out = []
    for it in _parse(xml).iter("item"):
        desc = _text(it, "description")
        rtype = m.group(1) if (m := re.search(r"Type:\s*<a[^>]*>([^<]+)</a>", desc)) else ""
        if rtype and not _C64_PLATFORM.match(rtype):
            continue                                  # Plus/4, VIC-20, Amiga… releases are skipped
        guid = _text(it, "guid") or _text(it, "link")
        rid = parse_qs(urlparse(guid).query).get("id", [""])[0]
        group = m.group(1) if (m := re.search(r"Released by:\s*<a[^>]*>([^<]+)</a>", desc)) else None
        dl = re.search(r'href="(https://csdb\.dk/release/download\.php\?id=\d+)"\s+title="([^"]+)"', desc)
        file_url = html.unescape(dl.group(2)) if dl else ""
        ext = unquote(urlparse(file_url).path).rsplit(".", 1)[-1].lower() if "." in file_url else ""
        on_csdb = (urlparse(file_url).hostname or "").lower() in DOWNLOAD_HOSTS
        img = m.group(1) if (m := re.search(r'<img[^>]+src="([^"]+)"', desc)) else None
        category = csdb_category(rtype)
        out.append({
            "guid": guid, "kind": "release", "title": html.unescape(_text(it, "title")),
            "url": f"https://csdb.dk/release/?id={rid}" if rid else guid, "author": group and html.unescape(group),
            "release_type": rtype or None, "category": category, "image": _https(img), "csdb_id": rid or None,
            "download_url": file_url or None,
            "playable": bool(on_csdb and ext in IMPORT_EXT),   # music and pictures come as programs too
            "summary": f"{rtype} by {html.unescape(group)}" if group and rtype else (rtype or None),
            "published_at": _date(_text(it, "pubDate")),
        })
    return out


def parse_atom(root: ET.Element, kind: str) -> list[dict[str, Any]]:
    out = []
    for e in root.iter(f"{_ATOM}entry"):
        link = next((ln.get("href") for ln in e.findall(f"{_ATOM}link") if ln.get("rel", "alternate") == "alternate"), None)
        if not _https(link):
            continue
        body = _text(e, f"{_ATOM}content") or _text(e, f"{_ATOM}summary")
        thumb = e.find(f"{_MEDIA}thumbnail")
        img = thumb.get("url") if thumb is not None else (m.group(1) if (m := re.search(r'<img[^>]+src="([^"]+)"', body)) else "")
        author = e.find(f"{_ATOM}author")
        out.append({"guid": _text(e, f"{_ATOM}id") or link, "kind": kind, "title": html.unescape(_text(e, f"{_ATOM}title")),
                    "url": link, "image": _https(html.unescape(img or "")), "summary": _plain(body),
                    "author": _text(author, f"{_ATOM}name") or None, "category": None, "release_type": None,
                    "published_at": _date(_text(e, f"{_ATOM}published") or _text(e, f"{_ATOM}updated"))})
    return out


def parse_rss(xml: str, kind: str) -> list[dict[str, Any]]:
    """itch.io and blog feeds (RSS 2.0, or Atom)."""
    root = _parse(xml)
    if root.tag == f"{_ATOM}feed":
        return parse_atom(root, kind)
    out = []
    for it in root.iter("item"):
        link = _text(it, "link")
        if not _https(link):
            continue
        desc = _text(it, "description")
        img = _text(it, "imageurl") or (m.group(1) if (m := re.search(r'<img[^>]+src="([^"]+)"', desc)) else "")
        if not img and (th := it.find(f"{_MEDIA}thumbnail")) is not None:
            img = th.get("url", "")
        title = _text(it, "plainTitle") or _text(it, "title")
        price = _text(it, "price")
        out.append({
            "guid": _text(it, "guid") or link, "kind": kind, "title": html.unescape(title), "url": link,
            "image": _https(img), "summary": _plain(desc),
            "category": "game" if kind == "release" else None,
            "release_type": ("Free" if price in ("", "$0.00") else price) if kind == "release" else None,
            "author": urlparse(link).hostname.split(".")[0] if kind == "release" and urlparse(link).hostname else None,
            "published_at": _date(_text(it, "pubDate")),
        })
    return out


def parse_youtube(xml: str, label: str, c64only: bool) -> list[dict[str, Any]]:
    out = []
    for e in _parse(xml).iter(f"{_ATOM}entry"):
        vid = _text(e, f"{_YT}videoId")
        title = _text(e, f"{_ATOM}title")
        group = e.find(f"{_MEDIA}group")
        desc = _text(group, f"{_MEDIA}description")
        if not vid or not re.fullmatch(r"[\w-]{6,20}", vid):
            continue
        if not c64only and not (C64_WORDS.search(title) or _C64_STRICT.search(desc[:300])):
            continue
        if re.search(r"#shorts\b", title + desc[:300], re.I):
            continue                                  # vertical shorts make poor TV viewing
        stats: dict[str, Any] = {}
        community = group.find(f"{_MEDIA}community") if group is not None else None
        if community is not None:
            if (st := community.find(f"{_MEDIA}statistics")) is not None and st.get("views", "").isdigit():
                stats["views"] = int(st.get("views"))
            if (sr := community.find(f"{_MEDIA}starRating")) is not None and sr.get("count", "").isdigit():
                stats["likes"] = int(sr.get("count"))
        out.append({
            "stats": stats or None,
            "guid": f"yt:{vid}", "kind": "video", "title": html.unescape(title), "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}", "author": label,
            "image": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "summary": _plain(desc, 300),
            "published_at": _date(_text(e, f"{_ATOM}published")),
        })
    return out


# ---------------------------------------------------------------- 🔥 popularity
STATS_DAYS = 60                 # releases younger than this keep getting their numbers refreshed
STATS_PER_RUN = 150             # pages read per check (one at a time, politely spaced)
STATS_GAP = 1.0                 # seconds between page reads


def parse_csdb_page(page: str) -> dict[str, Any]:
    """A CSDb release page → {downloads, comments, votes, rating}. The average shows once 8 votes are in;
    before that the page says "awaiting 8 votes (N left)", which still gives the number of votes."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"\s+", " ", text)
    out: dict[str, Any] = {"downloads": sum(int(n) for n in re.findall(r"downloads:\s*(\d+)", text, re.I)),
                           "comments": 0, "votes": 0}
    if m := re.search(r"User Comments\s*\((\d+)\)", text, re.I):
        out["comments"] = int(m.group(1))
    if m := re.search(r"User rating:.*?(\d+(?:\.\d+)?)/10\s*\((\d+) votes?\)", text, re.I):
        out["rating"], out["votes"] = float(m.group(1)), int(m.group(2))
    elif m := re.search(r"awaiting (\d+) votes?\s*\((\d+) left\)", text, re.I):
        out["votes"] = max(0, int(m.group(1)) - int(m.group(2)))
    return out


def parse_itch_page(page: str) -> dict[str, Any]:
    """An itch.io game page → {ratings, stars} from its structured data (no rating yet → zeros)."""
    block = re.search(r'"aggregateRating"\s*:\s*\{([^}]*)\}', page)
    count = re.search(r'"ratingCount"\s*:\s*"?(\d+)', block.group(1)) if block else None
    value = re.search(r'"ratingValue"\s*:\s*"?([\d.]+)', block.group(1)) if block else None
    if not count or not value:
        return {"ratings": 0}
    return {"ratings": int(count.group(1)), "stars": float(value.group(1))}


def _bayes(avg: float | None, n: int, prior: float, weight: int = 8) -> float:
    """An average that needs votes to move away from the typical score (3 votes of 10 ≠ 100 votes of 9.5)."""
    return prior if not avg or n <= 0 else (avg * n + prior * weight) / (n + weight)


def raw_score(source: str, stats: dict[str, Any] | None) -> float | None:
    """How popular one item is, on its own source's scale (only compared with items from the same source)."""
    if not stats:
        return None
    if source == "csdb":
        quality = _bayes(stats.get("rating"), stats.get("votes", 0), 6.5) - 6.5
        return (math.log1p(stats.get("downloads", 0)) + 1.5 * math.log1p(stats.get("comments", 0))
                + 1.5 * math.log1p(stats.get("votes", 0)) + 0.8 * quality)
    if source == "itch":
        quality = _bayes(stats.get("stars"), stats.get("ratings", 0), 3.5) - 3.5
        return 2.0 * math.log1p(stats.get("ratings", 0)) + 1.5 * quality
    if source.startswith("youtube:"):
        return math.log1p(stats.get("views", 0)) + 0.5 * math.log1p(stats.get("likes", 0))
    return None


def _group(source: str) -> str:
    return "youtube" if source.startswith("youtube:") else source


def _percentiles(values: dict[int, float]) -> dict[int, float]:
    """id → 0..100: the share of items in the group this one beats (ties share a rank)."""
    if not values:
        return {}
    ordered = sorted(values.values())
    n = len(ordered)
    out = {}
    for k, v in values.items():
        below = bisect.bisect_left(ordered, v)
        same = bisect.bisect_right(ordered, v) - below
        out[k] = round(100.0 * (below + 0.5 * same) / n, 1) if n > 1 else 50.0
    return out


class NewsService:
    def __init__(self, ask, sf, hub=None, fetch=None):  # noqa: ANN001
        self.ask, self.sf, self.hub = ask, sf, hub
        self._fetch = fetch or self._http_get
        self._task: asyncio.Task | None = None
        self._stats_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.state: dict[str, Any] = {"checkedAt": None, "running": False, "errors": {}, "added": 0}
        self.digest: dict[str, Any] | None = None

    # ------------------------------------------------------------ monitoring
    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="news-monitor")

    async def stop(self) -> None:
        for task in (self._task, self._stats_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    async def _loop(self) -> None:
        await asyncio.sleep(20)                       # let the console finish starting
        while True:
            try:
                await self.refresh()
                await self.update_stats()
            except Exception as exc:  # noqa: BLE001 - the monitor keeps going
                log.warning("news check failed: %s", exc)
            await asyncio.sleep(INTERVAL)

    @staticmethod
    async def _http_get(url: str) -> str:
        async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as client:
            r = await client.get(url)
        r.raise_for_status()
        if len(r.content) > 3_000_000:
            raise ValueError("feed too large")
        # feeds without a charset header are UTF-8 (XML's default) — httpx would otherwise guess
        return r.content.decode(r.charset_encoding or "utf-8", "replace")

    async def _one(self, feed: dict[str, Any]) -> list[dict[str, Any]]:
        xml = await self._fetch(feed["url"])
        if feed["source"] == "csdb":
            items = parse_csdb(xml)
        elif feed["kind"] == "video":
            items = parse_youtube(xml, feed["label"], bool(feed.get("c64only")))
        else:
            items = parse_rss(xml, feed["kind"])
        for i in items:
            i["source"] = feed["source"]
        return items

    async def refresh(self) -> dict[str, Any]:
        """Check every feed now. Returns {added, errors, checkedAt}."""
        async with self._lock:
            self.state["running"] = True
            try:
                results = await asyncio.gather(*(self._one(f) for f in FEEDS), return_exceptions=True)
                errors: dict[str, str] = {}
                items: list[dict[str, Any]] = []
                for feed, r in zip(FEEDS, results, strict=True):
                    if isinstance(r, BaseException):
                        errors[feed["label"]] = f"{type(r).__name__}: {str(r)[:120]}"
                        log.info("news feed %s failed: %s", feed["label"], errors[feed["label"]])
                    else:
                        items.extend(r)
                added = self._store(items)
                self.state.update(checkedAt=datetime.now(UTC).isoformat(), errors=errors, added=len(added))
                if added:
                    self.digest = None
                    if self.hub:
                        self.hub.publish("news", {"added": len(added), "titles": [a["title"] for a in added[:5]]})
                return {"added": len(added), "errors": errors, "checkedAt": self.state["checkedAt"]}
            finally:
                self.state["running"] = False

    def _store(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        fields = {c.name for c in NewsItem.__table__.columns} - {"id", "fetched_at", "game_id"}
        added = []
        with self.sf() as s:
            existing = {n.guid: n for n in s.scalars(select(NewsItem).where(NewsItem.guid.in_([i["guid"] for i in items])))}
            known = set(existing)
            now = datetime.now(UTC)
            cutoff = now - timedelta(days=KEEP_DAYS)
            video_cutoff = now - timedelta(days=KEEP_DAYS_VIDEO)
            for i in items:
                if i["guid"] in existing and i.get("stats"):          # videos: fresh view counts on every check
                    existing[i["guid"]].stats, existing[i["guid"]].stats_at = i["stats"], now
                if i["guid"] in known or i["published_at"] < (video_cutoff if i["kind"] == "video" else cutoff):
                    continue
                if i.get("stats"):
                    i["stats_at"] = now
                known.add(i["guid"])
                row = {k: v for k, v in i.items() if k in fields}
                for k, n in (("title", 300), ("url", 600), ("image", 600), ("download_url", 600), ("guid", 400)):
                    if isinstance(row.get(k), str):
                        row[k] = row[k][:n]
                s.add(NewsItem(**row))
                added.append(i)
            s.query(NewsItem).filter(NewsItem.published_at < cutoff, NewsItem.game_id.is_(None),
                                     NewsItem.kind != "video").delete(synchronize_session=False)
            s.query(NewsItem).filter(NewsItem.published_at < video_cutoff, NewsItem.kind == "video").delete(
                synchronize_session=False)
            s.commit()
        self.rank()
        return added

    # ------------------------------------------------------------ 🔥 popularity
    def start_stats(self) -> None:
        if self._stats_task is None or self._stats_task.done():
            self._stats_task = asyncio.create_task(self.update_stats(), name="news-stats")

    async def update_stats(self, limit: int = STATS_PER_RUN, gap: float = STATS_GAP) -> int:
        """Read the release pages (CSDb, itch.io) of recent releases for their downloads / votes / ratings.
        Newer releases are re-read more often; one page at a time, spaced out, to be a polite visitor."""
        now = datetime.now(UTC)
        with self.sf() as s:
            rows = s.scalars(select(NewsItem).where(
                NewsItem.kind == "release", NewsItem.source.in_(("csdb", "itch")),
                NewsItem.published_at > now - timedelta(days=STATS_DAYS))).all()
            due = []
            for n in rows:
                at = n.stats_at.replace(tzinfo=UTC) if n.stats_at and n.stats_at.tzinfo is None else n.stats_at
                pub = n.published_at.replace(tzinfo=UTC) if n.published_at.tzinfo is None else n.published_at
                every = timedelta(hours=6) if now - pub < timedelta(days=14) else timedelta(hours=24)
                if at is None or now - at > every:
                    due.append((at or datetime.min.replace(tzinfo=UTC), n.id, n.source, n.url))
        due.sort()                                       # never-read first, then the stalest
        done = 0
        for _, item_id, source, url in due[:limit]:
            try:
                page = await self._fetch(url)
                stats = parse_csdb_page(page) if source == "csdb" else parse_itch_page(page)
            except Exception as exc:  # noqa: BLE001 - try again next time
                log.info("stats for %s failed: %s", url, exc)
                continue
            with self.sf() as s:
                n = s.get(NewsItem, item_id)
                if n:
                    n.stats, n.stats_at = stats, datetime.now(UTC)
                    s.commit()
                    done += 1
            if gap:
                await asyncio.sleep(gap)
        if done:
            self.rank()
        return done

    def rank(self) -> None:
        """Popularity and trend percentiles (0-100) within each source — CSDb downloads and YouTube views
        are not on the same scale, but "top 10% of this week's CSDb releases" and "top 10% of videos" are."""
        now = datetime.now(UTC)
        with self.sf() as s:
            rows = s.scalars(select(NewsItem).where(NewsItem.stats.is_not(None))).all()
            pop: dict[str, dict[int, float]] = {}
            hot: dict[str, dict[int, float]] = {}
            for n in rows:
                raw = raw_score(n.source, n.stats)
                if raw is None:
                    continue
                pub = n.published_at.replace(tzinfo=UTC) if n.published_at.tzinfo is None else n.published_at
                days = max(0.0, (now - pub).total_seconds() / 86400)
                pop.setdefault(_group(n.source), {})[n.id] = raw
                hot.setdefault(_group(n.source), {})[n.id] = raw / (days + 2) ** 0.6   # per day, gently
            p = {k: v for g in pop.values() for k, v in _percentiles(g).items()}
            t = {k: v for g in hot.values() for k, v in _percentiles(g).items()}
            for n in rows:
                n.popularity, n.trend = p.get(n.id), t.get(n.id)
            s.commit()

    # ------------------------------------------------------------ reading
    @staticmethod
    def to_dict(n: NewsItem) -> dict[str, Any]:
        return {"id": n.id, "kind": n.kind, "source": n.source, "sourceLabel": LABELS.get(n.source, n.source),
                "category": n.category, "title": n.title, "url": n.url, "summary": n.summary, "image": n.image,
                "author": n.author, "releaseType": n.release_type, "csdbId": n.csdb_id, "videoId": n.video_id,
                "playable": n.playable, "gameId": n.game_id, "stats": n.stats or None,
                "popularity": n.popularity, "trend": n.trend,
                "publishedAt": n.published_at.isoformat() if n.published_at else None}

    def list(self, kind: str | None = None, category: str | None = None, q: str | None = None,
             since: datetime | None = None, limit: int = 60, offset: int = 0,
             sort: str = "newest") -> dict[str, Any]:
        with self.sf() as s:
            stmt = select(NewsItem)
            if kind:
                stmt = stmt.where(NewsItem.kind == kind)
            if category:
                stmt = stmt.where(NewsItem.category == category)
            if q:
                like = f"%{q.strip()[:60]}%"
                stmt = stmt.where(NewsItem.title.ilike(like) | NewsItem.author.ilike(like) | NewsItem.summary.ilike(like))
            if since:
                stmt = stmt.where(NewsItem.published_at > since)
            order = {"popular": (NewsItem.popularity.is_(None), NewsItem.popularity.desc()),
                     "trending": (NewsItem.trend.is_(None), NewsItem.trend.desc())}.get(sort, ())
            rows = s.scalars(stmt.order_by(*order, NewsItem.published_at.desc(), NewsItem.id)
                             .offset(offset).limit(min(limit, 200))).all()
            counts = dict(s.execute(select(NewsItem.kind, func.count()).group_by(NewsItem.kind)).all())
            return {"items": [self.to_dict(r) for r in rows], "counts": counts, "state": self.status()}

    def count_since(self, since: datetime) -> dict[str, int]:
        """How many items arrived since the user last looked (by when we found them, not when published)."""
        with self.sf() as s:
            rows = s.execute(select(NewsItem.kind, func.count()).where(NewsItem.fetched_at > since)
                             .group_by(NewsItem.kind)).all()
        out = {k: int(n) for k, n in rows}
        out["total"] = sum(out.values())
        return out

    def status(self) -> dict[str, Any]:
        return {**self.state, "intervalMinutes": INTERVAL // 60,
                "feeds": [{"source": f["source"], "label": f["label"], "kind": f["kind"]} for f in FEEDS]}

    def get(self, item_id: int) -> NewsItem | None:
        with self.sf() as s:
            n = s.get(NewsItem, item_id)
            if n:
                s.expunge(n)
            return n

    def link_game(self, item_id: int, game_id: int) -> None:
        with self.sf() as s:
            n = s.get(NewsItem, item_id)
            if n:
                n.game_id = game_id
                s.commit()

    # ------------------------------------------------------------ 🤖 digest
    async def make_digest(self) -> dict[str, Any]:
        """🤖 "This week in C64": the AI reads the last 7 days' headlines and picks what is worth a look."""
        week = self.list(since=datetime.now(UTC) - timedelta(days=7), limit=80)["items"]
        if not week:
            raise AskError("Nothing new in the last 7 days yet — check the feeds first")
        lines = "\n".join(
            f"[{i['id']}] ({i['kind']}{'/' + i['category'] if i['category'] else ''}, {i['sourceLabel']}) "
            f"{i['title']}" + (f" — {i['author']}" if i["author"] else "") for i in week)
        system = ("You are the editor of a friendly Commodore 64 news round-up for enthusiasts. Only use the items "
                  "given. Reply with one JSON object: {\"headline\": str (max 90 chars), \"intro\": str (2 sentences), "
                  "\"picks\": [{\"id\": int, \"why\": str (max 140 chars)}] (4-8 picks: the most interesting new "
                  "games, demos, news and videos; prefer variety)}.")
        data, _ = await ask_json(self.ask, system, f"This week's items:\n{lines}", max_tokens=2500, what="news digest")
        ids = {i["id"]: i for i in week}
        picks = []
        for p in data.get("picks") or []:
            try:
                item = ids.get(int(p.get("id")))
            except (TypeError, ValueError, AttributeError):
                continue
            if item and all(x["item"]["id"] != item["id"] for x in picks):
                picks.append({"item": item, "why": strs([p.get("why")], 1, 160)[0] if p.get("why") else ""})
        if not picks:
            raise AskError("The AI model could not make the news digest: no usable picks")
        self.digest = {"headline": strs([data.get("headline")], 1, 90)[0] if data.get("headline") else "This week in C64",
                       "intro": strs([data.get("intro")], 1, 400)[0] if data.get("intro") else "",
                       "picks": picks[:8], "madeAt": datetime.now(UTC).isoformat()}
        return self.digest
