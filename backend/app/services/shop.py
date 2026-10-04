"""🛒 Hardware shop — a curated catalog that links out to the sellers. The console never sells anything itself.

* Catalog: the built-in ``app/data/shop_catalog.json``, or the one your website publishes (``SHOP_CATALOG_URL``,
  https, refreshed daily). Checkout, seller accounts and affiliate tracking live on the sellers' / your website;
  the console only opens their links. Affiliate links are marked and disclosed.
* Suggestions that know your setup: what a game needs (REU, EasyFlash, mouse, paddles…), what the controller page
  sees (keyboard as joystick → wireless joysticks), what the repair assistant found (parts), what your C64 is.
* 🤖 Starter-kit advisor: the AI picks catalog items for "I just got a C64 Ultimate, what else do I need?".
* eBay: live listings through eBay's Browse API (``EBAY_CLIENT_ID`` / ``EBAY_CLIENT_SECRET``), affiliate-tagged
  with an eBay Partner Network campaign id (``EBAY_CAMPAIGN_ID``). Without keys: plain eBay search links.
  💰 Price watches: a listing at or under your price becomes a "deal" in What's new (the 🆕 badge).
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import re
import statistics
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from sqlalchemy import select

from app.models.db import Game, GameIssue, Media, NewsItem
from app.models.shop import PriceWatch

from .ai_json import ask_json, strs
from .ask import AskError

log = logging.getLogger("c64.shop")

BUILTIN = Path(__file__).resolve().parent.parent / "data" / "shop_catalog.json"
CATEGORIES = {
    "power": "⚡ Power", "storage": "💾 Storage & drives", "cartridges-memory": "🧩 Cartridges & memory",
    "video": "📺 Video", "input": "🕹 Joysticks & input", "sound": "🎵 Sound", "repair-parts": "🔧 Repair parts",
    "diagnostics": "🩺 Diagnostics", "accessories": "🎒 Accessories", "kits": "📦 Kits",
}
CATALOG_TTL = 24 * 3600
WATCH_INTERVAL = 6 * 3600
DISCLOSURE = ("Links go to the sellers' own shops. Some links may be affiliate links (marked 🔗): the seller may pay "
              "a small commission at no extra cost to you. Prices and availability are the sellers'.")

# eBay Partner Network rotation ids per marketplace (used in plain search links with a campaign id)
EBAY_SITES = {
    "EBAY_US": ("www.ebay.com", "711-53200-19255-0", "USD"), "EBAY_GB": ("www.ebay.co.uk", "710-53481-19255-0", "GBP"),
    "EBAY_DE": ("www.ebay.de", "707-53477-19255-0", "EUR"), "EBAY_FR": ("www.ebay.fr", "709-53476-19255-0", "EUR"),
    "EBAY_IT": ("www.ebay.it", "724-53478-19255-0", "EUR"), "EBAY_ES": ("www.ebay.es", "1185-53479-19255-0", "EUR"),
    "EBAY_CA": ("www.ebay.ca", "706-53473-19255-0", "CAD"), "EBAY_AU": ("www.ebay.com.au", "705-53470-19255-0", "AUD"),
}

# What a game's files, notes and problem reports say it needs → catalog tags
NEEDS = [
    (re.compile(r"\b(reu|1764|1750|ram expansion)\b", re.I), ["reu"], "needs a RAM Expansion Unit (REU)"),
    (re.compile(r"\beasy\s*flash\b", re.I), ["easyflash", "cartridge"], "is an EasyFlash cartridge game"),
    (re.compile(r"\bgeo\s*ram\b", re.I), ["reu"], "uses GeoRAM"),
    (re.compile(r"\b(1351|mouse)\b", re.I), ["mouse"], "is played with a mouse"),
    (re.compile(r"\bpaddles?\b", re.I), ["paddles"], "uses paddles"),
    (re.compile(r"\b(two|2)[- ]player|both joysticks|joystick in port 1\b", re.I), ["joystick"],
     "is for two players (two joysticks)"),
    (re.compile(r"\bsupercpu|scpu\b", re.I), ["supercpu"], "needs a SuperCPU"),
]


def _https(url: Any) -> str | None:
    return url if isinstance(url, str) and urlparse(url).scheme == "https" and urlparse(url).hostname else None


def validate_catalog(data: Any) -> dict[str, Any]:
    """Keep only well-formed entries with https links — a remote catalog can't inject anything else."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("catalog needs an 'items' list")
    sellers = {}
    for s in data.get("sellers") or []:
        if isinstance(s, dict) and s.get("id") and s.get("name"):
            sellers[str(s["id"])[:40]] = {"id": str(s["id"])[:40], "name": str(s["name"])[:80], "url": _https(s.get("url")),
                                          "country": str(s.get("country") or "")[:40], "affiliate": bool(s.get("affiliate")),
                                          "note": str(s.get("note") or "")[:200]}
    items = []
    for it in data["items"]:
        if not isinstance(it, dict) or not it.get("id") or not it.get("name"):
            continue
        links = []
        for ln in it.get("links") or []:
            url = _https(ln.get("url")) if isinstance(ln, dict) else None
            if not url:
                continue
            sid = str(ln.get("seller") or "")[:40]
            price = ln.get("price")
            links.append({"seller": sid, "sellerName": sellers.get(sid, {}).get("name") or sid or urlparse(url).hostname,
                          "url": url, "affiliate": bool(ln.get("affiliate", sellers.get(sid, {}).get("affiliate", False))),
                          "price": float(price) if isinstance(price, int | float) else None,
                          "currency": str(ln.get("currency") or "")[:3] or None})
        if not links:
            continue
        cat = str(it.get("category") or "accessories")
        items.append({"id": str(it["id"])[:60], "name": str(it["name"])[:120],
                      "description": str(it.get("description") or "")[:400],
                      "category": cat if cat in CATEGORIES else "accessories",
                      "tags": [str(t)[:30] for t in (it.get("tags") or []) if str(t).strip()][:20],
                      "image": _https(it.get("image")), "links": links})
    services = []
    for sv in data.get("services") or []:
        url = _https(sv.get("url")) if isinstance(sv, dict) else None
        if url and sv.get("name"):
            services.append({"name": str(sv["name"])[:80], "url": url, "region": str(sv.get("region") or "")[:60],
                             "tags": [str(t)[:30] for t in (sv.get("tags") or [])][:10], "note": str(sv.get("note") or "")[:200]})
    return {"version": str(data.get("version") or "")[:20], "updated": str(data.get("updated") or "")[:20],
            "disclosure": str(data.get("disclosure") or DISCLOSURE)[:600], "sellers": list(sellers.values()),
            "items": items, "services": services}


# Product photos: fetched like a browser would (some shops refuse unknown clients), saved locally.
BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/124.0 Safari/537.36 C64UltimateAIConsole/1.0",
                   "Accept": "text/html,application/xhtml+xml,image/*;q=0.9,*/*;q=0.8", "Accept-Language": "en"}
IMAGE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
_MAGIC = ((b"\xff\xd8\xff", ".jpg"), (b"\x89PNG", ".png"), (b"GIF8", ".gif"), (b"RIFF", ".webp"))
_NOT_PRODUCT = re.compile(r"logo|icon|flag|banner|sprite|payment|paypal|cart|placeholder|avatar|badge|loading", re.I)


def find_product_image(page: str, page_url: str) -> str | None:
    """The product photo of a shop page: og:image / twitter:image, then schema.org product data, then the first
    content <img> that isn't a logo or icon. Relative URLs are resolved against the page (and its <base href>)."""
    base = page_url
    if m := re.search(r'<base[^>]+href=["\']([^"\']+)', page, re.I):
        base = urljoin(page_url, html.unescape(m.group(1)))
    candidates: list[str] = []
    for prop in ("og:image:secure_url", "og:image", "twitter:image"):
        for pat in (rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]*content=["\']([^"\']+)',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{prop}["\']'):
            candidates += re.findall(pat, page, re.I)
    candidates += re.findall(r'"image"\s*:\s*\[?\s*"(https?:[^"]+)"', page)
    candidates += [u for u in re.findall(r'<img[^>]+(?:data-src|src)=["\']([^"\']+\.(?:jpe?g|png|webp)(?:\?[^"\']*)?)["\']',
                                         page, re.I) if not _NOT_PRODUCT.search(u)]
    for c in candidates:
        url = urljoin(base, html.unescape(c.strip()).replace(" ", "%20"))
        if urlparse(url).scheme in ("http", "https") and not _NOT_PRODUCT.search(urlparse(url).path):
            return url
    return None


async def _download_image(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    r = await client.get(url)
    r.raise_for_status()
    data = r.content
    if len(data) > 4_000_000 or len(data) < 500:
        raise ValueError(f"image size {len(data)}")
    ext = next((e for magic, e in _MAGIC if data.startswith(magic)), None)
    if ext is None:
        raise ValueError(f"not an image ({r.headers.get('content-type', '?')})")
    return data, ext


def _when(v: Any) -> datetime | None:
    """A feed's date (RFC 822 or ISO) → aware datetime."""
    if not v:
        return None
    from email.utils import parsedate_to_datetime
    try:
        d = parsedate_to_datetime(str(v))
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def ebay_search_url(query: str, settings, *, sold: bool = False) -> str:  # noqa: ANN001
    """A plain eBay search link (affiliate-tagged when an EPN campaign id is set)."""
    host, rotation, _ = EBAY_SITES.get(settings.EBAY_MARKETPLACE, EBAY_SITES["EBAY_US"])
    url = f"https://{host}/sch/i.html?_nkw={quote_plus(query)}"
    if sold:
        url += "&LH_Sold=1&LH_Complete=1"
    camp = re.sub(r"\D", "", settings.EBAY_CAMPAIGN_ID or "")
    if camp:
        url += f"&mkcid=1&mkrid={rotation}&siteid=0&campid={camp}&toolid=10001&mkevt=1"
    return url


class EbayError(Exception):
    pass


class EbayClient:
    """eBay Browse API (application token, read-only searches)."""
    API = "https://api.ebay.com"

    def __init__(self, settings_provider, http=None):  # noqa: ANN001
        self._settings = settings_provider
        self._http = http                                   # tests inject an httpx.AsyncClient with a mock transport
        self._token: tuple[str, float] | None = None

    @property
    def configured(self) -> bool:
        s = self._settings()
        return bool(s.EBAY_CLIENT_ID and s.EBAY_CLIENT_SECRET)

    async def _client(self) -> httpx.AsyncClient:
        return self._http or httpx.AsyncClient(timeout=15)

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        s = self._settings()
        basic = base64.b64encode(f"{s.EBAY_CLIENT_ID}:{s.EBAY_CLIENT_SECRET}".encode()).decode()
        r = await client.post(f"{self.API}/identity/v1/oauth2/token",
                              headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
                              data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"})
        if r.status_code != 200:
            raise EbayError(f"eBay sign-in failed (HTTP {r.status_code}) — check the eBay app keys in Settings")
        data = r.json()
        self._token = (data["access_token"], time.time() + int(data.get("expires_in", 7200)))
        return self._token[0]

    async def search(self, query: str, *, max_price: float | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if not self.configured:
            raise EbayError("eBay listings need eBay developer keys (Settings → Shop)")
        s = self._settings()
        _, _, currency = EBAY_SITES.get(s.EBAY_MARKETPLACE, EBAY_SITES["EBAY_US"])
        params: dict[str, Any] = {"q": query[:100], "limit": max(1, min(limit, 50))}
        if max_price:
            params["filter"] = f"price:[..{max_price:.2f}],priceCurrency:{currency}"
        client = await self._client()
        try:
            token = await self._get_token(client)
            headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": s.EBAY_MARKETPLACE or "EBAY_US"}
            camp = re.sub(r"\D", "", s.EBAY_CAMPAIGN_ID or "")
            if camp:
                headers["X-EBAY-C-ENDUSERCTX"] = f"affiliateCampaignId={camp}"
            r = await client.get(f"{self.API}/buy/browse/v1/item_summary/search", params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise EbayError(f"eBay unreachable: {type(exc).__name__}") from exc
        finally:
            if self._http is None:
                await client.aclose()
        if r.status_code != 200:
            raise EbayError(f"eBay search failed (HTTP {r.status_code})")
        out = []
        for it in r.json().get("itemSummaries") or []:
            url = _https(it.get("itemAffiliateWebUrl")) or _https(it.get("itemWebUrl"))
            price = (it.get("price") or {})
            if not url or not price.get("value"):
                continue
            out.append({"id": str(it.get("itemId"))[:80], "title": str(it.get("title") or "")[:200],
                        "price": float(price["value"]), "currency": price.get("currency") or currency,
                        "condition": it.get("condition"), "image": _https((it.get("image") or {}).get("imageUrl")),
                        "url": url, "affiliate": bool(it.get("itemAffiliateWebUrl")),
                        "seller": (it.get("seller") or {}).get("username"),
                        "location": (it.get("itemLocation") or {}).get("country"),
                        "buying": it.get("buyingOptions") or []})
        return out


class ShopService:
    def __init__(self, container):  # noqa: ANN001
        self.c = container
        self.ebay = EbayClient(lambda: container.settings)
        self._remote: dict[str, Any] | None = None
        self._remote_at = 0.0
        self._remote_error: str | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ catalog
    @property
    def cache_file(self) -> Path:
        return self.c.settings.data_path / "shop_catalog.json"

    def builtin(self) -> dict[str, Any]:
        return validate_catalog(json.loads(BUILTIN.read_text(encoding="utf-8")))

    async def refresh_remote(self, force: bool = False) -> None:
        url = _https(self.c.settings.SHOP_CATALOG_URL)
        if not url:
            self._remote = None
            return
        if not force and self._remote and time.time() - self._remote_at < CATALOG_TTL:
            return
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                r = await client.get(url, headers={"Accept": "application/json"})
            r.raise_for_status()
            if len(r.content) > 2_000_000:
                raise ValueError("catalog too large")
            self._remote = validate_catalog(r.json())
            self._remote_at, self._remote_error = time.time(), None
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(json.dumps(self._remote), encoding="utf-8")
        except (httpx.HTTPError, ValueError) as exc:
            self._remote_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            log.info("shop catalog from %s failed: %s", urlparse(url).hostname, self._remote_error)
            if self._remote is None and self.cache_file.exists():      # last good copy
                try:
                    self._remote = validate_catalog(json.loads(self.cache_file.read_text(encoding="utf-8")))
                except (ValueError, OSError):
                    self._remote = None

    # ------------------------------------------------------------ 🖼 product images
    @property
    def image_dir(self) -> Path:
        return self.c.settings.data_path / "shop_images"

    def image_file(self, item_id: str) -> Path | None:
        safe = re.sub(r"[^a-z0-9-]", "", item_id.lower())[:60]
        if not safe or not self.image_dir.exists():
            return None
        return next((p for p in self.image_dir.glob(f"{safe}.*") if p.suffix in IMAGE_EXT.values()), None)

    def _with_images(self, cat: dict[str, Any]) -> dict[str, Any]:
        items = []
        for i in cat["items"]:
            f = self.image_file(i["id"])
            items.append({**i, "imageUrl": f"/api/shop/images/{i['id']}?v={int(f.stat().st_mtime)}" if f else None})
        return {**cat, "items": items}

    async def fetch_images(self, force: bool = False, gap: float = 1.0, limit: int = 80) -> dict[str, int]:
        """Find each product's photo (the website's catalog image, else the seller page's og:image / product data /
        first product photo), download it once and keep it here — no hotlinking, and pages load fast."""
        self.image_dir.mkdir(parents=True, exist_ok=True)
        failed_file = self.image_dir / "_failed.json"
        try:
            failed: dict[str, float] = json.loads(failed_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            failed = {}
        done = skipped = errors = 0
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=BROWSER_HEADERS) as client:
            for item in self.catalog()["items"][:limit]:
                if not force and (self.image_file(item["id"]) or time.time() - failed.get(item["id"], 0) < 7 * 86400):
                    skipped += 1
                    continue
                try:
                    url = item.get("image")
                    if not url:
                        page = await client.get(item["links"][0]["url"])
                        page.raise_for_status()
                        url = find_product_image(page.text[:2_000_000], str(page.url))
                    if not url:
                        raise ValueError("no product image on the page")
                    data, ext = await _download_image(client, url)
                    for old in self.image_dir.glob(f"{item['id']}.*"):
                        old.unlink(missing_ok=True)
                    (self.image_dir / f"{item['id']}{ext}").write_bytes(data)
                    failed.pop(item["id"], None)
                    done += 1
                except (httpx.HTTPError, ValueError) as exc:
                    failed[item["id"]] = time.time()
                    errors += 1
                    log.info("image for %s: %s", item["id"], str(exc)[:120])
                if gap:
                    await asyncio.sleep(gap)
        failed_file.write_text(json.dumps(failed), encoding="utf-8")
        return {"downloaded": done, "skipped": skipped, "failed": errors}

    def catalog(self) -> dict[str, Any]:
        cat = self._with_images(self._remote or self.builtin())
        return {**cat, "source": "website" if self._remote else "built-in", "error": self._remote_error,
                "categories": CATEGORIES, "ebay": {"configured": self.ebay.configured,
                                                   "affiliate": bool(re.sub(r"\D", "", self.c.settings.EBAY_CAMPAIGN_ID or ""))}}

    def items(self, category: str | None = None, tags: list[str] | None = None, q: str | None = None) -> list[dict[str, Any]]:
        out = self.catalog()["items"]
        if category:
            out = [i for i in out if i["category"] == category]
        if tags:
            want = set(tags)
            out = sorted((i for i in out if want & set(i["tags"])), key=lambda i: -len(want & set(i["tags"])))
        if q:
            ql = q.lower()
            out = [i for i in out if ql in (i["name"] + " " + i["description"] + " " + " ".join(i["tags"])).lower()]
        return out

    def device_info(self) -> dict[str, Any]:
        info = getattr(self.c.device, "info", None)
        if info is None:
            return {}
        return {"product": getattr(info, "product", None), "firmwareVersion": getattr(info, "firmware_version", None)}

    # ------------------------------------------------------------ suggestions that know your setup
    def suggest(self, context: str, *, game_id: int | None = None, using: str | None = None,
                tags: list[str] | None = None) -> dict[str, Any]:
        reasons: list[dict[str, Any]] = []
        if context == "game" and game_id:
            reasons = self._game_needs(game_id)
        elif context == "controller":
            if using in ("keyboard", "touch"):
                reasons.append({"why": f"You're playing with the {using} as the joystick — a real joystick (wireless ones "
                                       "work too) makes most games much easier", "tags": ["wireless-joystick", "joystick"]})
            bridge = getattr(self.c.device, "bridge", None)
            if not (bridge and getattr(bridge, "configured", False)):
                reasons.append({"why": "To play your real C64 from this app with a gamepad, a joystick-port bridge or a "
                                       "wireless receiver in the joystick port is needed",
                                "tags": ["wireless-joystick", "gamepad"]})
        elif context == "repair" and tags:
            reasons.append({"why": "Parts and tools for what the repair assistant found", "tags": tags})
        elif context == "setup":
            if "Ultimate" in str(self.device_info().get("product") or ""):
                reasons.append({"why": "Made for the C64 Ultimate", "tags": ["c64-ultimate"]})
            reasons.append({"why": "Essentials most C64 owners end up wanting", "tags": ["joystick", "psu", "sd-storage"]})
        for r in reasons:
            r["items"] = self.items(tags=r["tags"])[:6]
        return {"context": context, "suggestions": [r for r in reasons if r["items"]],
                "services": [s for s in self.catalog()["services"] if tags and set(tags) & set(s["tags"])]}

    def _game_needs(self, game_id: int) -> list[dict[str, Any]]:
        with self.c.sf() as s:
            g = s.get(Game, game_id)
            if g is None:
                return []
            text = [g.title or "", getattr(g, "notes", "") or ""]
            formats = {m.format for m in s.scalars(select(Media).where(Media.game_id == game_id))}
            for i in s.scalars(select(GameIssue).where(GameIssue.game_id == game_id)):
                text += [i.category or "", i.note or "", i.resolution or "", json.dumps(i.diagnostics or {})[:4000],
                         json.dumps(i.analysis or {})[:4000]]
        blob = "\n".join(text)
        out, seen = [], set()
        for rx, tags, why in NEEDS:
            if rx.search(blob) and why not in seen:
                seen.add(why)
                out.append({"why": f"This game {why}", "tags": tags})
        if "crt" in formats and "is an EasyFlash cartridge game" not in seen:
            out.append({"why": "This game is a cartridge image — on a real C64 it runs from a flash cartridge",
                        "tags": ["easyflash", "cartridge"]})
        return out

    # ------------------------------------------------------------ 🤖 starter kit
    async def advisor(self, prompt: str, budget: float | None = None) -> dict[str, Any]:
        items = self.catalog()["items"]
        if not items:
            raise AskError("The catalog is empty")
        lines = "\n".join(
            f"[{i['id']}] {i['name']} ({i['category']}; tags: {', '.join(i['tags'])})"
            + (f" ~{min(ln['price'] for ln in i['links'] if ln['price'])} {i['links'][0]['currency'] or ''}"
               if any(ln["price"] for ln in i["links"]) else "") for i in items)
        info = self.device_info()
        setup = f"Their machine: {info.get('product') or 'unknown'}" + (
            f", firmware {info.get('firmwareVersion')}" if info.get("firmwareVersion") else "")
        system = ("You are a friendly, honest Commodore 64 hardware advisor. Recommend ONLY items from the catalog, by "
                  "their [id]. Prefer fewer, genuinely useful items; say when something is optional. Never invent "
                  "products, prices or links. Reply with one JSON object: {\"intro\": str (2 sentences), \"kit\": "
                  "[{\"id\": str, \"why\": str (max 140 chars), \"priority\": \"must\"|\"nice\"}] (3-8 items), "
                  "\"tips\": [str] (0-4 short tips, e.g. about safe power supplies)}.")
        user = (f"{setup}\nRequest: {prompt.strip()[:400]}"
                + (f"\nBudget: about {budget:.0f}" if budget else "") + f"\n\nCatalog:\n{lines}")
        data, _ = await ask_json(self.c.ask, system, user, max_tokens=1800, what="starter kit")
        by_id = {i["id"]: i for i in items}
        kit = []
        for k in data.get("kit") or []:
            item = by_id.get(str(k.get("id") or "").strip("[] ")) if isinstance(k, dict) else None
            if item and all(x["item"]["id"] != item["id"] for x in kit):
                kit.append({"item": item, "why": strs([k.get("why")], 1, 160)[0] if k.get("why") else "",
                            "priority": "must" if k.get("priority") == "must" else "nice"})
        if not kit:
            raise AskError("The AI model could not make the starter kit: it picked nothing from the catalog")
        return {"intro": strs([data.get("intro")], 1, 400)[0] if data.get("intro") else "",
                "kit": kit, "tips": strs(data.get("tips"), 4, 200)}

    # ------------------------------------------------------------ eBay + 💰 watches
    async def listings(self, query: str, max_price: float | None = None) -> dict[str, Any]:
        s = self.c.settings
        out: dict[str, Any] = {"configured": self.ebay.configured, "searchUrl": ebay_search_url(query, s),
                               "soldUrl": ebay_search_url(query, s, sold=True), "items": []}
        if self.ebay.configured:
            out["items"] = await self.ebay.search(query, max_price=max_price)
        return out

    async def estimate(self, query: str) -> dict[str, Any]:
        """A value guide from current eBay asking prices (eBay's sold-price data needs special API access)."""
        r = await self.listings(query)
        prices = sorted(i["price"] for i in r["items"])
        if len(prices) >= 3:                           # trim the extremes
            cut = max(1, len(prices) // 5)
            prices = prices[cut:-cut] or prices
        return {"value": round(statistics.median(prices), 2) if prices else None,
                "currency": r["items"][0]["currency"] if r["items"] else None, "count": len(r["items"]),
                "basis": f"median of {len(prices)} current eBay asking prices" if prices else None,
                "soldUrl": r["soldUrl"], "configured": r["configured"]}

    def watches(self) -> list[dict[str, Any]]:
        with self.c.sf() as s:
            return [self._watch_dict(w) for w in s.scalars(select(PriceWatch).order_by(PriceWatch.created_at.desc()))]

    @staticmethod
    def _watch_dict(w: PriceWatch) -> dict[str, Any]:
        return {"id": w.id, "query": w.query, "maxPrice": w.max_price, "currency": w.currency, "active": w.active,
                "lowest": w.lowest, "collectionItemId": w.collection_item_id,
                "checkedAt": w.checked_at.isoformat() if w.checked_at else None}

    def add_watch(self, query: str, max_price: float | None, collection_item_id: int | None = None) -> dict[str, Any]:
        _, _, currency = EBAY_SITES.get(self.c.settings.EBAY_MARKETPLACE, EBAY_SITES["EBAY_US"])
        with self.c.sf() as s:
            w = None
            if collection_item_id:
                w = s.scalars(select(PriceWatch).where(PriceWatch.collection_item_id == collection_item_id)).first()
            if w is None:
                w = PriceWatch(query=query.strip()[:120], collection_item_id=collection_item_id, alerted=[])
                s.add(w)
            w.query, w.max_price, w.currency, w.active = query.strip()[:120], max_price, currency, True
            s.commit()
            return self._watch_dict(w)

    def remove_watch(self, watch_id: int | None = None, collection_item_id: int | None = None) -> bool:
        with self.c.sf() as s:
            w = s.get(PriceWatch, watch_id) if watch_id else s.scalars(
                select(PriceWatch).where(PriceWatch.collection_item_id == collection_item_id)).first()
            if w is None:
                return False
            s.delete(w)
            s.commit()
            return True

    async def check_watches(self) -> int:
        """Look for listings at or under each watch's price; new ones become 💰 deals in What's new."""
        if not self.ebay.configured:
            return 0
        with self.c.sf() as s:
            ws = [(w.id, w.query, w.max_price) for w in s.scalars(select(PriceWatch).where(PriceWatch.active.is_(True)))]
        found = 0
        for wid, query, max_price in ws:
            try:
                items = await self.ebay.search(query, max_price=max_price, limit=20)
            except EbayError as exc:
                log.info("price watch %s: %s", wid, exc)
                continue
            with self.c.sf() as s:
                w = s.get(PriceWatch, wid)
                if w is None:
                    continue
                w.checked_at = datetime.now(UTC)
                w.lowest = min((i["price"] for i in items), default=w.lowest)
                seen = list(w.alerted or [])
                for it in items:
                    if max_price is not None and it["price"] > max_price or it["id"] in seen:
                        continue
                    seen.append(it["id"])
                    guid = f"ebay:{it['id']}"
                    if s.scalars(select(NewsItem.id).where(NewsItem.guid == guid)).first():
                        continue
                    s.add(NewsItem(guid=guid, source="ebay", kind="deal", category=None,
                                   title=f"💰 {it['title'][:200]} — {it['price']:.2f} {it['currency']}",
                                   url=it["url"], image=it["image"], author=it.get("seller"),
                                   summary=f"Under your {max_price:.2f} {it['currency']} watch for “{query}”" if max_price
                                   else f"New listing for “{query}”", published_at=datetime.now(UTC)))
                    found += 1
                w.alerted = seen[-300:]
                s.commit()
            await asyncio.sleep(1)
        if found and getattr(self.c, "hub", None):
            self.c.hub.publish("news", {"added": found, "titles": [f"💰 {found} price alert{'s' if found > 1 else ''}"]})
        return found

    async def crawl_products(self) -> dict[str, Any]:
        """🆕 New hardware in the C64 makers' shops (feeds, store APIs, sitemaps — see crawlers.PRODUCT_FEEDS).
        The first time a shop is read, its current range is the baseline (not announced as new)."""
        from .crawlers import PRODUCT_FEEDS, parse_products
        added, errors = 0, {}
        titles: list[str] = []
        async with httpx.AsyncClient(timeout=25, follow_redirects=True,
                                     headers={"User-Agent": "C64UltimateAIConsole/1.0 (+new hardware)"}) as client:
            for feed in PRODUCT_FEEDS:
                try:
                    r = await client.get(feed["url"])
                    r.raise_for_status()
                    products = parse_products(feed, r.content.decode(r.charset_encoding or "utf-8", "replace")[:5_000_000])
                except (httpx.HTTPError, ValueError) as exc:
                    errors[feed["seller"]] = f"{type(exc).__name__}: {str(exc)[:100]}"
                    continue
                source = f"shop:{feed['key']}"
                now = datetime.now(UTC)
                with self.c.sf() as s:
                    known = set(s.scalars(select(NewsItem.guid).where(NewsItem.source == source)))
                    baseline = not known
                    for p in products:
                        guid = p["guid"][:400]
                        if guid in known:
                            continue
                        known.add(guid)
                        published = _when(p.get("published")) or now
                        s.add(NewsItem(guid=guid, source=source, kind="product", category="hardware", title=p["title"],
                                       url=p["url"][:600], image=(p.get("image") or None) and p["image"][:600],
                                       author=feed["seller"], release_type=p.get("price"), summary=p.get("summary") or None,
                                       published_at=published if not baseline else min(published, now - timedelta(days=30)),
                                       fetched_at=now if not baseline else now - timedelta(days=30)))
                        if not baseline:
                            added += 1
                            titles.append(f"{p['title']} ({feed['seller']})")
                    s.commit()
                await asyncio.sleep(1)
        if added and getattr(self.c, "hub", None):
            self.c.hub.publish("news", {"added": added, "titles": [f"🛒 {t}" for t in titles[:3]]})
        return {"added": added, "errors": errors}

    def deals(self, limit: int = 40) -> list[dict[str, Any]]:
        since = datetime.now(UTC) - timedelta(days=30)
        with self.c.sf() as s:
            rows = s.scalars(select(NewsItem).where(NewsItem.kind == "deal", NewsItem.published_at > since)
                             .order_by(NewsItem.published_at.desc()).limit(limit)).all()
            return [self.c.news.to_dict(r) for r in rows]

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if not self.c.settings.NEWS_MONITOR:        # same switch as the other online monitors (off in tests)
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="shop-watches")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                await self.refresh_remote()
                await self.fetch_images()
                await self.check_watches()
            except Exception as exc:  # noqa: BLE001 - keep watching
                log.warning("shop check failed: %s", exc)
            await asyncio.sleep(WATCH_INTERVAL)


def attach(container) -> ShopService:  # noqa: ANN001
    return ShopService(container)
