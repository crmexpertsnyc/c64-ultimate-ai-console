"""Online C64 game archives beyond Assembly64: search them by title, and fetch a playable file from the ones
that offer downloads, so a game can be played in the browser or on the C64 like any library title.

| Source | Search | Play |
|---|---|---|
| Internet Archive — C64 Software Library + Ultimate Tape Archive | JSON advanced search | download (d64/tap/crt…) |
| C64.com | HTML search | download (zip with the disk image) |
| Games That Weren't 64 (gamesthatwerent.com) | WordPress REST search | download where the entry has one |
| GameBase64, CSDb, Lemon64 | — (Cloudflare / robots.txt / no API) | link out: a search page to open |

Safety: every request goes to a fixed endpoint of one of these sites, built here from a search text or a
source-specific id that is validated first — callers never pass URLs. Downloads must stay on the
source's own hosts (checked after redirects), are size-limited, and are only kept when they contain a
C64 file (see ImportService). Nothing is fetched unless the user searches or presses play.
"""

from __future__ import annotations

import asyncio
import html
import io
import logging
import re
import ssl
import zipfile
from typing import Any
from urllib.parse import quote, quote_plus, urlparse

import httpx

from app.library.titles import title_key

from .catalog import MAX_TOTAL_BYTES

log = logging.getLogger("c64.sources")

UA = {"User-Agent": "C64-AI-Console/0.5 (self-hosted retro console; user-initiated requests)"}
PLAY_EXT = ("d64", "g64", "t64", "prg", "crt", "tap", "d81", "d71", "p00", "zip")

SOURCES = {
    "archive": {"label": "Internet Archive", "home": "https://archive.org/details/softwarelibrary_c64"},
    "c64com": {"label": "C64.com", "home": "https://www.c64.com"},
    "gtw": {"label": "Games That Weren't", "home": "https://www.gamesthatwerent.com/gtw64/"},
    "gb64": {"label": "GameBase64", "home": "https://www.gb64.com"},
    "csdb": {"label": "CSDb", "home": "https://csdb.dk"},
    "lemon64": {"label": "Lemon64", "home": "https://www.lemon64.com"},
}
# Hosts a download may come from (after redirects), per source.
DOWNLOAD_HOSTS = {
    "archive": (".archive.org", "archive.org"),
    "c64com": ("www.c64.com", "c64.com"),
    "gtw": ("www.gamesthatwerent.com", "gamesthatwerent.com"),
}
_ARCHIVE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_GTW_ID = re.compile(r"^\d{1,9}$")
_C64COM_ID = re.compile(r"^\d{1,7}$")


class SourceError(Exception):
    pass


def _ssl_context() -> ssl.SSLContext | bool:
    # The OS certificate store (some archives serve incomplete certificate chains that browsers accept).
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001 - fall back to httpx's bundled CAs
        return True


def _client(timeout: float = 12) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=UA, verify=_ssl_context())


def _host_ok(url: str, source: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or (h.startswith(".") and host.endswith(h)) for h in DOWNLOAD_HOSTS[source])


def _relevant(query: str, title: str) -> bool:
    q, t = title_key(query), title_key(title)
    return bool(q) and (q[:10] in t or t[:10] in q)


def link_outs(query: str) -> list[dict[str, str]]:
    """Search pages to open in a new tab, for sources that can't be searched from here."""
    q = quote_plus(query)
    return [
        {"source": "gb64", "label": "GameBase64", "url": f"https://www.gb64.com/search.php?a=0&f=0&t=2&s={q}&d=18&h=0"},
        {"source": "csdb", "label": "CSDb", "url": f"https://csdb.dk/search/?seinsel=releases&search={q}"},
        # Lemon64 is behind a Cloudflare check and has no search URL to link to reliably: a site web search.
        {"source": "lemon64", "label": "Lemon64", "url": f"https://duckduckgo.com/?q={quote_plus('site:lemon64.com ' + query)}"},
    ]


# ---------------------------------------------------------------- search
async def search_archive(client: httpx.AsyncClient, query: str, rows: int = 10) -> list[dict[str, Any]]:
    words = " ".join(re.findall(r"[A-Za-z0-9]+", query))[:80]
    if not words:
        return []
    q = f"(collection:softwarelibrary_c64 OR collection:ultimatetapearchive) AND title:({words})"
    params = [("q", q), ("rows", str(rows)), ("output", "json"),
              *[("fl[]", f) for f in ("identifier", "title", "year", "date", "creator", "emulator_ext", "collection")]]
    r = await client.get("https://archive.org/advancedsearch.php", params=params)
    r.raise_for_status()
    out = []
    for d in r.json().get("response", {}).get("docs", []):
        ident, title = d.get("identifier", ""), d.get("title", "")
        if not _ARCHIVE_ID.match(ident) or not _relevant(query, title):
            continue
        cols = d.get("collection") or []
        year = str(d.get("year") or d.get("date") or "")[:4]
        creator = d.get("creator")
        out.append({"source": "archive", "id": ident, "title": title,
                    "year": int(year) if year.isdigit() else None,
                    "publisher": creator[0] if isinstance(creator, list) and creator else creator,
                    "note": "Ultimate Tape Archive (tape)" if "ultimatetapearchive" in cols else None,
                    "format": d.get("emulator_ext"), "url": f"https://archive.org/details/{ident}", "playable": True})
    return out


async def search_c64com(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    r = await client.get("https://www.c64.com/games/no-frame.php", params={"searchfor": query[:60], "main": "1"})
    r.raise_for_status()
    t = r.text
    found: dict[str, str] = {}
    for m in re.finditer(r'href="no-frame\.php\?showid=(\d+)&searchfor=[^"]*"[^>]*>\s*<b>(.*?)</b>', t, re.S):
        found.setdefault(m.group(1), html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip())
    if not found:  # a single match opens the game page directly
        m = re.search(r'<span class="headline_1">(.*?)</span>.*?showid=(\d+)', t, re.S)
        if m:
            found[m.group(2)] = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
    out = []
    for gid, title in found.items():
        if not _relevant(query, title):
            continue
        year = pub = None
        m = re.search(rf'searchtype=1&searchfor=(\d{{4}})&show_review=&showid={gid}"', t)
        if m:
            year = int(m.group(1))
            p = re.search(rf'searchtype=1&searchfor=([^&"]+)&show_review=&showid={gid}"[^>]*>'
                          r'<span class="headline_2">([^<]+)', t[m.end():])
            pub = html.unescape(p.group(2)).strip() if p else None
        out.append({"source": "c64com", "id": gid, "title": title, "year": year, "publisher": pub, "note": None,
                    "format": "zip", "url": f"https://www.c64.com/games/{gid}", "playable": True})
    return out


async def search_gtw(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    r = await client.get("https://www.gamesthatwerent.com/wp-json/wp/v2/gtw64",
                         params={"search": query[:60], "per_page": "10", "_fields": "id,title,link,date,tags"})
    r.raise_for_status()
    out = []
    for d in r.json():
        title = html.unescape(re.sub(r"<[^>]+>", "", (d.get("title") or {}).get("rendered", ""))).strip()
        link = d.get("link", "")
        if not _GTW_ID.match(str(d.get("id", ""))) or not _relevant(query, title) or not _host_ok(link, "gtw"):
            continue
        out.append({"source": "gtw", "id": str(d["id"]), "title": title, "year": None, "publisher": None,
                    "note": "unreleased / prototype", "format": None, "url": link, "playable": None})
    return out


async def search_all(query: str) -> dict[str, Any]:
    """Search every searchable archive at once. Failures of one source never hide the others."""
    query = query.strip()[:80]
    if len(title_key(query)) < 2:
        raise SourceError("type a game title")
    async with _client() as client:
        tasks = {"archive": search_archive(client, query), "c64com": search_c64com(client, query),
                 "gtw": search_gtw(client, query)}
        done = await asyncio.gather(*tasks.values(), return_exceptions=True)
    results: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for name, res in zip(tasks, done, strict=True):
        if isinstance(res, Exception):
            log.info("source %s search failed: %s", name, res)
            errors[name] = type(res).__name__
        else:
            results.extend(res[:8])
    want = title_key(query)
    results.sort(key=lambda r: (title_key(r["title"]) != want, not title_key(r["title"]).startswith(want[:10]),
                                ["archive", "c64com", "gtw"].index(r["source"])))
    for r in results:
        r["label"] = SOURCES[r["source"]]["label"]
    return {"query": query, "results": results, "errors": errors, "linkOuts": link_outs(query)}


async def archive_matches(title: str, limit: int = 3) -> list[dict[str, Any]]:
    """The best archive hits for one known title (used by Ask), as "elsewhere" results."""
    try:
        found = await search_all(title)
    except SourceError:
        return []
    want = title_key(title)
    out: list[dict[str, Any]] = []
    for r in found["results"]:
        got = title_key(re.sub(r"\s*[(\[].*$", "", r["title"]))  # "Bubble Bobble (1987)(Firebird)[cr …]" → the name
        rest = got[len(want):] if got.startswith(want) else ""
        sequel = rest[:1].isdigit() or rest in ("ii", "iii", "iv")  # "Lotus Turbo Challenge 2" is another game
        if got != want and not (got.startswith(want) and len(want) >= 6 and not sequel):
            continue
        if any(o["kind"] == r["source"] for o in out):
            continue  # one per archive
        out.append({"kind": r["source"], "id": r["id"], "url": r["url"], "label": r["label"], "title": r["title"],
                    "note": r.get("note")})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- download
async def _get_file(client: httpx.AsyncClient, url: str, source: str) -> tuple[str, bytes]:
    if not _host_ok(url, source):
        raise SourceError("unexpected download location")
    async with client.stream("GET", url) as r:
        final = str(r.url)
        if not _host_ok(final, source):
            raise SourceError("the download left the archive; open it in your browser instead")
        if r.status_code != 200:
            raise SourceError(f"download failed (HTTP {r.status_code})")
        data = bytearray()
        async for chunk in r.aiter_bytes():
            data.extend(chunk)
            if len(data) > MAX_TOTAL_BYTES:
                raise SourceError("the file is too large")
        name = ""
        cd = r.headers.get("content-disposition", "")
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, re.I)
        if m:
            name = m.group(1)
        name = name or urlparse(final).path.rsplit("/", 1)[-1]
    return name, bytes(data)


async def fetch(source: str, ident: str) -> tuple[str, bytes, str]:
    """Download the playable file of one search result. Returns (filename, bytes, page url)."""
    async with _client(timeout=40) as client:
        if source == "archive":
            if not _ARCHIVE_ID.match(ident):
                raise SourceError("invalid Internet Archive id")
            meta = (await client.get(f"https://archive.org/metadata/{ident}")).json()
            files = [f for f in meta.get("files", []) if isinstance(f.get("name"), str)
                     and f["name"].rsplit(".", 1)[-1].lower() in PLAY_EXT]
            if not files:
                raise SourceError("this Internet Archive item has no C64 file")
            want = (meta.get("metadata") or {}).get("emulator_ext")
            files.sort(key=lambda f: (f["name"].rsplit(".", 1)[-1].lower() != want, f.get("source") != "original",
                                      f["name"].lower().startswith("extras/"), f["name"].lower()))
            first = files[0]
            ext = first["name"].rsplit(".", 1)[-1].lower()
            # Several disks / sides of the same kind in one item: fetch them all (as one zip → one title).
            same = [f for f in files if f["name"].rsplit(".", 1)[-1].lower() == ext and ext != "zip"
                    and not f["name"].lower().startswith("extras/") and f.get("source") == first.get("source")][:4]
            got = [await _get_file(client, f"https://archive.org/download/{ident}/{quote(f['name'])}", source)
                   for f in same or [first]]
            page = f"https://archive.org/details/{ident}"
            if len(got) == 1:
                return got[0][0], got[0][1], page
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                for (fname, data), f in zip(got, same, strict=True):
                    z.writestr(f["name"].rsplit("/", 1)[-1] or fname, data)
            return f"{ident}.zip", buf.getvalue(), page
        if source == "c64com":
            if not _C64COM_ID.match(ident):
                raise SourceError("invalid C64.com id")
            name, data = await _get_file(client, f"https://www.c64.com/games/download.php?id={ident}", source)
            return name or f"c64com-{ident}.zip", data, f"https://www.c64.com/games/{ident}"
        if source == "gtw":
            if not _GTW_ID.match(ident):
                raise SourceError("invalid Games That Weren't id")
            post = (await client.get(f"https://www.gamesthatwerent.com/wp-json/wp/v2/gtw64/{ident}",
                                     params={"_fields": "link"})).json()
            link = post.get("link", "")
            if not _host_ok(link, "gtw"):
                raise SourceError("unexpected Games That Weren't page")
            page = (await client.get(link)).text
            tab = page.split('id="tabs-downloads"', 1)[-1] if 'id="tabs-downloads"' in page else ""
            links = re.findall(r'''href=["']?(https://www\.gamesthatwerent\.com/wp-content/uploads/gtw64/[^"'\s>]+)''', tab)
            links = [u for u in links if u.rsplit(".", 1)[-1].lower() in PLAY_EXT]
            if not links:
                raise SourceError("this Games That Weren't entry has no download — open its page instead")
            name, data = await _get_file(client, links[0], source)
            return name, data, link
    raise SourceError("this source is link-out only")
