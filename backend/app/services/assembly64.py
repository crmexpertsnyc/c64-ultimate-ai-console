"""Assembly64 catalog client, plus Spiffy ``server.json`` helpers.

The public Assembly64 catalog (https://hackerswithstyle.se) and Spiffy's Home Assembly 64
server share one HTTP API:

    GET /leet/search/aql/<offset>/<count>?query=<AQL>   → [{id, category, name, group, year, ...}]
    GET /leet/search/aql/presets                        → filter vocabulary (repos, categories, types)
    GET /leet/search/entries/<id>/<category>            → {"contentEntry": [{id, path, size, date}]}
    GET /leet/search/bin/<id>/<category>/<content id>   → raw file (headers: filename, checksum=md5)

Requests carry a ``Client-Id`` header. The public service rejects unknown ids with HTTP 464;
``ASSEMBLY64_CLIENT_ID`` must hold an id it accepts (Spiffy firmware uses ``Spiffy``).

``server.json`` on the Ultimate is only ever **read** (FTP ``RETR``); it is never written.
"""

from __future__ import annotations

import asyncio
import email
import ftplib
import hashlib
import io
import json
import re
from dataclasses import dataclass
from email.policy import default as email_policy
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

# Spiffy documents /flash/config/server.json; the FTP server exposes the flash as /Flash.
SERVER_JSON_PATHS = ("/Flash/config/server.json", "/flash/config/server.json")
SERVER_JSON_PATH = SERVER_JSON_PATHS[0]

DEFAULT_ENDPOINTS = {
    "url-search": "/leet/search/aql/0/100?query=",
    "url-patterns": "/leet/search/aql/presets",
    "url-entries": "/leet/search/entries",
    "url-download": "/leet/search/bin",
}

# Subcategory ids returned in search results (from /leet/search/aql/presets), with the kind of
# content and a launch preference (lower = preferred when several entries share a title).
SUBCATEGORIES: dict[int, tuple[str, str, int]] = {
    16: ("Gamebase64", "game", 0),
    33: ("OneLoad64", "game", 1),
    15: ("C64.com games", "game", 2),
    0: ("CSDB games", "game", 3),
    26: ("Guybrush games", "game", 4),
    29: ("Guybrush games (German)", "game", 5),
    19: ("HVSC games", "music", 6),
    35: ("C64Tapes.org", "game", 9),
    18: ("HVSC music", "music", 0),
    4: ("CSDB music", "music", 1),
    1: ("CSDB demos", "demo", 0),
    14: ("C64.com demos", "demo", 1),
    20: ("HVSC demos", "music", 2),
    31: ("Guybrush demos", "demo", 2),
    11: ("C64.org intros", "demo", 3),
    10: ("CSDB EasyFlash", "game", 6),
    8: ("CSDB tools", "tool", 0),
    27: ("Guybrush utilities", "tool", 1),
    28: ("Guybrush utilities (German)", "tool", 2),
    7: ("CSDB misc", "other", 0),
    30: ("Guybrush misc", "other", 1),
    2: ("CSDB C128", "other", 2),
    3: ("CSDB graphics", "other", 3),
    5: ("CSDB discmags", "other", 4),
    6: ("CSDB BBS", "other", 5),
    9: ("CSDB charts", "other", 6),
    23: ("Preservation disks", "game", 7),
    24: ("Preservation tapes", "game", 8),
}
KIND_TO_AQL = {"games": "games", "music": "music", "demos": "demos", "tools": "tools"}

# File types the Ultimate can start over REST (TAP is not), best first for a single-file launch.
PLAYABLE_ORDER = ["d64", "prg", "crt", "t64", "g64", "d71", "d81", "g71", "sid", "mod"]
DISK_TYPES = {"d64", "g64", "d71", "g71", "d81"}


class Assembly64Error(Exception):
    pass


class Assembly64AuthError(Assembly64Error):
    pass


def category_info(category: int | str) -> dict[str, Any]:
    try:
        cid = int(category)
    except (TypeError, ValueError):
        return {"id": category, "source": str(category), "kind": "other", "rank": 99}
    name, kind, rank = SUBCATEGORIES.get(cid, (f"category {cid}", "other", 99))
    return {"id": cid, "source": name, "kind": kind, "rank": rank}


def build_query(name: str, kind: str | None = None, repo: str | None = None, ftype: str | None = None,
                group: str | None = None) -> str:
    def q(v: str) -> str:
        # quotes and apostrophes break the AQL query (HTTP 500); the catalog stores names without them anyway
        v = re.sub(r"\s+", " ", re.sub(r"[\"'’‘´`]", "", v)).strip()
        return "%" + v[1:].replace("%", "") if v.startswith("%") else v.replace("%", "")   # "%x" = contains x

    parts = [f'(name:"{q(name)}")']
    if kind and kind in KIND_TO_AQL:
        parts.append(f"(category:{KIND_TO_AQL[kind]})")
    if repo:
        parts.append(f"(repo:{re.sub(r'[^a-z0-9]', '', repo.lower())})")
    if ftype:
        parts.append(f"(type:{re.sub(r'[^a-z0-9]', '', ftype.lower())})")
    if group:
        parts.append(f'(group:"{q(group)}")')
    return " & ".join(parts)


def choose_files(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Files to download for a launch: every disk image of a multi-disk release, otherwise the
    single best playable file. Unplayable files (TAP, readme, nfo, …) are skipped."""
    def ext(e: dict[str, Any]) -> str:
        return Path(str(e.get("path", ""))).suffix.lower().lstrip(".")

    playable = [e for e in entries if ext(e) in PLAYABLE_ORDER]
    if not playable:
        return []
    disks = [e for e in playable if ext(e) in DISK_TYPES]
    if len(disks) > 1:
        # Keep one image per disk: prefer D64 over G64 for the same base name.
        by_base: dict[str, dict[str, Any]] = {}
        for e in sorted(disks, key=lambda e: PLAYABLE_ORDER.index(ext(e))):
            by_base.setdefault(Path(str(e["path"])).stem.lower(), e)
        return sorted(by_base.values(), key=lambda e: str(e["path"]).lower())
    return [min(playable, key=lambda e: (PLAYABLE_ORDER.index(ext(e)), str(e.get("path", ""))))]


@dataclass
class DownloadedFile:
    filename: str
    data: bytes
    checksum_ok: bool | None


class Assembly64Client:
    def __init__(self, base_url: str, client_id: str = "", timeout: float = 20.0):
        if not re.match(r"^https?://", base_url or ""):
            raise ValueError("server URL must start with http:// or https://")
        self.base_url = base_url.rstrip("/")
        self.headers = {"Client-Id": client_id} if client_id else {}
        self.timeout = timeout

    async def _get(self, path: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=timeout or self.timeout, follow_redirects=True) as c:
                r = await c.get(f"{self.base_url}{path}", params=params, headers=self.headers)
        except httpx.HTTPError as exc:
            raise Assembly64Error(f"catalog unreachable: {type(exc).__name__}") from exc
        if r.status_code == 464:
            raise Assembly64AuthError("the catalog rejected the Client-Id (HTTP 464); set ASSEMBLY64_CLIENT_ID")
        if r.status_code >= 400:
            raise Assembly64Error(f"catalog HTTP {r.status_code}")
        return r

    async def search(self, name: str, kind: str | None = None, repo: str | None = None, ftype: str | None = None,
                     group: str | None = None, offset: int = 0, count: int = 40) -> list[dict[str, Any]]:
        query = build_query(name, kind, repo, ftype, group)
        r = await self._get(f"/leet/search/aql/{int(offset)}/{max(1, min(int(count), 100))}", {"query": query})
        data = r.json()
        items = data if isinstance(data, list) else data.get("entries", data.get("results", []))
        out = []
        for it in items:
            info = category_info(it.get("category"))
            out.append({"id": str(it.get("id")), "category": info["id"], "name": it.get("name") or "",
                        "group": it.get("group") or it.get("handle"), "year": it.get("year") or None,
                        "released": it.get("released"), "source": info["source"], "kind": info["kind"],
                        "rank": info["rank"], "rating": it.get("rating") or 0})
        return out

    async def entries(self, entry_id: str, category: str | int) -> list[dict[str, Any]]:
        r = await self._get(f"/leet/search/entries/{quote(str(entry_id))}/{quote(str(category))}")
        return list(r.json().get("contentEntry", []))

    async def download(self, entry_id: str, category: str | int, content_id: str | int,
                       fallback_name: str = "download.bin") -> DownloadedFile:
        r = await self._get(f"/leet/search/bin/{quote(str(entry_id))}/{quote(str(category))}/{quote(str(content_id))}",
                            timeout=90)
        ctype = r.headers.get("content-type", "")
        data = r.content
        filename = r.headers.get("filename") or _disposition_name(r.headers.get("content-disposition", "")) or fallback_name
        if ctype.startswith("multipart/"):  # Home Assembly 64 answers with multipart/mixed
            msg = email.message_from_bytes(f"Content-Type: {ctype}\r\n\r\n".encode() + data, policy=email_policy)
            part = next((p for p in msg.iter_parts() if p.get_payload(decode=True)), None)
            if part is None:
                raise Assembly64Error("empty multipart response")
            data = part.get_payload(decode=True)
            filename = part.get_filename() or filename
        checksum = r.headers.get("checksum")
        ok = None if not checksum else hashlib.md5(data).hexdigest().lower() == checksum.lower()  # noqa: S324
        if ok is False:
            raise Assembly64Error(f"checksum mismatch for {filename}")
        return DownloadedFile(filename=filename, data=data, checksum_ok=ok)


# Backwards-compatible name used for Home Assembly 64 servers.
HomeAssemblyClient = Assembly64Client


def _disposition_name(value: str) -> str | None:
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', value or "")
    return m.group(1) if m else None


def safe_name(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._()\-\[\]&,+!']+", "_", Path(filename).name)[:120] or "download.bin"


def safe_cache_path(cache_dir: Path, filename: str) -> Path:
    path = (cache_dir / safe_name(filename)).resolve()
    if cache_dir.resolve() not in path.parents:
        raise ValueError("invalid filename")
    return path


# ------------------------------------------------------------------- server.json
def _ftp_read(host: str, path: str, password: str, timeout: float = 8.0) -> bytes:
    buf = io.BytesIO()
    with ftplib.FTP(timeout=timeout) as ftp:
        ftp.connect(host, 21)
        ftp.login("anonymous", password or "c64console@")
        ftp.retrbinary(f"RETR {path}", buf.write)  # read-only transfer
    return buf.getvalue()


async def read_server_json(host: str, password: str = "") -> dict[str, Any]:
    last_error = ""
    for path in SERVER_JSON_PATHS:
        try:
            raw = await asyncio.to_thread(_ftp_read, host, path, password)
        except ftplib.error_perm as exc:
            last_error = str(exc)
            continue
        except (OSError, ftplib.Error) as exc:
            return {"ok": False, "error": f"FTP error: {exc}"}
        text = raw.decode("utf-8", errors="replace")
        try:
            return {"ok": True, "path": path, "raw": text, "parsed": json.loads(text)}
        except ValueError as exc:
            return {"ok": True, "path": path, "raw": text, "parsed": None, "error": f"invalid JSON: {exc}"}
    return {"ok": False, "missing": True,
            "error": f"no server.json on the device ({last_error}); the firmware uses its built-in "
                     "Assembly64 server settings"}


def generate_server_entry(name: str, host: str, port: int = 8000, client_id: str = "Spiffy") -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9.\-]{1,253}", host):
        raise ValueError("host must be a hostname or IPv4 address")
    if not 1 <= port <= 65535:
        raise ValueError("invalid port")
    return {"name": name[:40] or "Home Assembly", "host": host, "port": port, "client-id": client_id[:40],
            **DEFAULT_ENDPOINTS}


def merged_server_json(existing: dict[str, Any] | None, entry: dict[str, Any]) -> dict[str, Any]:
    servers = list((existing or {}).get("assembly64", []))
    servers = [s for s in servers if not (s.get("host") == entry["host"] and s.get("port") == entry["port"])]
    servers.append(entry)
    return {**(existing or {}), "assembly64": servers}


INSTRUCTIONS = f"""This application does not modify {SERVER_JSON_PATH}. To add the server:
1. Back up the current file: connect with an FTP client to the Ultimate and download {SERVER_JSON_PATH}
   (if it does not exist yet, the firmware is using its built-in defaults).
2. Merge the generated entry into the "assembly64" array (the "merged" preview shows the result).
3. Upload the edited file to {SERVER_JSON_PATH} with your FTP client.
4. Reboot the Ultimate so the firmware re-reads the file.
To run a local server: python spiffy_home_assembly64.py -d /path/to/c64/files  (default port 8000)
See https://github.com/spiffycrew/Spiffy_Home_Assembly_64"""
