"""Box art from the libretro thumbnails collection (as used by RetroArch).

https://thumbnails.libretro.com/Commodore%20-%2064/ has three folders of PNGs named after the game
(No-Intro style, e.g. "Last Ninja, The (Europe).png"):
    Named_Boxarts – box scans, Named_Titles – title screens, Named_Snaps – in-game shots.
The folder listings are cached for a week; matched images are downloaded once into DATA_DIR/art.
Images remain the property of their publishers; they are shown only in your own library.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from app.library.scanner import normalize_key

log = logging.getLogger("c64.boxart")

BASE = "https://thumbnails.libretro.com/Commodore%20-%2064"
KINDS = {"boxart": "Named_Boxarts", "title": "Named_Titles", "snap": "Named_Snaps"}
INDEX_MAX_AGE = 7 * 86400
MAX_BYTES = 4 * 1024 * 1024

# Prefer the plain original release over variants.
_REGION_RANK = {"(usa, europe)": 0, "(europe)": 1, "(usa)": 2, "(world)": 0}
_VARIANT_WORDS = ("budget", "compilation", "collection", "b-", "beta", "proto", "demo", "sample", "hack",
                  "alt", "bonus", "re-release", "rerelease")


def base_title(filename: str) -> str:
    """'Last Ninja, The (Europe) (Budget).png' → 'The Last Ninja'."""
    name = re.sub(r"\.png$", "", filename, flags=re.I)
    name = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", name).strip()
    m = re.match(r"^(.*),\s*(The|A|An)$", name, flags=re.I)
    if m:
        name = f"{m.group(2)} {m.group(1)}"
    return name.replace("_", " ").strip()


def variant_rank(filename: str) -> tuple[int, int, int]:
    low = filename.lower()
    tags = re.findall(r"\([^)]*\)", low)
    region = min((_REGION_RANK.get(t, 3) for t in tags), default=3)
    variant = sum(1 for t in tags if any(w in t for w in _VARIANT_WORDS))
    return (variant, region, len(tags))


def safe_art_name(kind: str, filename: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", filename.lower().removesuffix(".png")).strip("-")[:90]
    return f"libretro-{kind}-{slug}.png"


class BoxArtService:
    def __init__(self, data_dir_provider):  # noqa: ANN001
        self._data_dir = data_dir_provider
        self._index: dict[str, dict[str, list[str]]] | None = None
        self._lock = asyncio.Lock()

    @property
    def folder(self) -> Path:
        p = self._data_dir() / "art"
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def index(self) -> dict[str, dict[str, list[str]]]:
        """{kind: {normalized title: [filenames]}}, cached on disk for a week."""
        async with self._lock:
            if self._index is not None:
                return self._index
            cache = self.folder / "libretro-index.json"
            if cache.exists() and time.time() - cache.stat().st_mtime < INDEX_MAX_AGE:
                try:
                    self._index = json.loads(cache.read_text(encoding="utf-8"))
                    return self._index
                except ValueError:
                    pass
            index: dict[str, dict[str, list[str]]] = {}
            async with httpx.AsyncClient(timeout=40, follow_redirects=True) as c:
                for kind, folder in KINDS.items():
                    try:
                        r = await c.get(f"{BASE}/{folder}/")
                        r.raise_for_status()
                    except httpx.HTTPError as exc:
                        log.info("libretro index %s unavailable: %s", folder, exc)
                        continue
                    by_title: dict[str, list[str]] = {}
                    for href in re.findall(r'href="([^"]+\.png)"', r.text):
                        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
                        by_title.setdefault(normalize_key(base_title(name)), []).append(name)
                    index[kind] = by_title
            if index:
                cache.write_text(json.dumps(index), encoding="utf-8")
            self._index = index
            return index

    async def find(self, title: str, kinds: tuple[str, ...] = ("boxart", "title")) -> tuple[str, str] | None:
        """Best (kind, filename) for a game title, or None."""
        key = normalize_key(title)
        if not key:
            return None
        idx = await self.index()
        for kind in kinds:
            names = idx.get(kind, {}).get(key)
            if names:
                return kind, sorted(names, key=variant_rank)[0]
        return None

    async def fetch(self, kind: str, filename: str) -> Path | None:
        target = self.folder / safe_art_name(kind, filename)
        if target.exists():
            return target
        url = f"{BASE}/{KINDS[kind]}/{urllib.parse.quote(filename)}"
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                r = await c.get(url)
        except httpx.HTTPError as exc:
            log.info("box art download failed: %s", exc)
            return None
        if r.status_code != 200 or not r.content.startswith(b"\x89PNG") or len(r.content) > MAX_BYTES:
            return None
        target.write_bytes(r.content)
        return target

    async def art_for(self, title: str, alternates: list[str] | None = None) -> tuple[str, Path] | None:
        """Find and download art for a title (or one of its alternate names)."""
        for name in [title, *(alternates or [])]:
            found = await self.find(name)
            if found:
                path = await self.fetch(*found)
                if path:
                    return found[0], path
        return None

    def describe(self) -> dict[str, Any]:
        idx = self._index or {}
        return {kind: len(v) for kind, v in idx.items()}
