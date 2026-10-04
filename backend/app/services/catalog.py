"""Online catalog (Assembly64): search, fetch into the local library, and launch.

Downloads go to ``DATA_DIR/assembly64/<category>-<entry id>/`` (the app's own cache), are
checksum-verified when the server sends a checksum, and are then indexed like any other
library folder. Multi-disk releases are downloaded together so disk swapping works.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.library.scanner import scan_root
from app.library.titles import tidy_title, title_key
from app.models.db import Game, Media

from .assembly64 import Assembly64Client, Assembly64Error, choose_files, safe_cache_path

log = logging.getLogger("c64.catalog")

MAX_FILES = 8
MAX_TOTAL_BYTES = 40 * 1024 * 1024


class CatalogService:
    def __init__(self, settings_provider, session_factory: sessionmaker):  # noqa: ANN001
        self._settings = settings_provider
        self.sf = session_factory
        self._locks: dict[str, asyncio.Lock] = {}
        # Optional async callable(game_id) that finds cover art for a new title (set by the container).
        self.cover_hook = None

    @property
    def settings(self) -> Settings:
        return self._settings()

    @property
    def configured(self) -> bool:
        s = self.settings
        return bool(s.ASSEMBLY64_URL and s.ASSEMBLY64_CLIENT_ID)

    def status(self) -> dict[str, Any]:
        s = self.settings
        return {"configured": self.configured, "url": s.ASSEMBLY64_URL, "clientId": s.ASSEMBLY64_CLIENT_ID,
                "cacheDir": str(self.cache_root)}

    @property
    def cache_root(self) -> Path:
        return self.settings.data_path / "assembly64"

    def client(self) -> Assembly64Client:
        if not self.configured:
            raise Assembly64Error("the Assembly64 catalog is not configured (ASSEMBLY64_URL / ASSEMBLY64_CLIENT_ID)")
        return Assembly64Client(self.settings.ASSEMBLY64_URL, self.settings.ASSEMBLY64_CLIENT_ID)

    # ---------------------------------------------------------------- search
    async def search(self, name: str, kind: str | None = "games", offset: int = 0, count: int = 40) -> list[dict[str, Any]]:
        name, group = split_author(name)
        results = await self.client().search(name, kind=None if kind == "all" else kind, group=group,
                                             offset=offset, count=count)
        known = self._known_entries()
        for r in results:
            r["gameId"] = known.get(f"{r['category']}-{r['id']}")
        return results

    async def find_for_play(self, title: str, kind: str, variant: str | None = None,
                            target: str = "c64") -> list[dict[str, Any]]:
        """Online candidates for 'Play <title>', best first. Exact title matches are ranked by
        source (Gamebase / OneLoad64 / C64.com / CSDB for games; HVSC for music).

        target="browser" (the in-browser emulator) prefers one-file cartridge releases (EasyFlash /
        OneLoad64): they start instantly and avoid the intros, trainers and custom disk loaders of
        cracked disk versions, which often need keyboard input or hang in the emulator."""
        name, group = split_author(title)
        client = self.client()
        if kind == "music":
            batches = [await client.search(name, kind="music", group=group, count=60)]
        else:
            # "category:games" only covers CSDB; curated repositories must be asked for explicitly.
            batches = await asyncio.gather(
                client.search(name, repo="gamebase", group=group, count=30),
                client.search(name, repo="oneload", group=group, count=30),
                client.search(name, kind="games", group=group, count=60),
                return_exceptions=True)
            errors = [b for b in batches if isinstance(b, BaseException)]
            batches = [b for b in batches if not isinstance(b, BaseException)]
            if not batches and errors:
                raise errors[0]
        seen: set[tuple] = set()
        results = []
        for batch in batches:
            for r in batch:
                if (r["category"], r["id"]) not in seen:
                    seen.add((r["category"], r["id"]))
                    results.append(r)
        want_kind = "music" if kind == "music" else "game"
        key = title_key(name)
        exact = [r for r in results if title_key(r["name"]) == key and r["kind"] == want_kind]
        loose = [r for r in results if r not in exact and r["kind"] == want_kind and key in title_key(r["name"])]

        literal = name.strip().lower()

        def rank(r: dict[str, Any]) -> tuple:
            year = r.get("year") or 0
            # Same normalised key but different spelling ("Boulder Dash +") ranks after the literal title.
            # Within a source, older (lower) ids first: Gamebase adds originals before later
            # translations/versions (e.g. Bruce Lee #1135 original vs #9805 Italian release).
            numeric_id = int(r["id"]) if str(r["id"]).isdigit() else 1 << 62
            browser_fit = 0 if target != "browser" else (0 if is_one_file_release(r) else 1)
            return (browser_fit, 0 if tidy_title(r["name"]).lower() in (literal, f"the {literal}") else 1,
                    variant_penalty(r, variant), r["rank"],
                    0 if 1980 <= year <= 1995 else 1, -(r.get("rating") or 0), numeric_id)

        return sorted(exact, key=rank) + sorted(loose, key=rank)

    def _known_entries(self) -> dict[str, int]:
        with self.sf() as s:
            out = {}
            for g in s.scalars(select(Game).where(Game.source_root == str(self.cache_root))):
                a = (g.extra or {}).get("assembly64")
                if a:
                    out[f"{a['category']}-{a['id']}"] = g.id
            return out

    # ----------------------------------------------------------------- fetch
    async def fetch(self, entry_id: str, category: int | str, meta: dict[str, Any] | None = None) -> int:
        """Download an entry's playable files into the cache and return its library game id."""
        meta = meta or {}
        key = f"{int(category)}-{re.sub(r'[^A-Za-z0-9]', '', str(entry_id))[:64]}"
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            existing = self._known_entries().get(key)
            if existing:
                return existing
            client = self.client()
            entries = await client.entries(entry_id, category)
            files = choose_files(entries)
            zips = [e for e in entries if str(e.get("path", "")).lower().endswith(".zip")] if not files else []
            if not files and not zips:
                raise Assembly64Error("this entry has no file type the Ultimate can start over REST "
                                      "(TAP images are not supported)")
            folder = self.cache_root / key
            folder.mkdir(parents=True, exist_ok=True)
            total = 0
            for f in files[:MAX_FILES]:
                total += int(f.get("size") or 0)
                if total > MAX_TOTAL_BYTES:
                    raise Assembly64Error("entry is too large to fetch")
                dl = await client.download(entry_id, category, f["id"], fallback_name=str(f.get("path")))
                safe_cache_path(folder, dl.filename).write_bytes(dl.data)
            if zips:
                z = zips[0]
                if int(z.get("size") or 0) > MAX_TOTAL_BYTES:
                    raise Assembly64Error("archive is too large to fetch")
                dl = await client.download(entry_id, category, z["id"], fallback_name=str(z.get("path")))
                extracted = await asyncio.to_thread(extract_playable, dl.data, folder)
                if not extracted:
                    raise Assembly64Error("the archive contains no file type the Ultimate can start over REST")
                files = [{"path": n} for n in extracted]
            (folder / "assembly64.json").write_text(json.dumps(
                {"id": str(entry_id), "category": int(category), **{k: meta.get(k) for k in ("name", "group", "year", "source")},
                 "files": [f.get("path") for f in files]}, indent=2), encoding="utf-8")
            game_id = await asyncio.to_thread(self._index, folder, str(entry_id), int(category), meta)
            if self.cover_hook is not None:
                try:
                    await self.cover_hook(game_id)
                except Exception as exc:  # noqa: BLE001 - art is optional
                    log.info("cover lookup failed: %s", exc)
            return game_id


    def _index(self, folder: Path, entry_id: str, category: int, meta: dict[str, Any]) -> int:
        with self.sf() as s:
            scan_root(s, str(self.cache_root))
            media = list(s.scalars(select(Media).where(Media.path.like(f"{folder}%"))))
            if not media:
                raise Assembly64Error("downloaded files could not be indexed")
            games = {m.game_id for m in media}
            # Several files that the scanner split into separate titles belong to one release.
            main = s.get(Game, min(games))
            for gid in games - {main.id}:
                other = s.get(Game, gid)
                for m in list(other.media):
                    m.game = main
                s.delete(other)
            name = tidy_title(meta.get("name") or main.title)
            main.title = name
            main.normalized_title = title_key(name)  # edition tags like (EasyFlash) don't hurt matching
            if meta.get("group"):
                main.publisher = str(meta["group"]).replace("_", " ")
            if meta.get("year"):
                main.year = int(meta["year"])
            info = SOURCE_CATEGORY.get(category)
            if info:
                main.category = info
            main.tags = sorted(set((main.tags or []) + ["assembly64"] + ([meta["source"]] if meta.get("source") else [])))
            main.extra = {**(main.extra or {}), "assembly64": {"id": entry_id, "category": category}}
            # Releases like Gamebase's SUMMER1A/1B/1C.D64 carry no disk marker: number them in
            # filename order so "Put disk 2 in" works.
            ordered = sorted(main.media, key=lambda m: m.path.lower())
            if len({m.disk_number for m in ordered}) != len(ordered):
                for n, m in enumerate(ordered, start=1):
                    m.disk_number = n
            main.num_disks = max(len(main.media), main.num_disks or 1)
            s.commit()
            return main.id


SOURCE_CATEGORY = {18: "music", 4: "music", 20: "music", 1: "demo", 14: "demo", 31: "demo", 11: "demo",
                   8: "tool", 27: "tool", 28: "tool"}


def split_author(title: str) -> tuple[str, str | None]:
    """'commando by rob hubbard' → ('commando', 'hubbard') — Assembly64 stores HVSC authors as
    'Hubbard_Rob', so the surname is used as the group filter."""
    m = re.match(r"^(.*?)\s+by\s+(.+)$", title.strip(), flags=re.I)
    if not m:
        return title.strip(), None
    author = m.group(2).strip().split()
    return m.group(1).strip(), author[-1] if author else None


LANGUAGE_MARKERS = {
    "german": ("german", "deutsch", "(de)", "[de]", " ger"), "italian": ("italian", "italiano", "(it)", "[it]"),
    "french": ("french", "francais", "français", "(fr)", "[fr]"), "spanish": ("spanish", "espanol", "español", "(es)"),
    "dutch": ("dutch", "(nl)"), "swedish": ("swedish", "(se)"), "polish": ("polish", "polski", "(pl)"),
}


def is_one_file_release(r: dict[str, Any]) -> bool:
    """EasyFlash / OneLoad64 releases: a single cartridge or one-file game, no multi-part loaders."""
    return r.get("category") in (33, 10) or "easyflash" in (r.get("name") or "").lower()


def variant_penalty(r: dict[str, Any], variant: str | None) -> int:
    """0 = matches the requested release, 1 = neutral, 2 = clearly a different language.
    Catalog names only sometimes carry the language, so this is a preference, not a filter."""
    text = f" {r.get('name', '')} {r.get('group') or ''} ".lower()
    langs = [lang for lang, marks in LANGUAGE_MARKERS.items() if any(m in text for m in marks)]
    if not variant:
        return 2 if langs else 1  # unasked-for foreign releases rank last
    if variant in LANGUAGE_MARKERS:
        return 0 if variant in langs else 1
    # english / original / pal / ntsc: prefer entries without a foreign-language marker
    return 2 if langs else 1


def extract_playable(data: bytes, folder: Path, max_files: int = MAX_FILES,
                     max_bytes: int = MAX_TOTAL_BYTES) -> list[str]:
    """Extract only C64-playable files from a ZIP into ``folder`` (flattened, safe names, size-
    and count-limited, so a malformed archive can't write elsewhere or fill the disk)."""
    import io
    import zipfile

    from .assembly64 import PLAYABLE_ORDER
    out: list[str] = []
    total = 0
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise Assembly64Error("download is not a valid ZIP archive") from exc
    members = sorted((i for i in zf.infolist() if not i.is_dir()), key=lambda i: i.filename.lower())
    for info in members:
        ext = Path(info.filename).suffix.lower().lstrip(".")
        if ext not in PLAYABLE_ORDER:
            continue
        if len(out) >= max_files:
            break
        total += info.file_size
        if total > max_bytes:
            raise Assembly64Error("archive contents are too large")
        target = safe_cache_path(folder, Path(info.filename).name)
        with zf.open(info) as src:
            content = src.read(info.file_size + 1)
        if len(content) > info.file_size:  # size header lied
            raise Assembly64Error("archive entry larger than declared")
        target.write_bytes(content)
        out.append(target.name)
    return out
