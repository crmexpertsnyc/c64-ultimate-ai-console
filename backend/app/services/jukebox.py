"""🎵 SID jukebox: C64 music from the High Voltage SID Collection, played on the real SID chip.

* Search: Assembly64 mirrors HVSC (categories 18 music, 19 games, 20 demos; CSDb music is 4). A title
  search is ``(name:"…") & (category:music)``; a composer search is a group-only query (``(group:"Hubbard")``
  matches HVSC's ``Hubbard_Rob``) — combining ``name:"*"`` with a group returns nothing.
* Play: the ``.sid`` is downloaded once into ``DATA_DIR/sidcache/<category>-<id>/`` and uploaded to the
  Ultimate's sidplay runner (``POST /v1/runners:sidplay``). Nothing else on the device is touched.
* Stations: the AI names tunes and composers; each pick is looked up on Assembly64 and only ids that the
  catalog returned are kept (the model never supplies ids or URLs).
"""

from __future__ import annotations

import asyncio
import logging
import re
import struct
from datetime import UTC
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.library.titles import title_key
from app.models.jukebox import JukeboxPlay, JukeboxStation
from app.ultimate.client import UltimateError
from app.ultimate.input import InputUnsupported

from .ai_json import ask_json, strs
from .ask import AskError
from .assembly64 import Assembly64Client, Assembly64Error, category_info, safe_cache_path

log = logging.getLogger("c64.jukebox")

MUSIC_CATEGORIES = (18, 19, 20, 4)          # HVSC music / games / demos, CSDb music
CATEGORY_PREF = {c: n for n, c in enumerate(MUSIC_CATEGORIES)}
DEFAULT_DURATION = 180                      # seconds per track when HVSC's song length isn't known
MAX_SID_BYTES = 512 * 1024
MAX_PICKS = 20
MAX_TRACKS = 30
HISTORY = 30
SUGGESTIONS = [
    "Rob Hubbard classics",
    "Martin Galway's best",
    "Chill demo tunes",
    "Epic game title themes",
    "Jeroen Tel and Maniacs of Noise",
    "Chris Huelsbeck on the SID",
]
_ID = re.compile(r"[A-Za-z0-9_\-]{1,64}")


class JukeboxError(Exception):
    """A user-facing failure with the HTTP status the API should answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- PSID header
def parse_psid(data: bytes) -> dict[str, Any]:
    """PSID / RSID header → {format, version, title, author, released, songs, startSong, load/init/play}.
    Raises ValueError for anything that isn't a SID file."""
    if len(data) < 0x76 or data[:4] not in (b"PSID", b"RSID"):
        raise ValueError("not a PSID/RSID file")
    version, data_offset, load, init, play, songs, start = struct.unpack(">HHHHHHH", data[4:0x12])

    def text(at: int) -> str:
        return data[at:at + 32].split(b"\0", 1)[0].decode("latin-1").strip()

    if data_offset < 0x76 or data_offset > len(data):
        raise ValueError("bad PSID data offset")
    songs = max(1, min(songs, 256))
    return {"format": data[:4].decode("ascii"), "version": version, "dataOffset": data_offset,
            "loadAddress": load, "initAddress": init, "playAddress": play, "songs": songs,
            "startSong": start if 1 <= start <= songs else 1,
            "title": text(0x16), "author": text(0x36), "released": text(0x56)}


# ---------------------------------------------------------------- names
def pretty_title(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").replace("_", " ")).strip()


def pretty_composer(group: str | None) -> str | None:
    """HVSC 'Hubbard_Rob' → 'Rob Hubbard', 'van_Rijn_Ramiro' → 'Ramiro van Rijn'; group names
    ('Maniacs_of_Noise', 'Booze Design') just lose their underscores."""
    if not group:
        return None
    parts = [p for p in str(group).split("_") if p]
    if len(parts) == 2 or (len(parts) > 2 and parts[0][:1].islower()):
        return " ".join([parts[-1], *parts[:-1]])
    return " ".join(parts) or None


def group_token(composer: str) -> str | None:
    """What goes into ``(group:"…")``: an HVSC group name as is, otherwise the surname of 'Rob Hubbard'."""
    c = re.sub(r"[^A-Za-z0-9_\- ]", "", composer or "").strip()[:40]
    if not c:
        return None
    return c if "_" in c else c.split()[-1]


def composer_matches(group: str | None, composer: str | None) -> bool:
    token = group_token(composer or "")
    if not token or not group:
        return False
    return token.split("_")[0].lower() in str(group).lower()


def _aql_text(v: str) -> str:
    return re.sub(r'["()&|]', "", v or "").strip()[:60]


def to_tune(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": str(item["id"]), "category": int(item["category"]), "title": pretty_title(item.get("name", "")),
            "composer": pretty_composer(item.get("group")), "group": item.get("group"),
            "year": item.get("year"), "source": item.get("source"), "rating": item.get("rating") or 0}


def _music_only(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[int, str]] = set()
    out = []
    for it in items:
        if it.get("category") in MUSIC_CATEGORIES and it.get("id") not in (None, "None"):
            key = (int(it["category"]), str(it["id"]))
            if key not in seen:
                seen.add(key)
                out.append(to_tune(it))
    return out


class JukeboxService:
    def __init__(self, container):  # noqa: ANN001
        self.c = container
        self._locks: dict[str, asyncio.Lock] = {}
        self.now_playing: dict[str, Any] | None = None

    @property
    def cache_root(self) -> Path:
        return self.c.settings.data_path / "sidcache"

    def _client(self) -> Assembly64Client:
        if not self.c.catalog.configured:
            raise JukeboxError(409, "The Assembly64 catalog isn't set up — add it under Settings → Online catalog")
        return self.c.catalog.client()

    def status(self) -> dict[str, Any]:
        d = self.c.device
        return {"connected": bool(d.connected), "sidPlayback": bool(d.connected and d.caps.usable("sidPlayback")),
                "catalog": self.c.catalog.configured, "nowPlaying": self.now_playing}

    # ------------------------------------------------------------ search
    async def _name_search(self, client: Assembly64Client, name: str, group: str | None = None,
                           count: int = 40) -> list[dict[str, Any]]:
        return _music_only(await client.search(_aql_text(name), kind="music", group=group, count=count))

    async def _group_search(self, client: Assembly64Client, group: str, count: int = 60) -> list[dict[str, Any]]:
        r = await client._get(f"/leet/search/aql/0/{max(1, min(count, 100))}", {"query": f'(group:"{_aql_text(group)}")'})
        data = r.json()
        items = data if isinstance(data, list) else data.get("entries", data.get("results", []))
        raw = []
        for it in items:
            info = category_info(it.get("category"))
            raw.append({"id": it.get("id"), "category": info["id"], "name": it.get("name") or "",
                        "group": it.get("group") or it.get("handle"), "year": it.get("year") or None,
                        "source": info["source"], "rating": it.get("rating") or 0})
        return _music_only(raw)

    async def search(self, q: str = "", composer: str = "") -> list[dict[str, Any]]:
        q, composer = (q or "").strip()[:60], (composer or "").strip()[:40]
        if not q and not composer:
            return []
        client = self._client()
        token = group_token(composer) if composer else None
        try:
            if q:
                tunes = await self._name_search(client, q, token, count=60)
            else:
                tunes = await self._group_search(client, token or composer)
        except Assembly64Error as exc:
            raise JukeboxError(502, str(exc)) from exc
        return sorted(tunes, key=lambda t: (CATEGORY_PREF.get(t["category"], 9), t["title"].lower()))

    # ------------------------------------------------------------ files
    async def fetch_sid(self, entry_id: str, category: int) -> Path:
        """The entry's .sid file, downloaded once (later plays come from the cache)."""
        folder = self.cache_root / f"{category}-{entry_id}"
        cached = sorted(folder.glob("*.sid")) if folder.is_dir() else []
        if cached:
            return cached[0]
        lock = self._locks.setdefault(f"{category}-{entry_id}", asyncio.Lock())
        async with lock:
            cached = sorted(folder.glob("*.sid")) if folder.is_dir() else []
            if cached:
                return cached[0]
            client = self._client()
            try:
                entries = await client.entries(entry_id, category)
                sids = sorted((e for e in entries if str(e.get("path", "")).lower().endswith(".sid")),
                              key=lambda e: (str(e["path"]).count("/"), str(e["path"]).lower()))
                if not sids:
                    raise JukeboxError(404, "this entry has no .sid file")
                pick = sids[0]
                if int(pick.get("size") or 0) > MAX_SID_BYTES:
                    raise JukeboxError(413, "the .sid file is too large")
                dl = await client.download(entry_id, category, pick["id"], fallback_name=Path(str(pick["path"])).name)
            except Assembly64Error as exc:
                raise JukeboxError(502, str(exc)) from exc
            if len(dl.data) > MAX_SID_BYTES:
                raise JukeboxError(413, "the .sid file is too large")
            try:
                parse_psid(dl.data)
            except ValueError as exc:
                raise JukeboxError(502, f"the download is not a SID tune ({exc})") from exc
            folder.mkdir(parents=True, exist_ok=True)
            name = Path(str(pick["path"])).name
            path = safe_cache_path(folder, name if name.lower().endswith(".sid") else f"{name}.sid")
            path.write_bytes(dl.data)
            return path

    # ------------------------------------------------------------ play / stop
    async def play(self, entry_id: str, category: int, *, title: str | None = None, composer: str | None = None,
                   song: int | None = None, station_id: int | None = None, source: str = "api") -> dict[str, Any]:
        entry_id = str(entry_id).strip()
        if not _ID.fullmatch(entry_id):
            raise JukeboxError(400, "invalid entry id")
        if category not in MUSIC_CATEGORIES:
            raise JukeboxError(400, "only HVSC / CSDb music entries can be played here")
        dev = self.c.device
        if not dev.connected or dev.client is None:
            raise JukeboxError(503, f"C64 Ultimate not connected: {dev.last_error or 'offline'}")
        if not dev.caps.usable("sidPlayback"):
            raise JukeboxError(409, "this Ultimate's firmware doesn't offer SID playback over its REST API")
        path = await self.fetch_sid(entry_id, category)
        info = parse_psid(path.read_bytes())
        if song is not None and not 1 <= song <= info["songs"]:
            raise JukeboxError(400, f"this tune has {info['songs']} song(s)")
        title = (title or info["title"] or path.stem).strip()[:200]
        composer = (composer or pretty_composer(info["author"]) or info["author"] or "").strip()[:120] or None
        try:
            async with self.c.audit.action(source, "jukebox.play", title,
                                           {"id": entry_id, "category": category, "song": song}) as rec:
                await dev.runners.run("sid", local_path=path, songnr=song)
                rec.set_response({"ok": True, "file": path.name})
        except InputUnsupported as exc:
            raise JukeboxError(409, str(exc)) from exc
        except UltimateError as exc:
            raise JukeboxError(502, str(exc)) from exc
        track = {"id": entry_id, "category": category, "title": title, "composer": composer,
                 "released": info["released"] or None, "songs": info["songs"], "startSong": info["startSong"],
                 "song": song or info["startSong"], "durationS": DEFAULT_DURATION, "stationId": station_id}
        with self.c.sf() as s:
            s.add(JukeboxPlay(entry_id=entry_id, category=category, title=title, composer=composer,
                              released=(info["released"] or None) and info["released"][:120], song=track["song"],
                              songs=info["songs"], station_id=station_id))
            s.commit()
        self.now_playing = track
        self.c.hub.publish("jukebox", {"nowPlaying": track})
        return {"ok": True, "track": track, "sid": {k: info[k] for k in ("title", "author", "released", "songs",
                                                                          "startSong", "format")}}

    async def stop_playback(self, source: str = "api") -> dict[str, Any]:
        """SID playback has no "stop" route: the only safe stop is the existing machine reset."""
        dev = self.c.device
        if not dev.connected or dev.client is None:
            raise JukeboxError(503, f"C64 Ultimate not connected: {dev.last_error or 'offline'}")
        if not dev.caps.usable("machineReset"):
            raise JukeboxError(409, "This Ultimate can't be reset over the network — press the C64's reset button "
                                    "to stop the music")
        await dev.release_all_inputs("jukebox stop")
        async with self.c.audit.action(source, "machine.reset", "stop the music", {"reason": "jukebox stop"}) as rec:
            await dev.client.reset()
            rec.set_response({"ok": True})
        dev.caps.record_use("machineReset", True)
        self.now_playing = None
        self.c.hub.publish("jukebox", {"nowPlaying": None})
        return {"ok": True, "reset": True}

    # ------------------------------------------------------------ history
    def history(self, limit: int = HISTORY) -> list[dict[str, Any]]:
        with self.c.sf() as s:
            rows = s.scalars(select(JukeboxPlay).order_by(JukeboxPlay.played_at.desc(), JukeboxPlay.id.desc())
                             .limit(limit)).all()
            return [{"id": r.entry_id, "category": r.category, "title": r.title, "composer": r.composer,
                     "released": r.released, "song": r.song, "songs": r.songs, "stationId": r.station_id,
                     "playedAt": _iso(r.played_at)} for r in rows]

    # ------------------------------------------------------------ 🤖 stations
    async def _resolve(self, client: Assembly64Client, sem: asyncio.Semaphore, title: str,
                       composer: str, expand: int = 3) -> list[dict[str, Any]]:
        """One AI pick → catalog tunes (a titled pick → its best match; a composer-only pick → a few tunes)."""
        async with sem:
            try:
                if not title:
                    token = group_token(composer)
                    tunes = await self._group_search(client, token) if token else []
                    tunes.sort(key=lambda t: (CATEGORY_PREF.get(t["category"], 9), -t["rating"]))
                    return tunes[:expand]
                results = await self._name_search(client, title)
            except Assembly64Error as exc:
                log.info("station pick %r not found: %s", title, exc)
                return []
        key = title_key(title)
        if not key:
            return []
        exact = [t for t in results if title_key(t["title"]) == key]
        loose = [t for t in results if t not in exact and key in title_key(t["title"])
                 and composer_matches(t["group"], composer)]

        def rank(t: dict[str, Any]) -> tuple:
            return (0 if composer_matches(t["group"], composer) else 1, CATEGORY_PREF.get(t["category"], 9),
                    -t["rating"])

        best = sorted(exact, key=rank) + sorted(loose, key=rank)
        return best[:1]

    async def make_station(self, prompt: str) -> dict[str, Any]:
        prompt = (prompt or "").strip()[:200]
        if not prompt:
            raise JukeboxError(400, "describe the station you'd like")
        client = self._client()
        system = (
            "You are a DJ for Commodore 64 SID music from the High Voltage SID Collection (HVSC). Build a themed "
            "radio station for the listener's request. Only name real, well-known SID tunes. Reply with one JSON "
            "object: {\"name\": str (max 50 chars), \"description\": str (1-2 sentences), "
            "\"picks\": [{\"title\": str (the tune's HVSC title, e.g. \"Commando\"), \"composer\": str "
            "(e.g. \"Rob Hubbard\")}] (10-20 picks; a pick with an empty title means \"some tunes by this composer\"), "
            "\"composers\": [str] (HVSC composer folder names in Lastname_Firstname form, e.g. \"Hubbard_Rob\", "
            "\"Galway_Martin\"; max 6)}.")
        data, _ = await ask_json(self.c.ask, system, f"Station request: {prompt}", max_tokens=2500, what="station")
        picks = []
        for p in (data.get("picks") or [])[:MAX_PICKS]:
            if isinstance(p, dict):
                title = strs([p.get("title")], 1, 80)
                composer = strs([p.get("composer")], 1, 60)
                if title or composer:
                    picks.append((title[0] if title else "", composer[0] if composer else ""))
        composers = strs(data.get("composers"), 6, 40)
        sem = asyncio.Semaphore(4)
        batches = await asyncio.gather(*(self._resolve(client, sem, t, c) for t, c in picks))
        tracks: list[dict[str, Any]] = []
        seen: set[tuple[int, str]] = set()

        def add(tunes: list[dict[str, Any]]) -> None:
            for t in tunes:
                if (t["category"], t["id"]) not in seen and len(tracks) < MAX_TRACKS:
                    seen.add((t["category"], t["id"]))
                    tracks.append({k: t[k] for k in ("id", "category", "title", "composer", "year", "source")}
                                  | {"durationS": DEFAULT_DURATION})

        for b in batches:
            add(b)
        if len(tracks) < 8 and composers:           # thin result: top up from the named composers
            extra = await asyncio.gather(*(self._resolve(client, sem, "", c, expand=4) for c in composers))
            for b in extra:
                add(b)
        if not tracks:
            raise AskError("None of the AI's picks could be found in the HVSC catalog — try another idea")
        name = strs([data.get("name")], 1, 60)
        desc = strs([data.get("description")], 1, 400)
        with self.c.sf() as s:
            st = JukeboxStation(name=name[0] if name else prompt[:60], prompt=prompt,
                                description=desc[0] if desc else None, tracks=tracks)
            s.add(st)
            s.commit()
            return self._station(st, full=True) | {"dropped": sum(1 for b in batches if not b)}

    @staticmethod
    def _station(st: JukeboxStation, full: bool = False) -> dict[str, Any]:
        out = {"id": st.id, "name": st.name, "prompt": st.prompt, "description": st.description,
               "trackCount": len(st.tracks or []), "createdAt": _iso(st.created_at)}
        if full:
            out["tracks"] = list(st.tracks or [])
        return out

    def stations(self) -> dict[str, Any]:
        with self.c.sf() as s:
            rows = s.scalars(select(JukeboxStation).order_by(JukeboxStation.created_at.desc(),
                                                             JukeboxStation.id.desc())).all()
            return {"stations": [self._station(r) for r in rows], "suggestions": SUGGESTIONS}

    def station(self, station_id: int) -> dict[str, Any] | None:
        with self.c.sf() as s:
            st = s.get(JukeboxStation, station_id)
            return self._station(st, full=True) if st else None

    def delete_station(self, station_id: int) -> bool:
        with self.c.sf() as s:
            st = s.get(JukeboxStation, station_id)
            if st is None:
                return False
            s.delete(st)
            s.commit()
            return True


def _iso(d) -> str | None:  # noqa: ANN001
    if d is None:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=UTC)).isoformat()


def attach(container) -> JukeboxService:  # noqa: ANN001
    return JukeboxService(container)
