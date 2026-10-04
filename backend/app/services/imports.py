"""Games from outside the Assembly64 catalog.

* ``ImportService`` adds a game file to the library: a file you downloaded (itch.io, a developer's site,
  Lemon64 …) and dropped into the app, or a direct CSDb download. Files go to DATA_DIR/imports/<name>/,
  are indexed like any library folder, and can then be played on the real C64 or in the browser.
* ``find_elsewhere`` looks a title up on itch.io, CSDb and Lemon64 (via Brave web search) and says what
  each source offers: a CSDb release (fetched automatically — Assembly64 mirrors CSDb under the same id),
  an itch.io page (play/buy/download there, in a new tab), or a Lemon64 page.

Downloads are only ever made from CSDb (https://csdb.dk); other sites are links the user opens.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy import select

from app.library.scanner import scan_root
from app.library.titles import tidy_title, title_key
from app.models.db import Game, Media

from .assembly64 import PLAYABLE_ORDER, safe_cache_path
from .catalog import MAX_TOTAL_BYTES, extract_playable

log = logging.getLogger("c64.imports")

IMPORT_EXT = set(PLAYABLE_ORDER) | {"tap", "p00", "x64", "zip"}
DOWNLOAD_HOSTS = {"csdb.dk", "www.csdb.dk"}


class ImportError_(Exception):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "game"


class ImportService:
    def __init__(self, settings_provider, session_factory, cover_hook=None):  # noqa: ANN001
        self._settings = settings_provider
        self.sf = session_factory
        self.cover_hook = cover_hook
        self._lock = asyncio.Lock()

    @property
    def root(self) -> Path:
        p = self._settings().data_path / "imports"
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def import_bytes(self, filename: str, data: bytes, *, title: str | None = None, source: str = "upload",
                           url: str | None = None) -> int:
        name = Path(filename or "game").name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in IMPORT_EXT:
            raise ImportError_(f"“{name}” is not a C64 file the console can play "
                               f"({', '.join(sorted(IMPORT_EXT))})")
        if not data:
            raise ImportError_("the file is empty")
        if len(data) > MAX_TOTAL_BYTES:
            raise ImportError_("the file is too large")
        digest = hashlib.sha1(data).hexdigest()[:10]
        display = tidy_title(title or Path(name).stem.replace("_", " "))
        folder = self.root / f"{_slug(display)}-{digest}"
        async with self._lock:
            for earlier in self.root.glob(f"*-{digest}"):  # the same file, imported before (any name)
                existing = self._game_in(earlier)
                if existing:
                    return existing
            folder.mkdir(parents=True, exist_ok=True)
            if ext == "zip":
                extracted = await asyncio.to_thread(extract_playable, data, folder)
                if not extracted:
                    raise ImportError_("the archive contains no C64 file the console can play")
            else:
                safe_cache_path(folder, name).write_bytes(data)
            game_id = await asyncio.to_thread(self._index, folder, display, source, url)
        if self.cover_hook is not None:
            try:
                await self.cover_hook(game_id)
            except Exception as exc:  # noqa: BLE001 - art is optional
                log.info("cover art for imported game failed: %s", exc)
        return game_id

    async def import_url(self, url: str, title: str | None = None) -> int:
        """Direct download — CSDb only (other sites are opened by the user, who then drops the file in)."""
        u = urlparse(url)
        if u.scheme != "https" or (u.hostname or "").lower() not in DOWNLOAD_HOSTS:
            raise ImportError_("automatic downloads are only made from CSDb (https://csdb.dk)")
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "C64-AI-Console"}) as client:
            release = _CSDB_RELEASE.match(url)
            if release:  # a release page: ask CSDb's webservice for the release's download file
                url = await _csdb_download_link(client, release.group(1))
            try:
                r = await client.get(url)
            except httpx.HTTPError as exc:
                raise ImportError_(f"download failed: {type(exc).__name__}") from exc
        final = urlparse(str(r.url))
        if (final.hostname or "").lower() not in DOWNLOAD_HOSTS:
            raise ImportError_("the download left CSDb; open it in your browser instead")
        if r.status_code != 200 or not r.content:
            raise ImportError_(f"download failed (HTTP {r.status_code})")
        filename = unquote(final.path.rsplit("/", 1)[-1]) or "game"
        return await self.import_bytes(filename, r.content, title=title, source="csdb", url=url)

    def _game_in(self, folder: Path) -> int | None:
        if not folder.is_dir():
            return None
        with self.sf() as s:
            m = s.scalars(select(Media).where(Media.path.like(f"{folder}%"))).first()
            return m.game_id if m else None

    def _index(self, folder: Path, title: str, source: str, url: str | None) -> int:
        with self.sf() as s:
            scan_root(s, str(self.root))
            media = list(s.scalars(select(Media).where(Media.path.like(f"{folder}%"))))
            if not media:
                raise ImportError_("the file could not be added to the library")
            games = {m.game_id for m in media}
            main = s.get(Game, min(games))
            for gid in games - {main.id}:  # several files of one release → one title
                other = s.get(Game, gid)
                for m in list(other.media):
                    m.game = main
                s.delete(other)
            main.title, main.normalized_title = title, title_key(title)
            main.tags = sorted(set((main.tags or []) + ["imported", source]))
            main.extra = {**(main.extra or {}), "import": {"source": source, "url": url}}
            ordered = sorted(main.media, key=lambda m: m.path.lower())
            if len({m.disk_number for m in ordered}) != len(ordered):
                for n, m in enumerate(ordered, start=1):
                    m.disk_number = n
            main.num_disks = max(len(main.media), main.num_disks or 1)
            s.commit()
            return main.id


# ------------------------------------------------------------- other sources
_ITCH_GAME = re.compile(r"^https://([a-z0-9-]+)\.itch\.io/([a-z0-9-]+)/?$", re.I)
_CSDB_RELEASE = re.compile(r"^https?://(?:www\.)?csdb\.dk/release/\?id=(\d+)", re.I)
_LEMON_GAME = re.compile(r"^https://www\.lemon64\.com/game/[a-z0-9-]+/?$", re.I)


async def find_elsewhere(title: str, brave_key: str, catalog=None, search=None) -> list[dict[str, Any]]:  # noqa: ANN001
    """Where else a C64 game can be found. Each result: {kind, url, label, ...}.
    kind: "csdb" (with ``catalog`` item when Assembly64 has it — playable right away),
          "itch" (open in a new tab: play / buy / download there), "lemon64" (game page)."""
    from .ask import brave_search
    search = search or brave_search
    query = f'"{title}" c64 (site:itch.io OR site:csdb.dk OR site:lemon64.com)'
    results = await search(query, brave_key, count=8)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    want = title_key(title)

    def relevant(r: dict[str, str]) -> bool:
        return want[:12] in title_key(r["title"]) or want[:12] in title_key(r["url"].replace("-", " "))

    for r in results:
        url = r["url"].split("#")[0]
        if url in seen or not relevant(r):
            continue
        m = _CSDB_RELEASE.match(url)
        if m and not any(o["kind"] == "csdb" for o in out):
            seen.add(url)
            item = None
            if catalog is not None and catalog.configured:
                try:
                    files = await catalog.client().entries(m.group(1), 0)
                    if files:
                        item = {"id": m.group(1), "category": 0, "name": title, "group": None, "year": None,
                                "source": "CSDB games", "kind": "game", "rank": 3, "rating": 0}
                except Exception as exc:  # noqa: BLE001 - optional
                    log.info("assembly64 lookup of csdb %s failed: %s", m.group(1), exc)
            out.append({"kind": "csdb", "url": url, "csdbId": m.group(1), "catalog": item,
                        "label": "CSDb release" + ("" if item else " (not mirrored)")})
            continue
        if _ITCH_GAME.match(url) and not any(o["kind"] == "itch" for o in out):
            seen.add(url)
            out.append({"kind": "itch", "url": url, "label": "itch.io", "playable": await _itch_playable(url)})
            continue
        if _LEMON_GAME.match(url) and not any(o["kind"] == "lemon64" for o in out):
            seen.add(url)
            out.append({"kind": "lemon64", "url": url, "label": "Lemon64"})
    return out


async def _csdb_download_link(client: httpx.AsyncClient, release_id: str) -> str:
    try:
        r = await client.get("https://csdb.dk/webservice/", params={"type": "release", "id": release_id, "depth": 2})
    except httpx.HTTPError as exc:
        raise ImportError_(f"CSDb unreachable: {type(exc).__name__}") from exc
    links = re.findall(r"<Link>(.*?)</Link>", r.text)
    playable = [u for u in links if u.rsplit(".", 1)[-1].lower() in IMPORT_EXT
                and (urlparse(u).hostname or "").lower() in DOWNLOAD_HOSTS]
    if not playable:
        raise ImportError_("this CSDb release has no downloadable C64 file on CSDb")
    return playable[0]


async def _itch_playable(url: str) -> bool:
    """Whether an itch.io page has a play-in-browser build (it is then played there, in a new tab)."""
    try:
        async with httpx.AsyncClient(timeout=6, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(url)
        return r.status_code == 200 and "html-classic.itch.zone" in r.text  # the hosted browser build
    except httpx.HTTPError:
        return False
