"""📟🖼 Thumbnails for BBS cards, taken from each board's own public web page — never from the BBS itself.

Where an image can come from, in order:
* the board's listed website;
* otherwise the board's host as a plain web site (http(s)://host/) — many boards run a web front page on the same
  machine. A guessed page is only used when its title / site name shares a distinctive word with the board's name,
  so an unrelated page on a shared host isn't mistaken for the board.
Which image: og:image / twitter:image, then an apple-touch-icon, then the first <img> that looks like the board's
logo, banner or screen shot (not badges, counters, social buttons or tiny spacers).

Safety: every URL (including each redirect) must resolve only to public addresses, like the telnet relay; pages are
read up to 1.5 MB and images up to 3 MB; the file must really be an image
(Pillow decodes it) of at least 48×48, or a 96×24 banner.
The thumbnail is re-encoded as PNG (≤ 320 px wide) and served from this console — no hotlinking.
The page URL the image came from is kept with the board, and the card links to it.
"""

from __future__ import annotations

import html
import io
import re
from urllib.parse import urljoin, urlparse

import httpx

from app.services import bbs_net

UA = {"User-Agent": "Mozilla/5.0 (compatible; C64UltimateAIConsole/1.0; BBS directory thumbnails)",
      "Accept": "text/html,application/xhtml+xml,image/*;q=0.9,*/*;q=0.8"}
MAX_PAGE, MAX_IMAGE, MAX_REDIRECTS = 1_500_000, 3_000_000, 3
THUMB_WIDTH = 320
_JUNK = re.compile(r"badge|counter|valid|w3c|paypal|donate|facebook|twitter|x-logo|discord|youtube|instagram|"
                   r"spacer|pixel|blank|1x1|loading|avatar|emoji|smiley|arrow|bullet|button|banner_ad|ads?/|"
                   r"/images/default/|synchronet|sync_pb|powered|anybrowser|any_browser|anyi\.gif|viewable", re.I)
# alt / title text of images that are about the software or the browser, not the board
_JUNK_TEXT = re.compile(r"powered by|synchronet|mystic|best viewed|any browser|valid (x?html|css)|counter", re.I)
_LIKELY = re.compile(r"logo|banner|header|title|screen|shot|ansi|petscii|bbs|splash|welcome|main", re.I)
_GENERIC = {"bbs", "the", "board", "online", "system", "systems", "bulletin", "node", "telnet", "net", "and", "of",
            "club", "world", "home"}


class ArtError(Exception):
    pass


def distinctive_words(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", name.lower()) if w not in _GENERIC}


def page_matches(page: str, name: str) -> bool:
    """Does a guessed page belong to this board? Its title or site name must share a distinctive word."""
    words = distinctive_words(name)
    if not words:
        return False
    bits = re.findall(r"<title[^>]*>(.*?)</title>", page[:200_000], re.S | re.I)
    bits += re.findall(r'<meta[^>]+(?:property|name)=["\'](?:og:site_name|og:title)["\'][^>]*content=["\']([^"\']+)',
                       page[:200_000], re.I)
    text = " ".join(html.unescape(b) for b in bits).lower()
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _same_site(a: str, b: str) -> bool:
    """example.org and www.example.org count as one site (one is a subdomain of the other); two boards that merely
    share a dynamic-DNS domain (a.dyndns.org, b.dyndns.org) do not."""
    return bool(a and b) and (a.endswith("." + b) or b.endswith("." + a))


def find_board_image(page: str, page_url: str) -> list[str]:
    """Candidate image URLs on a board's web page, best first."""
    base = page_url
    if m := re.search(r'<base[^>]+href=["\']([^"\']+)', page, re.I):
        base = urljoin(page_url, html.unescape(m.group(1)))
    found: list[str] = []
    for prop in ("og:image:secure_url", "og:image", "twitter:image"):
        for pat in (rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]*content=["\']([^"\']+)',
                    rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{prop}["\']'):
            found += re.findall(pat, page, re.I)
    found += re.findall(r'<link[^>]+rel=["\']apple-touch-icon[^"\']*["\'][^>]*href=["\']([^"\']+)', page, re.I)
    page_host = (urlparse(page_url).hostname or "").lower()
    imgs = re.findall(r'<img\b[^>]*>', page, re.I)
    likely, other = [], []
    for tag in imgs:
        # quoted or bare attribute values (plenty of hand-written BBS pages use <img src=logo.gif>)
        src = re.search(r'\s(?:data-src|src)\s*=\s*(?:"([^"]+)"|\'([^\']+)\'|([^\s>]+))', tag, re.I)
        if not src:
            continue
        url = next(g for g in src.groups() if g)
        if url.startswith("data:"):
            continue
        texts = " ".join(m.group(1) for m in re.finditer(r'\s(?:alt|title)\s*=\s*["\']([^"\']*)["\']', tag, re.I))
        if _JUNK_TEXT.search(texts):
            continue
        w = re.search(r'\swidth\s*=\s*["\']?(\d+)', tag, re.I)
        if w and int(w.group(1)) < 48:
            continue
        # a picture in the page's body must be on the board's own site (not a weather widget, counter, banner ad…)
        host = (urlparse(urljoin(base, url)).hostname or "").lower()
        if host != page_host and not _same_site(host, page_host):
            continue
        (likely if _LIKELY.search(url + " " + texts) else other).append(url)
    found += likely + other[:3]
    out: list[str] = []
    for c in found:
        url = urljoin(base, html.unescape(c.strip()).replace(" ", "%20"))
        if urlparse(url).scheme in ("http", "https") and not _JUNK.search(url) and url not in out:
            out.append(url)
    return out


async def _check_url(url: str, resolver: bbs_net.Resolver) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ArtError("not a web address")
    port = p.port or (443 if p.scheme == "https" else 80)
    if port not in (80, 443, 8080, 8000, 8443):
        raise ArtError(f"port {port} isn't used for web pages here")
    try:
        addrs = await resolver(p.hostname.lower(), port)
    except (OSError, TimeoutError) as exc:
        raise ArtError(f"couldn't look up {p.hostname}") from exc
    if not addrs or not all(bbs_net.is_public_address(a) for a in addrs):
        raise ArtError(f"{p.hostname} isn't a public address")


async def fetch_limited(client: httpx.AsyncClient, url: str, limit: int, resolver: bbs_net.Resolver) -> tuple[bytes, str]:
    """GET with every hop checked against the public-address policy and a size cap. → (body, final URL)."""
    for _ in range(MAX_REDIRECTS + 1):
        await _check_url(url, resolver)
        async with client.stream("GET", url) as r:
            if r.is_redirect and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            r.raise_for_status()
            body = bytearray()
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise ArtError("too large")
            return bytes(body), str(r.url)
    raise ArtError("too many redirects")


def make_thumbnail(data: bytes) -> bytes:
    """Decode (proves it's an image), drop tiny ones, shrink to ≤ 320 px wide, re-encode as PNG."""
    from PIL import Image, UnidentifiedImageError
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ArtError("not an image") from exc
    small = (im.width < 48 or im.height < 48) and not (im.width >= 96 and im.height >= 24)   # wide logo banners are fine
    if small or im.width * im.height > 40_000_000:
        raise ArtError(f"image size {im.width}×{im.height}")
    if getattr(im, "is_animated", False):
        im.seek(0)
    im = im.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else im.convert("RGB")
    if im.width > THUMB_WIDTH:
        im.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


async def find_art(name: str, host: str, website: str | None, *, resolver: bbs_net.Resolver = bbs_net.system_resolve,
                   http: httpx.AsyncClient | None = None) -> tuple[bytes, str, str]:
    """→ (PNG thumbnail, image URL, page URL) or ArtError."""
    pages: list[tuple[str, bool]] = []                  # (url, guessed?)
    if website:
        pages.append((website, False))
    pages += [(f"https://{host}/", True), (f"http://{host}/", True)]
    client = http or httpx.AsyncClient(timeout=12, follow_redirects=False, headers=UA)
    last = "no web page"
    try:
        for page_url, guessed in pages:
            try:
                body, final = await fetch_limited(client, page_url, MAX_PAGE, resolver)
            except (ArtError, httpx.HTTPError) as exc:
                last = str(exc)[:100] or type(exc).__name__
                continue
            page = body.decode("utf-8", "replace")
            if guessed and not page_matches(page, name):
                last = "the web page on that host doesn't look like this board's"
                continue
            for img_url in find_board_image(page, final)[:4]:
                try:
                    data, _ = await fetch_limited(client, img_url, MAX_IMAGE, resolver)
                    return make_thumbnail(data), img_url, final
                except (ArtError, httpx.HTTPError) as exc:
                    last = str(exc)[:100] or type(exc).__name__
            if not guessed:
                last = "no usable image on its website"
        raise ArtError(last)
    finally:
        if http is None:
            await client.aclose()
