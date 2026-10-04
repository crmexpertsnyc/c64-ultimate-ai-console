"""📅 Retro events calendar, worldwide (the service behind ``container.events``; attached from ``events.attach``).

Sources:

* CSDb upcoming events RSS — demoparties, meetings and compos ("Held on: 12 - 14 March 2027"), checked every 6 hours.
  For events in the next 60 days the CSDb event page is read too (politely, one at a time) for the event's website,
  its place and — when the website is a Twitch / YouTube channel — the stream.
* 🤖 Web research (weekly, and on demand) — computer fairs, retro gaming expos, user-group meetups, VCF-style
  festivals. The AI reads Brave web search results and answers with JSON; an event is only kept when its URL is one
  of the search results (or on the same site as a result it cites). The model never makes HTTP calls and never gets
  to invent links.
* Events the user adds by hand (they can also hide any event).

"Live now": an event whose dates include today (parties stream; recent videos from the Transmission64 party channel
are shown with them, from the 🎬 Watch feed).
"""

from __future__ import annotations

import asyncio
import calendar
import html
import logging
import re
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import or_, select

from app.models.db import NewsItem
from app.models.events import Event, EventState
from app.services.geo import FLAGS, normalize_country, place, region_name, scope_of

from .ai_json import ask_json
from .ask import AskError
from .crawlers import EVENT_CALENDARS, EVENT_SITES, find_event_dates, near_month, page_text, parse_ics, split_location
from .news import NewsService, _parse, _plain, _text

log = logging.getLogger("c64.events")

CSDB_RSS = "https://csdb.dk/rss/upcomingevents.php"
CSDB_EVERY = 6 * 3600                 # seconds between CSDb checks
RESEARCH_EVERY = 7 * 24 * 3600        # web research at most weekly (plus on demand)
DETAIL_DAYS = 60                      # CSDb event pages are read for events starting within this many days
DETAIL_EVERY = timedelta(days=7)      # … and re-read at most weekly
DETAIL_PER_RUN = 8
DETAIL_GAP = 2.0                      # seconds between CSDb page reads
RESEARCH_HORIZON = 548                # ~18 months
LIVE_MAX_DAYS = 14                    # a 3-month online compo is "ongoing", not "live now"
KEEP_PAST_DAYS = 400                  # past CSDb / web events are pruned after this
PARTY_CHANNEL = "youtube:transmission64"
SOURCE_LABELS = {"csdb": "CSDb", "dpn": "demoparty.net", "site": "official site", "web": "web", "user": "yours"}
# which source wins when two list the same event (lower = better)
SOURCE_RANK = {"user": 0, "site": 1, "csdb": 2, "dpn": 3, "web": 4}
SITE_GAP = 2.0                        # seconds between official-site reads
STREAM_HOSTS = ("twitch.tv", "youtube.com", "youtu.be")

RESEARCH_QUERIES = [                  # US-heavy (the home country's events are highlighted), then worldwide
    "retro gaming convention {y} {y1} USA",
    "vintage computer festival {y1} VCF",
    "classic video game expo {y1} United States",
    "retro computing conference {y1}",
    "arcade pinball show {y1} USA",
    "Commodore Amiga Atari Apple II user group meeting {y} {y1}",
    "retro gaming expo {y1} Europe",
    "retro computer fair {y} {y1} UK Germany",
    "demoparty {y1}",
]
WEB_TYPES = ("Retro Gaming Expo", "Vintage Computer Festival", "Computer Fair", "Conference", "Convention",
             "Arcade & Pinball Show", "User Group Meeting", "Demo Party", "Swap Meet", "Other")
RESEARCH_SYSTEM = (
    "You list real upcoming retro events of any kind (retro gaming conventions and expos, vintage computer festivals, "
    "classic computing conferences, arcade and pinball shows, swap meets, user-group meetings for any classic platform "
    "such as Commodore, Atari, Apple II, TRS-80 or Amiga, demoparties) found in the numbered web search results. Only use events "
    "that the results clearly mention with dates; never invent events, dates or links. Reply with one JSON object: "
    '{"events": [{"name": str, "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "city": str, "state": str (US state / '
    'province code, or ""), "country": str (English name), "type": one of '
    + ", ".join(f'"{t}"' for t in WEB_TYPES) + ', "url": str (the page of a search result '
    'about this event, or "" if unsure), "sources": [result numbers]}]}. Skip events that are already over.')

_MONTHS: dict[str, int] = {}
for _i in range(1, 13):
    _MONTHS[calendar.month_name[_i].lower()] = _i
    _MONTHS[calendar.month_abbr[_i].lower()] = _i
_MONTHS["sept"] = 9
_PART = re.compile(r"^(?:(\d{1,2})(?:st|nd|rd|th)?\.?)?\s*(?:([A-Za-z]+)\.?)?\s*(?:(\d{4}))?$")


# ------------------------------------------------------------------ state (small key → value memory)
def load_state(sf, key: str) -> dict[str, Any]:  # noqa: ANN001
    with sf() as s:
        row = s.get(EventState, key)
        return dict(row.value or {}) if row else {}


def save_state(sf, key: str, value: dict[str, Any]) -> None:  # noqa: ANN001
    with sf() as s:
        row = s.get(EventState, key)
        if row is None:
            s.add(EventState(key=key, value=dict(value)))
        else:
            row.value = dict(value)
        s.commit()


# ------------------------------------------------------------------ dates
def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def parse_held_on(text: str) -> tuple[date, date] | None:
    """CSDb's "Held on" → (start, end), end inclusive. Handles "28 November 2026", "12 - 14 March 2027",
    "30 April - 2 May 2027", "30 December 2026 - 2 January 2027", "30 December - 2 January 2027" and "March 2027"
    (the whole month). None when it can't be read."""
    t = re.sub(r"\s+", " ", html.unescape(text or "")).strip().replace("–", "-").replace("—", "-")
    t = re.sub(r"\s*(?:-|\bto\b|\buntil\b)\s*", " - ", t, flags=re.I).strip(" -")
    parts = [p.strip() for p in t.split(" - ") if p.strip()]
    if not 1 <= len(parts) <= 2:
        return None
    parsed: list[list[int | None]] = []
    for p in parts:
        m = _PART.match(p)
        if not m or not any(m.groups()):
            return None
        month = None
        if m.group(2):
            month = _MONTHS.get(m.group(2).lower())
            if month is None:
                return None
        parsed.append([int(m.group(1)) if m.group(1) else None, month, int(m.group(3)) if m.group(3) else None])
    end_d, end_m, end_y = parsed[-1]
    if end_m is None or end_y is None:
        return None
    start_d, start_m, start_y = parsed[0] if len(parsed) == 2 else (end_d, end_m, end_y)
    start_m = start_m or end_m
    if start_y is None:
        start_y = end_y - 1 if start_m > end_m else end_y        # "30 December - 2 January 2027"
    try:
        start = date(start_y, start_m, start_d or 1)
        end = date(end_y, end_m, end_d or _last_day(end_y, end_m))
    except ValueError:
        return None
    return (start, end) if start <= end else None


def _iso_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ CSDb
def split_country(title: str) -> tuple[str, str | None]:
    """"Fioniadata 2027 (Denmark)" → ("Fioniadata 2027", "Denmark")."""
    m = re.match(r"^(.*?)\s*\(([^()]{2,40})\)\s*$", title.strip())
    if m and m.group(1) and not re.search(r"\d", m.group(2)):
        return m.group(1).strip(), m.group(2).strip()
    return title.strip(), None


def _https(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    p = urlparse(url)
    return url if p.scheme in ("http", "https") and p.hostname and not re.search(r"[\s<>\"]", url) else None


def parse_csdb_events(xml: str) -> list[dict[str, Any]]:
    out = []
    for it in _parse(xml).iter("item"):
        guid = _text(it, "guid") or _text(it, "link")
        eid = parse_qs(urlparse(html.unescape(guid)).query).get("id", [""])[0]
        if not eid.isdigit():
            continue
        desc = _text(it, "description")
        held = re.search(r"Held on:\s*([^<]+)", desc, re.I)
        dates = parse_held_on(held.group(1)) if held else None
        if not dates:
            log.info("CSDb event %s: dates not understood (%r)", eid, held.group(1) if held else None)
            continue
        name, country = split_country(html.unescape(_text(it, "title")))
        etype = re.search(r"Event type:\s*([^<]+)", desc, re.I)
        img = re.search(r'<img[^>]+src="([^"]+)"', desc, re.I)
        tag = re.search(r"<i>(.*?)</i>", desc, re.I | re.S)
        tagline = re.sub(r"^\s*-\s*", "", _plain(tag.group(1), 200)) if tag else ""
        out.append({
            "source": "csdb", "ext_id": f"csdb:{eid}", "name": name[:200], "country": country,
            "type": _plain(etype.group(1), 120) if etype else None, "start": dates[0], "end": dates[1],
            "page_url": f"https://csdb.dk/event/?id={eid}", "image": _https(html.unescape(img.group(1))) if img else None,
            "tagline": tagline or None,
        })
    return out


def parse_csdb_event_page(page: str) -> dict[str, Any]:
    """A CSDb event page → {website, stream, city, country} (whatever it lists)."""
    out: dict[str, Any] = {}
    web = re.search(r"<b>\s*Website\s*:?\s*</b>\s*(?:<br\s*/?>)?\s*<a[^>]+href=\"([^\"]+)\"", page, re.I)
    if web and (url := _https(html.unescape(web.group(1)))):
        out["website"] = url
    stream = re.search(r"<b>\s*(?:Live\s*)?Stream[^<]{0,20}</b>\s*(?:<br\s*/?>)?\s*<a[^>]+href=\"([^\"]+)\"", page, re.I)
    if stream and (url := _https(html.unescape(stream.group(1)))):
        out["stream"] = url
    elif out.get("website") and _host(out["website"]).endswith(STREAM_HOSTS):
        out["stream"] = out["website"]
    place = re.search(r"<b>\s*Place\s*:?\s*</b>\s*<br\s*/?>(.*?)<br\s*/?>\s*<br", page, re.I | re.S)
    if place:
        lines = [x for x in (_plain(p, 120) for p in re.split(r"<br\s*/?>", place.group(1))) if x]
        if lines:
            out["country"] = lines[-1]
        if len(lines) >= 2:
            city = re.sub(r"^(?:[A-Z]{1,3}-)?\d{3,6}\s+", "", lines[-2]).strip()   # "12681 Berlin" → "Berlin"
            city = re.sub(r"\s+\d{3,6}$", "", city).strip()
            if city:
                out["city"] = city
    return out


# ------------------------------------------------------------------ 🤖 web research
def _host(url: str | None) -> str:
    h = (urlparse(url or "").hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _norm_url(url: str | None) -> str | None:
    if not _https(url):
        return None
    p = urlparse(url.strip())  # type: ignore[union-attr]
    return f"{_host(url)}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


def _clip(v: Any, n: int) -> str | None:
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(v or "")).strip()
    return s[:n] if s else None


def name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _collapse(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One web result per show within a research run (the first, usually the most specific, wins)."""
    out: list[dict[str, Any]] = []
    for e in events:
        k = _bare_key(e["name"])
        if not any(len(k) >= 5 and (k in _bare_key(o["name"]) or _bare_key(o["name"]) in k)
                   and e["start"] <= o["end"] and o["start"] <= e["end"] for o in out):
            out.append(e)
    return out


def _bare_key(name: str) -> str:
    """The name without its year: "TooManyGames 2027" and "TooManyGames" are the same show."""
    return name_key(re.sub(r"\b20\d\d\b", "", name or ""))


def filter_research(data: dict[str, Any], results: list[dict[str, str]], today: date) -> tuple[list[dict[str, Any]], int]:
    """The model's events → the ones that are sourced and in range. Returns (events, dropped).
    The kept URL is always a search-result URL, or the model's URL when it is on the same site as a result it cites."""
    by_url: dict[str, str] = {}
    for r in results:
        if (n := _norm_url(r.get("url"))) and n not in by_url:
            by_url[n] = r["url"]
    out: list[dict[str, Any]] = []
    dropped = 0
    raw = data.get("events") if isinstance(data, dict) else None
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            dropped += 1
            continue
        name = _clip(e.get("name"), 200)
        start = _iso_date(e.get("start"))
        end = _iso_date(e.get("end")) or start
        if not name or not start or not end or end < start or (end - start).days > 30:
            dropped += 1
            continue
        if end < today or start > today + timedelta(days=RESEARCH_HORIZON):
            dropped += 1
            continue
        cited = []
        years: set[int] = set()
        for n in e.get("sources") or []:
            try:
                k = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= k <= len(results) and _https(results[k - 1].get("url")):
                cited.append(results[k - 1]["url"])
                r = results[k - 1]
                about = f"{r.get('url')} {r.get('title')} {r.get('description')}"
                years |= {int(y) for y in re.findall(r"\b(20[2-3]\d)\b", about)}
        if years and start.year not in years:      # e.g. a 2026 article turned into a 2027 date: not trusted
            dropped += 1
            continue
        given = str(e.get("url") or "").strip()
        url = None
        if given:
            n = _norm_url(given)
            if n and n in by_url:
                url = by_url[n]
            elif n and any(_host(given) == _host(c) for c in cited):
                url = _https(given)
            if url is None:                     # a link that isn't from the search results: the event goes
                dropped += 1
                continue
        elif cited:
            url = cited[0]
        else:
            dropped += 1
            continue
        etype = _clip(e.get("type"), 60)
        if etype and etype not in WEB_TYPES:
            etype = next((t for t in WEB_TYPES if t.lower() in etype.lower() or etype.lower() in t.lower()), "Other")
        out.append({"source": "web", "ext_id": f"web:{_bare_key(name)[:120]}:{start.isoformat()}", "name": name,
                    "start": start, "end": end,
                    "city": ", ".join(x for x in (_clip(e.get("city"), 100), _clip(e.get("state"), 30)) if x) or None,
                    "country": _clip(e.get("country"), 80),
                    "type": etype, "url": url, "page_url": cited[0] if cited else url})
    return out, dropped


# ------------------------------------------------------------------ 📅 iCalendar
def ics_escape(text: str | None) -> str:
    s = str(text or "")
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def ics_fold(line: str) -> str:
    """Lines longer than 75 octets are folded (CRLF + space), never inside a UTF-8 character."""
    parts, cur, size = [], "", 0
    for ch in line:
        b = len(ch.encode("utf-8"))
        if size + b > 75:
            parts.append(cur)
            cur, size = " ", 1
        cur += ch
        size += b
    parts.append(cur)
    return "\r\n".join(parts)


def _uri(url: str | None) -> str | None:
    u = _https(url)
    return u if u and not re.search(r"[\x00-\x1f\x7f]", u) else None


def ics_vevent(e: Event, stamp: datetime, host: str = "c64-ai-console") -> list[str]:
    where = ", ".join(x for x in (e.city, e.country) if x)
    desc = [x for x in (e.tagline, e.type and f"Type: {e.type}", e.notes,
                        e.stream_url and f"Stream: {e.stream_url}", e.page_url and f"More: {e.page_url}") if x]
    lines = ["BEGIN:VEVENT", f"UID:event-{e.id}-{e.ext_id.replace(':', '-')[:60]}@{host}",
             f"DTSTAMP:{stamp.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}",
             f"DTSTART;VALUE=DATE:{e.start.strftime('%Y%m%d')}",
             f"DTEND;VALUE=DATE:{(e.end + timedelta(days=1)).strftime('%Y%m%d')}",     # all-day: exclusive end
             f"SUMMARY:{ics_escape(e.name)}"]
    if where:
        lines.append(f"LOCATION:{ics_escape(where)}")
    if desc:
        lines.append(f"DESCRIPTION:{ics_escape(chr(10).join(desc))}")
    if url := _uri(e.url or e.page_url):
        lines.append(f"URL:{url}")
    if e.type:
        lines.append(f"CATEGORIES:{','.join(ics_escape(t.strip()) for t in e.type.split(',') if t.strip())}")
    lines.append("TRANSP:TRANSPARENT")
    lines.append("END:VEVENT")
    return lines


def ics_calendar(events: list[Event], name: str = "C64 retro events") -> str:
    stamp = datetime.now(UTC)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//C64 Ultimate AI Console//Retro events//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{ics_escape(name)}",
             "X-PUBLISHED-TTL:PT6H", "REFRESH-INTERVAL;VALUE=DURATION:PT6H"]
    for e in events:
        lines.extend(ics_vevent(e, stamp))
    lines.append("END:VCALENDAR")
    return "\r\n".join(ics_fold(x) for x in lines) + "\r\n"


# ------------------------------------------------------------------ service
class EventError(Exception):
    """A request the events feature refuses (→ 409)."""


USER_FIELDS = ("name", "type", "start", "end", "city", "country", "url", "notes")


class EventService:
    def __init__(self, ask, sf, hub=None, fetch: Callable[[str], Awaitable[str]] | None = None,  # noqa: ANN001
                 enabled: Callable[[], bool] | None = None):
        self.ask, self.sf, self.hub = ask, sf, hub
        self._fetch = fetch or NewsService._http_get
        self._enabled = enabled or (lambda: True)
        self.today: Callable[[], date] = date.today
        self.detail_gap = DETAIL_GAP
        self.firmware = None                                    # FirmwareWatch, set by events.attach
        self.home: Callable[[], str] = lambda: "United States"  # the settings' EVENTS_HOME_COUNTRY (set by attach)
        self._task: asyncio.Task | None = None
        self._details_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._research_lock = asyncio.Lock()
        self.state: dict[str, Any] = {"running": False, "researching": False, "errors": {}}

    # ------------------------------------------------------------ loop
    def start(self) -> None:
        """Background checks (CSDb every 6 h, web research weekly). Off when the news monitor is off (and in tests)."""
        if not self._enabled():
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="events-monitor")
        if self.firmware is not None:
            self.firmware.start()

    async def stop(self) -> None:
        for task in (self._task, self._details_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        if self.firmware is not None:
            await self.firmware.stop()

    @staticmethod
    def _age(iso: str | None) -> float | None:
        if not iso:
            return None
        try:
            return (datetime.now(UTC) - datetime.fromisoformat(iso)).total_seconds()
        except ValueError:
            return None

    async def _loop(self) -> None:
        await asyncio.sleep(45)                                   # let the console finish starting
        while True:
            try:
                age = self._age(load_state(self.sf, "csdb").get("at"))
                if age is None or age >= CSDB_EVERY:
                    await self.refresh()
                age = self._age(load_state(self.sf, "research").get("at"))
                if self.ai_ready and (age is None or age >= RESEARCH_EVERY):
                    await self.research()
            except Exception as exc:  # noqa: BLE001 - the monitor keeps going
                log.info("events check failed: %s", exc)
            await asyncio.sleep(3600)

    @property
    def ai_ready(self) -> bool:
        try:
            return bool(self.ask.provider.configured and self.ask.web_search_on)
        except Exception:  # noqa: BLE001
            return False

    def _publish(self, what: str, **data: Any) -> None:
        if self.hub:
            self.hub.publish("events", {"what": what, **data})

    # ------------------------------------------------------------ CSDb
    async def refresh(self, wait_details: bool = True) -> dict[str, Any]:
        """Read CSDb's upcoming events now, then the pages of the next 60 days' events (awaited, or in the
        background with ``wait_details=False`` — they're read slowly). Returns {added, updated, details, errors}."""
        async with self._lock:
            self.state["running"] = True
            errors: dict[str, str] = {}
            added = updated = 0
            try:
                try:
                    items = parse_csdb_events(await self._fetch(CSDB_RSS))
                    added, updated = self._upsert(items, keep_details=True)
                except Exception as exc:  # noqa: BLE001
                    errors["CSDb"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                    log.info("CSDb events failed: %s", errors["CSDb"])
                details = 0
                if not errors:
                    if wait_details:
                        details = await self._details()
                    elif self._details_task is None or self._details_task.done():
                        self._details_task = asyncio.create_task(self._details_then_publish(), name="events-details")
                self._prune()
                at = datetime.now(UTC).isoformat()
                save_state(self.sf, "csdb", {"at": at, "added": added, "errors": errors})
                self.state["errors"] = errors
                if added or updated or details:
                    self._publish("csdb", added=added, updated=updated)
                return {"added": added, "updated": updated, "details": details, "errors": errors, "checkedAt": at}
            finally:
                self.state["running"] = False

    async def _details_then_publish(self) -> None:
        try:
            if await self._details():
                self._publish("details")
        except Exception as exc:  # noqa: BLE001
            log.info("CSDb event pages failed: %s", exc)

    @staticmethod
    def _placed(i: dict[str, Any]) -> dict[str, Any]:
        """One spelling per country, the US state / province picked out of the city, and the event's scope."""
        city, region, country = place(i.get("city"), i.get("country"))
        scope = "commodore" if i.get("source") == "csdb" else scope_of(i.get("name"), i.get("type"), i.get("tagline"))
        placed = {"city": city, "region": region or i.get("region"), "country": country, "scope": i.get("scope") or scope}
        # a feed without a place must not wipe what an event page told us (CSDb's RSS has no city)
        return {**i, **{k: v for k, v in placed.items() if v is not None or k in i}}

    def _upsert(self, items: list[dict[str, Any]], keep_details: bool = False) -> tuple[int, int]:
        items = [self._placed(i) for i in items]
        added = updated = 0
        with self.sf() as s:
            existing = {e.ext_id: e for e in s.scalars(select(Event).where(Event.ext_id.in_([i["ext_id"] for i in items])))}
            for i in items:
                row = existing.get(i["ext_id"])
                if row is None:
                    s.add(Event(**i))
                    existing[i["ext_id"]] = i  # type: ignore[assignment]   # duplicates in one feed: first wins
                    added += 1
                    continue
                if not isinstance(row, Event):
                    continue
                changed = False
                for k, v in i.items():
                    if keep_details and k == "country" and not v:
                        continue                                 # keep what the event page said
                    if getattr(row, k) != v:
                        setattr(row, k, v)
                        changed = True
                updated += changed
            s.commit()
        return added, updated

    async def _details(self, limit: int = DETAIL_PER_RUN) -> int:
        """Website / place / stream from the CSDb pages of events in the next 60 days (rate-limited)."""
        today = self.today()
        now = datetime.now(UTC)
        with self.sf() as s:
            rows = s.scalars(select(Event).where(Event.source == "csdb", Event.end >= today,
                                                 Event.start <= today + timedelta(days=DETAIL_DAYS))
                             .order_by(Event.start)).all()
            due = []
            for e in rows:
                at = e.details_at
                if at is not None and at.tzinfo is None:
                    at = at.replace(tzinfo=UTC)
                if e.page_url and (at is None or now - at > DETAIL_EVERY):
                    due.append((e.id, e.page_url))
        done = 0
        for n, (event_id, page_url) in enumerate(due[:limit]):
            if n and self.detail_gap:
                await asyncio.sleep(self.detail_gap)
            try:
                info = parse_csdb_event_page(await self._fetch(page_url))
            except Exception as exc:  # noqa: BLE001 - next time
                log.info("CSDb event page %s failed: %s", page_url, exc)
                continue
            with self.sf() as s:
                e = s.get(Event, event_id)
                if e is None:
                    continue
                e.url = info.get("website") or e.url
                e.stream_url = info.get("stream") or e.stream_url
                e.city, e.region, e.country = place((info.get("city") or e.city or "")[:120] or None,
                                                    e.country or (info.get("country") or "")[:80] or None)
                e.details_at = datetime.now(UTC)
                s.commit()
                done += 1
        return done

    def _prune(self) -> None:
        cutoff = self.today() - timedelta(days=KEEP_PAST_DAYS)
        with self.sf() as s:
            s.query(Event).filter(Event.end < cutoff, Event.source != "user").delete(synchronize_session=False)
            s.commit()

    # ------------------------------------------------------------ 🤖 research
    async def research(self, queries: list[str] | None = None) -> dict[str, Any]:
        """🤖 Find fairs, expos and meetups worldwide with web search. Raises AskError when no AI / web search."""
        if not self.ask.provider.configured:
            raise AskError("This needs an AI model — set one up under Settings → AI assistant")
        if not self.ask.web_search_on:
            raise AskError("Finding events needs web search — add a Brave Search API key under Settings → AI assistant")
        if self._research_lock.locked():
            raise AskError("Already looking for events — give it a minute")
        async with self._research_lock:
            self.state["researching"] = True
            try:
                today = self.today()
                y = today.year
                qs = queries or [q.format(y=y, y1=y + 1) for q in RESEARCH_QUERIES]
                found: dict[str, dict[str, Any]] = {}
                dropped = 0
                errors: list[str] = []
                for q in qs:
                    user = (f"Today is {today.isoformat()}. Search: {q}\nList the upcoming events (from today until "
                            f"{(today + timedelta(days=RESEARCH_HORIZON)).isoformat()}) that the results below mention.")
                    try:
                        data, results = await ask_json(self.ask, RESEARCH_SYSTEM, user, search=q, count=8,
                                                       max_tokens=3000, what="events")
                    except AskError as exc:
                        errors.append(str(exc))
                        continue
                    events, n = filter_research(data, results, today)
                    dropped += n
                    for e in events:
                        found.setdefault(e["ext_id"], e)
                if errors and len(errors) == len(qs):
                    raise AskError(errors[0])
                fresh = self._dedupe(self._drop_known(_collapse(list(found.values()))), "web")
                self.tidy_web()
                added, updated = self._upsert(fresh)
                at = datetime.now(UTC).isoformat()
                save_state(self.sf, "research", {"at": at, "added": added, "found": len(found), "dropped": dropped})
                if added or updated:
                    self._publish("research", added=added)
                return {"added": added, "updated": updated, "found": len(found), "dropped": dropped,
                        "errors": errors, "researchedAt": at}
            finally:
                self.state["researching"] = False

    def _drop_known(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """An event CSDb (or the user) already lists is not added again from the web."""
        if not events:
            return []
        with self.sf() as s:
            others = [(name_key(e.name), e.start, e.end) for e in s.scalars(
                select(Event).where(Event.source != "web", Event.end >= self.today() - timedelta(days=1)))]
        out = []
        for e in events:
            k = name_key(e["name"])
            dup = any(len(k) >= 5 and len(ok) >= 5 and (k in ok or ok in k) and e["start"] <= oe and os_ <= e["end"]
                      for ok, os_, oe in others)
            if not dup:
                out.append(e)
        return out

    # ------------------------------------------------------------ 📅 calendars + 🏛 official sites
    def _dedupe(self, events: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
        """Skip events a better source already lists; remove weaker sources' copies of the ones kept."""
        if not events:
            return []
        rank = SOURCE_RANK.get(source, 9)
        with self.sf() as s:
            others = s.scalars(select(Event).where(Event.source != source,
                                                   Event.end >= self.today() - timedelta(days=1))).all()
            keep, weaker = [], []
            for e in events:
                k = _bare_key(e["name"])
                same = [o for o in others if len(k) >= 5 and (k in _bare_key(o.name) or _bare_key(o.name) in k)
                        and e["start"] <= o.end and o.start <= e["end"]]
                if any(SOURCE_RANK.get(o.source, 9) < rank for o in same):
                    continue
                keep.append(e)
                weaker += [o.id for o in same if SOURCE_RANK.get(o.source, 9) > rank and o.source != "user"]
            if weaker:
                s.query(Event).filter(Event.id.in_(weaker)).delete(synchronize_session=False)
                s.commit()
        return keep

    def tidy_web(self) -> int:
        """Web-research hygiene: one row per show (same name without the year, overlapping dates), and no event
        whose source page is about another year (an old article is not this year's date)."""
        removed = 0
        with self.sf() as s:
            rows = s.scalars(select(Event).where(Event.source == "web").order_by(Event.id)).all()
            kept: list[Event] = []
            for e in rows:
                years = {int(y) for y in re.findall(r"\b(20[2-3]\d)\b", f"{e.page_url or ''} {e.url or ''}")}
                if years and e.start.year not in years:
                    s.delete(e)
                    removed += 1
                    continue
                k = _bare_key(e.name)
                if any(len(k) >= 5 and (k in _bare_key(o.name) or _bare_key(o.name) in k)
                       and e.start <= o.end and o.start <= e.end for o in kept):
                    s.delete(e)
                    removed += 1
                    continue
                kept.append(e)
            s.commit()
        return removed

    async def crawl_sources(self) -> dict[str, Any]:
        """📅 Event calendars (demoparty.net's worldwide party list): new parties and changed dates."""
        today = self.today()
        added = updated = 0
        errors: dict[str, str] = {}
        for cal in EVENT_CALENDARS:
            try:
                rows = parse_ics(await self._fetch(cal["url"]))
            except Exception as exc:  # noqa: BLE001 - next time
                errors[cal["label"]] = f"{type(exc).__name__}: {str(exc)[:120]}"
                continue
            items = []
            for r in rows:
                if r["end"] < today or r["start"] > today + timedelta(days=RESEARCH_HORIZON):
                    continue
                city, country = split_location(r.get("location"))
                url = _https(r.get("url"))
                items.append({"source": "dpn", "ext_id": f"dpn:{(r.get('uid') or name_key(r['name']))[:150]}",
                              "name": r["name"], "type": cal["type"], "start": r["start"], "end": r["end"],
                              "city": city, "country": country, "url": url, "page_url": url,
                              "tagline": _plain(r.get("description") or "", 160) or None,
                              "stream_url": url if url and _host(url).endswith(STREAM_HOSTS) else None})
            a, u = self._upsert(self._dedupe(items, "dpn"))
            added, updated = added + a, updated + u
        if added or updated:
            self._publish("calendars", added=added)
        return {"added": added, "updated": updated, "errors": errors}

    async def check_sites(self) -> dict[str, Any]:
        """🏛 The official sites of recurring retro events (VCF, retro gaming expos, Commodore shows…): their next
        dates, read from the page — or by the AI from the page's text when the dates are written unusually."""
        today = self.today()
        found, errors, items = 0, {}, []
        for n, site in enumerate(EVENT_SITES):
            if n and self.detail_gap:                         # (0 in tests)
                await asyncio.sleep(SITE_GAP)
            try:
                page = await self._fetch(site["url"])
            except Exception as exc:  # noqa: BLE001 - blocked or down: next week
                errors[site["name"]] = f"{type(exc).__name__}"
                continue
            dates = find_event_dates(page, today, months=site.get("months"))
            if dates is None and self.ask.provider.configured:
                dates = await self._ai_dates(site, page, today)
            if dates is None:
                continue
            found += 1
            start, end = dates
            name = site["name"] if re.search(r"\b20\d\d\b", site["name"]) else f"{site['name']} {start.year}"
            items.append({"source": "site", "ext_id": f"site:{site['key']}:{start.year}", "name": name,
                          "type": site["type"], "start": start, "end": end, "url": site["url"], "page_url": site["url"],
                          "city": ", ".join(x for x in (site.get("city"), site.get("region")) if x) or None,
                          "country": site["country"], "scope": site.get("scope")})
        a, u = self._upsert(self._dedupe(items, "site"))
        self.tidy_web()
        if a or u:
            self._publish("sites", added=a)
        return {"checked": len(EVENT_SITES), "found": found, "added": a, "updated": u, "errors": errors}

    async def _ai_dates(self, site: dict[str, Any], page: str, today: date) -> tuple[date, date] | None:
        """The AI reads the dates off the page text (the page is ours to fetch; the model only returns dates)."""
        system = ("You read an event website's text and return the dates of the NEXT edition of the event, only if "
                  "the text states them with a year. Reply with one JSON object: {\"start\": \"YYYY-MM-DD\" or \"\", "
                  "\"end\": \"YYYY-MM-DD\" or \"\"}. Empty strings when the next dates aren't announced.")
        user = f"Event: {site['name']}\nToday: {today.isoformat()}\nPage text:\n{page_text(page)}"
        try:
            data, _ = await ask_json(self.ask, system, user, max_tokens=400, what="event dates")
        except AskError:
            return None
        start, end = _iso_date(data.get("start")), _iso_date(data.get("end"))
        end = end or start
        if not start or not end or end < start or end < today or (end - start).days > 14 \
                or start > today + timedelta(days=RESEARCH_HORIZON) or str(start.year) not in page \
                or not near_month(start, site.get("months")):
            return None
        return start, end

    # ------------------------------------------------------------ reading
    def is_live(self, e: Event, today: date | None = None) -> bool:
        t = today or self.today()
        return e.start <= t <= e.end and (e.end - e.start).days <= LIVE_MAX_DAYS

    def to_dict(self, e: Event, today: date | None = None) -> dict[str, Any]:
        t = today or self.today()
        return {"id": e.id, "source": e.source, "sourceLabel": SOURCE_LABELS.get(e.source, e.source), "name": e.name,
                "type": e.type, "start": e.start.isoformat(), "end": e.end.isoformat(), "city": e.city,
                "country": e.country, "region": e.region, "regionName": region_name(e.country, e.region),
                "flag": FLAGS.get(e.country or "", ""), "scope": e.scope or "retro",
                "home": bool(e.country and e.country == self.home()), "url": e.url, "pageUrl": e.page_url,
                "streamUrl": e.stream_url, "image": e.image,
                "tagline": e.tagline, "notes": e.notes, "hidden": e.hidden, "live": self.is_live(e, t),
                "ongoing": e.start <= t <= e.end, "editable": e.source == "user",
                "ics": f"/api/events/{e.id}.ics"}

    def list(self, country: str | None = None, type_: str | None = None, q: str | None = None,
             include_past: bool = False, include_hidden: bool = False, *, date_from: date | None = None,
             date_to: date | None = None, region: str | None = None, scope: str | None = None,
             home_first: bool = False) -> dict[str, Any]:
        today = self.today()
        home = self.home()
        want_country = home if country == "home" else normalize_country(country)
        with self.sf() as s:
            base = select(Event)
            if not include_past:
                base = base.where(Event.end >= today)
            all_rows = s.scalars(base.where(Event.hidden.is_(False))).all()
            stmt = base if include_hidden else base.where(Event.hidden.is_(False))
            if want_country:
                stmt = stmt.where(Event.country == want_country)
            if region:
                stmt = stmt.where(Event.region == region.upper()[:10])
            if scope == "commodore":
                stmt = stmt.where(Event.scope == "commodore")
            elif scope == "retro":
                stmt = stmt.where(or_(Event.scope == "retro", Event.scope.is_(None)))
            if date_from:
                stmt = stmt.where(Event.end >= date_from)
            if date_to:
                stmt = stmt.where(Event.start <= date_to)
            if type_:
                stmt = stmt.where(Event.type.ilike(f"%{type_.strip()[:40]}%"))
            if q:
                like = f"%{q.strip()[:60]}%"
                stmt = stmt.where(or_(Event.name.ilike(like), Event.city.ilike(like), Event.country.ilike(like),
                                      Event.tagline.ilike(like), Event.notes.ilike(like), Event.type.ilike(like)))
            rows = s.scalars(stmt.order_by(Event.start, Event.name).limit(500)).all()
            items = [self.to_dict(e, today) for e in rows]
            if home_first:
                items.sort(key=lambda x: not x["home"])          # stable: home-country events first, by date
            live = [self.to_dict(e, today) for e in all_rows if self.is_live(e, today)]
            hidden = s.query(Event).filter(Event.hidden.is_(True), Event.end >= today).count()
        countries = sorted({e.country for e in all_rows if e.country})
        region_country = want_country or home
        regions = sorted({e.region for e in all_rows if e.region and e.country == region_country})
        types = sorted({t.strip() for e in all_rows if e.type for t in e.type.split(",") if t.strip()})
        return {"items": items, "live": sorted(live, key=lambda x: x["start"]), "liveVideos": self.party_videos(),
                "homeCountry": home, "homeCount": sum(1 for e in all_rows if e.country == home),
                "regionCountry": region_country,
                "regions": [{"code": r, "name": region_name(region_country, r) or r} for r in regions],
                "countries": countries, "types": types, "hiddenCount": hidden, "state": self.status()}

    def party_videos(self, limit: int = 6) -> list[dict[str, Any]]:
        """Recent videos from the demoparty channel (they're in the 🎬 Watch feed)."""
        with self.sf() as s:
            rows = s.scalars(select(NewsItem).where(NewsItem.kind == "video", NewsItem.source.like(f"{PARTY_CHANNEL}%"))
                             .order_by(NewsItem.published_at.desc()).limit(limit)).all()
            return [NewsService.to_dict(n) for n in rows]

    def status(self) -> dict[str, Any]:
        csdb = load_state(self.sf, "csdb")
        research = load_state(self.sf, "research")
        try:
            ai = bool(self.ask.provider.configured)
            web = bool(self.ask.web_search_on)
        except Exception:  # noqa: BLE001
            ai = web = False
        return {"running": self.state["running"], "researching": self.state["researching"],
                "csdbAt": csdb.get("at"), "researchAt": research.get("at"), "errors": csdb.get("errors") or {},
                "aiConfigured": ai, "webSearch": web, "csdbEveryHours": CSDB_EVERY // 3600}

    def get(self, event_id: int) -> Event | None:
        with self.sf() as s:
            e = s.get(Event, event_id)
            if e:
                s.expunge(e)
            return e

    def upcoming(self) -> list[Event]:
        with self.sf() as s:
            rows = s.scalars(select(Event).where(Event.end >= self.today() - timedelta(days=30),
                                                 Event.hidden.is_(False)).order_by(Event.start)).all()
            for e in rows:
                s.expunge(e)
            return list(rows)

    # ------------------------------------------------------------ the user's own events
    @staticmethod
    def _check(values: dict[str, Any]) -> dict[str, Any]:
        out = dict(values)
        for k, n in (("name", 200), ("type", 120), ("city", 120), ("country", 80), ("notes", 2000)):
            if k in out:
                out[k] = _clip(out[k], n) if k != "notes" else ((str(out[k]).strip()[:n] or None) if out[k] else None)
        if "name" in out and not out["name"]:
            raise EventError("The event needs a name")
        if out.get("url"):
            url = _uri(str(out["url"]))
            if not url:
                raise EventError("The website must be an http(s) link")
            out["url"] = url[:600]
        elif "url" in out:
            out["url"] = None
        return out

    def add(self, values: dict[str, Any]) -> dict[str, Any]:
        v = self._check({k: values.get(k) for k in USER_FIELDS})
        start, end = v.get("start"), v.get("end") or v.get("start")
        if not isinstance(start, date):
            raise EventError("The event needs a start date")
        if end < start:
            raise EventError("The event can't end before it starts")
        if (end - start).days > 366:
            raise EventError("That's a very long event — at most a year")
        v["end"] = end
        with self.sf() as s:
            e = Event(source="user", ext_id=f"user:{secrets.token_hex(8)}", **v)
            s.add(e)
            s.commit()
            out = self.to_dict(e)
        self._publish("add", id=out["id"])
        return out

    def edit(self, event_id: int, changes: dict[str, Any]) -> dict[str, Any] | None:
        """Any event can be hidden / shown again; only the user's own events can be edited."""
        with self.sf() as s:
            e = s.get(Event, event_id)
            if e is None:
                return None
            fields = {k: v for k, v in changes.items() if k in USER_FIELDS}
            if fields and e.source != "user":
                raise EventError("Only your own events can be edited — you can hide this one")
            v = self._check(fields)
            start = v.get("start", e.start) or e.start
            end = v.get("end", e.end) or e.end
            if end < start:
                raise EventError("The event can't end before it starts")
            for k, val in v.items():
                if k in ("start", "end"):
                    continue
                setattr(e, k, val)
            e.start, e.end = start, end
            if changes.get("hidden") is not None:
                e.hidden = bool(changes["hidden"])
            s.commit()
            out = self.to_dict(e)
        self._publish("edit", id=event_id)
        return out

    def delete(self, event_id: int) -> bool | None:
        with self.sf() as s:
            e = s.get(Event, event_id)
            if e is None:
                return None
            if e.source != "user":
                raise EventError("Only events you added can be deleted — hide this one instead")
            s.delete(e)
            s.commit()
        self._publish("delete", id=event_id)
        return True
