"""Crawlers for the 🔄 scheduler: event calendars, the official sites of recurring retro events, and the C64 makers'
new-product feeds. Parsers only (no network here) — the services fetch, these turn pages into rows.

Sources were checked by hand (2026-10-03): demoparty.net's ICS is the best event aggregator (worldwide parties);
there is no machine-readable calendar for US retro expos, so their official sites are read weekly and the next
dates are taken from the page (JSON-LD, a countdown script, or a written date range).
"""

from __future__ import annotations

import calendar
import html
import json
import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

# ------------------------------------------------------------------ 📅 event calendars (ICS)
EVENT_CALENDARS = [
    {"key": "demoparty.net", "label": "demoparty.net", "url": "https://www.demoparty.net/demoparties.ical",
     "type": "Demo Party"},
]


def _unfold(text: str) -> list[str]:
    return re.sub(r"\r?\n[ \t]", "", text).splitlines()


def _ics_unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _ics_date(v: str) -> date | None:
    m = re.match(r"(\d{4})(\d{2})(\d{2})", v.strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_ics(text: str) -> list[dict[str, Any]]:
    """VEVENTs → {uid, name, start, end (inclusive), location, url, description}."""
    out, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            if cur.get("name") and cur.get("start"):
                end = cur.get("end_excl")
                if end and cur.get("all_day"):
                    end = end - timedelta(days=1)          # DTEND of all-day events is exclusive
                cur["end"] = max(end or cur["start"], cur["start"])
                cur.pop("end_excl", None)
                cur.pop("all_day", None)
                out.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            head, value = line.split(":", 1)
            name = head.split(";", 1)[0].upper()
            if name == "SUMMARY":
                cur["name"] = _ics_unescape(value).strip()[:200]
            elif name == "UID":
                cur["uid"] = value.strip()[:150]
            elif name == "DTSTART":
                cur["start"] = _ics_date(value)
                cur["all_day"] = "VALUE=DATE" in head.upper() or len(value.strip()) == 8
            elif name == "DTEND":
                cur["end_excl"] = _ics_date(value)
            elif name == "LOCATION":
                cur["location"] = _ics_unescape(value).strip()[:300]
            elif name == "URL":
                cur["url"] = value.strip()
            elif name == "DESCRIPTION":
                cur["description"] = _ics_unescape(value).strip()[:2000]
    return out


def split_location(loc: str | None) -> tuple[str | None, str | None]:
    """"ORWOhaus, Frank-Zappa-Straße 19, 12681 Berlin, Germany" → ("Berlin", "Germany");
    "Pittsburgh, PA, USA" → ("Pittsburgh, PA", "USA") (the state is picked out later)."""
    if not loc:
        return None, None
    parts = [p.strip() for p in loc.split(",") if p.strip()]
    if not parts:
        return None, None
    if len(parts) == 1:
        return None, parts[0]
    country = parts[-1]
    rest = parts[:-1]
    if len(rest) >= 2 and re.fullmatch(r"[A-Z]{2}(?:\s+\d{5})?", rest[-1]):        # "City, ST"
        town = re.sub(r"^[A-Z]{0,3}-?\d{3,6}\s+", "", rest[-2])
        return f"{town}, {rest[-1][:2]}", country
    city = re.sub(r"^(?:[A-Z]{1,3}-)?\d{3,6}\s+", "", rest[-1])                      # "12681 Berlin" → "Berlin"
    city = re.sub(r"\s+\d{3,6}$", "", city).strip()
    if re.search(r"\d", city) and len(rest) >= 2:                                    # a street, not a city
        city = rest[-2]
    return city or None, country


# ------------------------------------------------------------------ 🏛 official sites of recurring events
# Active recurring shows (checked 2026-10-03). Their next dates are read from the page weekly.
_SITE_FIELDS = ("key", "name", "url", "city", "region", "country", "type", "scope")
EVENT_SITES: list[dict[str, Any]] = [dict(zip(_SITE_FIELDS, row, strict=False)) for row in (
    # key, name, url, city, state / province, country, type[, scope]
    ("vcf-east", "Vintage Computer Festival East",
     "https://vcfed.org/events/vintage-computer-festival-east/",
     "Wall", "NJ", "United States", "Vintage Computer Festival"),
    ("vcf-west", "Vintage Computer Festival West",
     "https://vcfed.org/events/vintage-computer-festival-west/",
     "Mountain View", "CA", "United States", "Vintage Computer Festival"),
    ("vcf-southeast", "Vintage Computer Festival Southeast",
     "https://vcfed.org/events/otherevents/vintage-computer-festival-southeast/",
     "Atlanta", "GA", "United States", "Vintage Computer Festival"),
    ("vcf-midwest", "VCF Midwest",
     "https://vcfmw.org/",
     "Schaumburg", "IL", "United States", "Vintage Computer Festival"),
    ("vcf-southwest", "VCF Southwest",
     "https://www.vcfsw.org/",
     "Dallas–Fort Worth", "TX", "United States", "Vintage Computer Festival"),
    ("vcf-socal", "VCF SoCal",
     "https://www.vcfsocal.com/",
     "Southern California", "CA", "United States", "Vintage Computer Festival"),
    ("vcf-pnw", "VCF Pacific Northwest",
     "https://vcfpnw.org/",
     "Seattle area", "WA", "United States", "Vintage Computer Festival"),
    ("kansasfest", "KansasFest (Apple II)",
     "https://www.kansasfest.org/",
     "Kansas City", "MO", "United States", "Conference"),
    ("cocofest", "Chicago CoCoFEST!",
     "https://www.glensideccc.com/cocofest/",
     "Chicago area", "IL", "United States", "Convention"),
    ("amiwest", "AmiWest (Amiga)",
     "https://www.amiwest.net/",
     "Sacramento", "CA", "United States", "Conference", "commodore"),
    ("pacommex", "Pacific Commodore Expo NW",
     "https://portcommodore.com/dokuwiki/doku.php?id=pacommex:start",
     "Seattle", "WA", "United States", "Convention", "commodore"),
    ("prge", "Portland Retro Gaming Expo",
     "https://www.retrogamingexpo.com/",
     "Portland", "OR", "United States", "Retro Gaming Expo"),
    ("midwest-gaming-classic", "Midwest Gaming Classic",
     "https://www.midwestgamingclassic.com/",
     "Milwaukee area", "WI", "United States", "Retro Gaming Expo"),
    ("retro-world-expo", "Retro World Expo",
     "https://retroworldexpo.com/",
     "Hartford", "CT", "United States", "Retro Gaming Expo"),
    ("southern-fried", "Southern-Fried Gaming Expo",
     "https://gameatl.com/",
     "Atlanta", "GA", "United States", "Retro Gaming Expo"),
    ("toomanygames", "TooManyGames",
     "https://toomanygames.com/",
     "Oaks", "PA", "United States", "Retro Gaming Expo"),
    ("li-retro", "Long Island Retro Gaming Expo",
     "https://liretro.com/",
     "Garden City", "NY", "United States", "Retro Gaming Expo"),
    ("classic-game-fest", "Classic Game Fest",
     "https://classicgamefest.com/",
     "Austin", "TX", "United States", "Retro Gaming Expo"),
    ("texas-pinball", "Texas Pinball Festival",
     "https://texaspinball.com/",
     "Frisco", "TX", "United States", "Arcade & Pinball Show"),
    ("pinball-expo", "Pinball Expo",
     "https://pinballexpo.com/",
     "Chicago", "IL", "United States", "Arcade & Pinball Show"),
    ("california-extreme", "California Extreme",
     "https://caextreme.org/",
     "Santa Clara", "CA", "United States", "Arcade & Pinball Show"),
    ("free-play-florida", "Free Play Florida",
     "https://www.freeplayflorida.com/",
     "Florida", "FL", "United States", "Arcade & Pinball Show"),
    ("world-of-commodore", "World of Commodore",
     "https://woc.tpug.ca/",
     "Toronto area", "ON", "Canada", "Convention", "commodore"),
    ("vcf-europa", "Vintage Computer Festival Europa",
     "https://www.vcfe.org/E/",
     "Munich", None, "Germany", "Vintage Computer Festival"),
    ("classic-computing", "Classic Computing",
     "https://www.classic-computing.de/",
     None, None, "Germany", "Vintage Computer Festival"),
)]

# The month each show is usually held: event sites also list other dates (a swap meet in the sidebar, last year's
# show…) — the next date near the usual month (±1) is the one we want.
SITE_MONTHS: dict[str, tuple[int, ...]] = {
    "vcf-east": (4,), "vcf-west": (8,), "vcf-southeast": (7, 8), "vcf-midwest": (9,), "vcf-southwest": (6,),
    "vcf-socal": (2,), "vcf-pnw": (5,), "kansasfest": (7,), "cocofest": (5,), "amiwest": (10,), "pacommex": (6,),
    "prge": (10,), "retro-world-expo": (9,), "southern-fried": (7,), "toomanygames": (6,), "li-retro": (8,),
    "classic-game-fest": (7,), "texas-pinball": (3,), "pinball-expo": (10,), "california-extreme": (8,),
    "world-of-commodore": (12,), "vcf-europa": (5,), "classic-computing": (9, 10),
}
for _site in EVENT_SITES:
    _site["months"] = SITE_MONTHS.get(_site["key"])

_MONTHS: dict[str, int] = {}
for _i in range(1, 13):
    _MONTHS[calendar.month_name[_i].lower()] = _i
    _MONTHS[calendar.month_abbr[_i].lower()] = _i
_MONTHS.update({"sept": 9, "mai": 5, "okt": 10, "dez": 12, "märz": 3, "juni": 6, "juli": 7})
_MON = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|mai|june?|juni|july?|juli|aug(?:ust)?"
        r"|sept?(?:ember)?|oct(?:ober)?|okt|nov(?:ember)?|dec(?:ember)?|dez)\.?")
_ORD = r"(?:st|nd|rd|th)?"
_DASH = r"\s*(?:-|–|—|&|and|to|through|thru|bis)\s*"
# "October 9-11, 2026" · "July 31 - Aug 2, 2026" · "March 17th-21st, 2027"
_RANGE_MDY = re.compile(rf"\b{_MON}\s+(\d{{1,2}}){_ORD}(?:{_DASH}(?:{_MON}\s+)?(\d{{1,2}}){_ORD})?,?\s+(20\d\d)\b", re.I)
# "21 to 23 August 2026" · "2 - 4 October 2026" · "12 Oct 2026"
_RANGE_DMY = re.compile(rf"\b(\d{{1,2}}){_ORD}\.?(?:\s*{_MON})?(?:{_DASH}(\d{{1,2}}){_ORD}\.?)?\s+{_MON}\s+(20\d\d)\b", re.I)
_LD_DATES = re.compile(r'"startDate"\s*:\s*"(\d{4}-\d{2}-\d{2})[^"]*"(?:[^{}]*?"endDate"\s*:\s*"(\d{4}-\d{2}-\d{2}))?')
_JS_DATE = re.compile(r"""(?:TargetDate\s*=\s*|new\s+Date\(\s*)["'](\d{1,2})/(\d{1,2})/(20\d\d)""", re.I)
_JS_DATE2 = re.compile(rf"""new\s+Date\(\s*["']{_MON}\s+(\d{{1,2}}),?\s+(20\d\d)""", re.I)


def month(tok: str) -> int | None:
    t = tok.lower().rstrip(".")
    return _MONTHS.get(t) or _MONTHS.get(t[:3])


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def near_month(d: date, months: tuple[int, ...] | None) -> bool:
    return not months or any(min((d.month - m) % 12, (m - d.month) % 12) <= 1 for m in months)


def find_event_dates(page: str, today: date, horizon_days: int = 548,
                     months: tuple[int, ...] | None = None) -> tuple[date, date] | None:
    """The next upcoming date range written on an event's site, or None. Looks at schema.org Event data first,
    then countdown scripts, then written ranges ("October 9-11, 2026", "21 to 23 August 2026")."""
    structured: list[tuple[date, date]] = []                 # schema.org Event data
    scripts: list[tuple[date, date]] = []                    # countdown timers ("TargetDate", new Date(...))
    written: list[tuple[date, date]] = []                    # dates in the text
    for blob in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.I | re.S):
        for m in _LD_DATES.finditer(blob):
            s = date.fromisoformat(m.group(1))
            e = date.fromisoformat(m.group(2)) if m.group(2) else s
            structured.append((s, max(s, e)))
    for m in _JS_DATE.finditer(page):
        if d := _mk(int(m.group(3)), int(m.group(1)), int(m.group(2))):
            scripts.append((d, d))
    for m in _JS_DATE2.finditer(page):
        if (mo := month(m.group(1))) and (d := _mk(int(m.group(3)), mo, int(m.group(2)))):
            scripts.append((d, d))
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)))
    text = re.sub(r"\s+", " ", text)
    for m in _RANGE_MDY.finditer(text):
        m1, d1, m2, d2, y = month(m.group(1)), int(m.group(2)), m.group(3), m.group(4), int(m.group(5))
        s = _mk(y, m1, d1) if m1 else None
        e = _mk(y, (month(m2) if m2 else None) or m1, int(d2)) if d2 and m1 else s
        if s and e:
            written.append((s, e if e >= s else s))
    for m in _RANGE_DMY.finditer(text):
        d1, mm1, d2, mm2, y = int(m.group(1)), m.group(2), m.group(3), m.group(4), int(m.group(5))
        m2 = month(mm2)
        m1 = month(mm1) if mm1 else m2
        if not m2:
            continue
        s = _mk(y, m1 or m2, d1)
        e = _mk(y, m2, int(d2)) if d2 else s
        if s and e:
            written.append((s, e if e >= s else s))

    def ok(r: tuple[date, date]) -> bool:
        s, e = r
        return e >= today and s <= today + timedelta(days=horizon_days) and (e - s).days <= 14 and near_month(s, months)

    for tier in (structured, scripts, written):              # the most reliable kind of date wins
        upcoming = [r for r in tier if ok(r)]
        if upcoming:
            s, e = min(upcoming)
            if s == e:                                       # a countdown gives the first day: the text has the range
                e = max((e2 for s2, e2 in written if s2 == s and ok((s2, e2))), default=e)
            return s, e
    return None


def page_text(page: str, limit: int = 6000) -> str:
    """Readable text around the dates on a page (for the AI fallback)."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)))
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"\b20\d\d\b", text)
    start = max(0, (m.start() if m else 0) - 1500)
    return text[start:start + limit]


# ------------------------------------------------------------------ 🛒 new hardware from the makers
C64_RELEVANT = re.compile(r"\b(c64|c-64|c128|commodore|1541|1571|1581|sid|vic-?20|plus/?4|c16|zzap|cbm|ultimate|"
                          r"easyflash|kernal|jiffy|petscii|breadbin|amiga)\b", re.I)

PRODUCT_FEEDS: list[dict[str, Any]] = [
    {"key": "go4retro", "seller": "Retro Innovations", "kind": "bigcommerce-rss",
     "url": "https://store.go4retro.com/rss.php?action=newproducts&type=rss", "filter": False},
    {"key": "vgp", "seller": "VideoGamePerfection", "kind": "woo-store",
     "url": "https://videogameperfection.com/wp-json/wc/store/products?per_page=50&orderby=date&order=desc", "filter": True},
    {"key": "fusion", "seller": "Fusion Retro Books", "kind": "shopify",
     "url": "https://fusionretrobooks.com/products.json?limit=250", "filter": True},
    {"key": "commodore", "seller": "Commodore", "kind": "sitemap",
     "url": "https://commodore.net/product-sitemap.xml", "filter": False},
    {"key": "protovision", "seller": "Protovision", "kind": "protovision-html",
     "url": "https://www.protovision.games/shop/products_new.php?language=en", "filter": False},
]


def _https(url: Any) -> str | None:
    return url if isinstance(url, str) and urlparse(url).scheme in ("http", "https") and urlparse(url).hostname else None


def _plain(fragment: str, limit: int = 300) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def _price(v: Any, currency: str | None) -> str | None:
    try:
        n = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return f"{n:.2f} {currency}".strip() if currency else f"{n:.2f}"


def parse_products(feed: dict[str, Any], body: str) -> list[dict[str, Any]]:
    """One feed → [{guid, title, url, image, price, published (ISO or None), summary}]."""
    kind = feed["kind"]
    out: list[dict[str, Any]] = []
    if kind in ("bigcommerce-rss", "rss"):
        for item in re.findall(r"<item\b.*?</item>", body, re.S | re.I):
            def tag(name: str, item: str = item) -> str:
                m = re.search(rf"<{name}[^>]*>(.*?)</{name}>", item, re.S | re.I)
                return re.sub(r"^<!\[CDATA\[|\]\]>$", "", m.group(1).strip()) if m else ""
            price = tag("isc:price")
            link = html.unescape(tag("link"))
            if not _https(link):
                continue
            img = tag("isc:image") or (m.group(1) if (m := re.search(r'<img[^>]+src="([^"]+)"', tag("description"))) else "")
            out.append({"guid": f"{feed['key']}:{tag('guid') or link}"[:400], "title": _plain(tag("title"), 200), "url": link,
                        "image": _https(html.unescape(img)), "price": _price(price, "USD") if price else None,
                        "published": tag("pubDate") or None, "summary": _plain(tag("description"), 300)})
    elif kind == "woo-store":
        for p in json.loads(body):
            img = (p.get("images") or [{}])[0].get("src") if p.get("images") else None
            prices = p.get("prices") or {}
            minor = int(prices.get("currency_minor_unit") or 2)
            price = _price(int(prices["price"]) / 10 ** minor, prices.get("currency_code")) if prices.get("price") else None
            cats = " ".join(c.get("name", "") for c in p.get("categories") or [])
            out.append({"guid": f"{feed['key']}:{p.get('id')}", "title": _plain(p.get("name", ""), 200),
                        "url": p.get("permalink"),
                        "image": _https(img), "price": price, "published": None, "summary": _plain(cats, 300)})
    elif kind == "shopify":
        for p in json.loads(body).get("products", []):
            v = (p.get("variants") or [{}])[0]
            img = (p.get("images") or [{}])[0].get("src") if p.get("images") else None
            out.append({"guid": f"{feed['key']}:{p.get('id')}", "title": _plain(p.get("title", ""), 200),
                        "url": f"https://{urlparse(feed['url']).hostname}/products/{p.get('handle')}", "image": _https(img),
                        "price": _price(v.get("price"), None), "published": p.get("published_at") or p.get("created_at"),
                        "summary": _plain(" ".join([p.get("product_type") or "", " ".join(p.get("tags") or [])]), 300)})
    elif kind == "sitemap":
        for loc, lastmod in re.findall(r"<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?", body):
            path = urlparse(loc).path.rstrip("/")
            slug = path.rsplit("/", 1)[-1]
            if not slug or path.count("/") < 2:
                continue
            name = re.sub(r"[-_]+", " ", slug).strip().title()
            out.append({"guid": f"{feed['key']}:{loc}", "title": name[:200], "url": loc, "image": None, "price": None,
                        "published": lastmod or None, "summary": ""})
    elif kind == "protovision-html":
        seen = set()
        for pid, name in re.findall(r'product_info\.php\?products_id=(\d+)[^"]*">([^<]{3,120})</a>', body):
            if pid in seen:
                continue
            seen.add(pid)
            out.append({"guid": f"{feed['key']}:{pid}", "title": _plain(name, 200),
                        "url": urljoin(feed["url"], f"/shop/product_info.php?products_id={pid}&language=en"),
                        "image": f"https://www.protovision.games/shop/images/product_images/info_images/{pid}_0.jpg",
                        "price": None, "published": None, "summary": ""})
    out = [p for p in out if p.get("title") and _https(p.get("url"))]
    if feed.get("filter"):
        out = [p for p in out if C64_RELEVANT.search(f"{p['title']} {p.get('summary') or ''}")]
    return out
