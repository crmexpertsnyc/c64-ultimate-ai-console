"""📟 Where the BBS directory comes from. Each source turns a public list into plain records — nothing is invented.

Sources (checked October 2026):
* **SyncTERM directory** — https://syncterm.bbsdev.net/syncterm.lst, the dialing directory the SyncTERM project
  publishes for its users to download. A documented INI file (one ``[Name]`` section per board: ConnectionType,
  Address, Port, Comment, optional ScreenMode). Only telnet entries are used (the relay doesn't do SSH/rlogin).
  On by default (BBS_SOURCE_SYNCTERM).
* **Telnet BBS Guide** — https://www.telnetbbsguide.com, the largest monthly list (``bbslist.csv`` inside the monthly
  ZIP). Its terms ask software authors to request permission before including the list, and forbid merging it into
  another publication without written consent — so it is OFF by default (BBS_SOURCE_TBG) until you have that
  permission (info at telnetbbsguide dot com).
* **The Oasis BBS Commodore BBS Listing** — https://theoasisbbs.com/commodore-bbs-listing/, a hand-kept list of
  active Commodore 64/128 (and Amiga) boards: one small table per board (BBS, Sysop, Running, Telnet). No feed or
  API, and the site is "All Rights Reserved" — OFF by default (BBS_SOURCE_OASIS) until its owners say it's fine.

Compatibility is never guessed: the word "Commodore" or "C64" in a description says nothing about the terminal.
PETSCII becomes "unverified" only when a structured field says so (SyncTERM ``ScreenMode=C64``), the description
literally says PETSCII, or the listed BBS software is a Commodore BBS package. "confirmed" is only ever set by an
admin who tested it.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

UA = "C64-AI-Console/1.0 (BBS directory; monthly)"
SYNCTERM_URL = "https://syncterm.bbsdev.net/syncterm.lst"
TBG_SITE = "https://www.telnetbbsguide.com"
TBG_ZIP = "https://www.telnetbbsguide.com/bbslist/ibbs{mm}{yy}.zip"

# BBS packages written for Commodore 64/128 — they talk PETSCII to callers
COMMODORE_SOFTWARE = ("image bbs", "image 1.", "image 2.", "image 3.", "color 64", "c-net", "cnet", "dmbbs",
                      "cottonwood", "omni 128", "omni-128", "centipede 128", "blue board", "punter bbs", "nissa",
                      "c*base", "c-base", "cbase", "mcbbs", "magnetar")
_PETSCII_WORD = re.compile(r"\bpets?cii\b", re.I)
_HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


class SourceError(Exception):
    pass


@dataclass
class Record:
    name: str
    host: str
    port: int
    source: str
    source_label: str
    source_url: str
    description: str | None = None
    location: str | None = None
    website: str | None = None
    software: str | None = None
    protocol: str = "telnet"
    petscii: str = "unknown"
    ansi: str = "unknown"
    compat_note: str | None = None
    listing_updated: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def normalize_host(host: str) -> str | None:
    """Lower case, no scheme/path/trailing dot; None for anything that isn't a hostname or IP literal."""
    h = (host or "").strip().lower()
    h = re.sub(r"^[a-z]+://", "", h).split("/", 1)[0].rstrip(".")
    if h.startswith("[") and "]" in h:
        h = h[1:h.index("]")]
    if ":" in h and h.count(":") == 1:          # host:port written into the address field
        h = h.split(":", 1)[0]
    if not h:
        return None
    try:
        import ipaddress
        return str(ipaddress.ip_address(h))
    except ValueError:
        pass
    return h if _HOST.match(h) else None


def key(host: str, port: int) -> str:
    return f"{host}:{port}"


def petscii_hint(description: str | None, software: str | None) -> str | None:
    """Why a board might be PETSCII (→ "unverified"), or None. Never from a mere mention of Commodore."""
    sw = (software or "").lower()
    if "amiga" in sw:                         # e.g. "CNet Amiga Pro": an Amiga board talks ANSI, not PETSCII
        sw = ""
    if sw and any(sw.startswith(p) or f" {p}" in f" {sw}" for p in COMMODORE_SOFTWARE):
        return f"runs {software}, a Commodore BBS package"
    if description and _PETSCII_WORD.search(description):
        return "its listing mentions PETSCII"
    return None


async def _get(url: str, http: httpx.AsyncClient | None) -> bytes:
    client = http or httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": UA})
    try:
        r = await client.get(url)
        r.raise_for_status()
        return r.content
    except httpx.HTTPError as exc:
        raise SourceError(f"{url} — {type(exc).__name__}") from exc
    finally:
        if http is None:
            await client.aclose()


# ------------------------------------------------------------------ SyncTERM
def parse_syncterm(text: str, url: str = SYNCTERM_URL) -> list[Record]:
    """The SyncTERM INI list → telnet/raw records (ssh and rlogin entries skipped)."""
    entries: list[dict[str, str]] = []
    cur: dict[str, str] | None = None
    last_key = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith(";"):
            continue
        m = re.match(r"^\[(.+)\]\s*$", line)
        if m:
            cur = {"name": m.group(1).strip()}
            entries.append(cur)
            last_key = ""
            continue
        if cur is None:
            continue
        kv = re.match(r"^\s+([A-Za-z]+)=(.*)$", line)
        if kv:
            last_key = kv.group(1).lower()
            cur[last_key] = kv.group(2).strip()
        elif last_key == "comment":             # a comment that wrapped onto the next line
            cur["comment"] = f"{cur.get('comment', '')} {line.strip()}".strip()
    out: list[Record] = []
    for e in entries:
        kind = e.get("connectiontype", "telnet").strip().lower()
        if kind not in ("telnet", "raw"):
            continue
        host = normalize_host(e.get("address", ""))
        try:
            port = int(e.get("port") or 23)
        except ValueError:
            continue
        if not host or not 1 <= port <= 65535:
            continue
        desc = re.sub(r"\s+", " ", e.get("comment") or "").strip() or None
        rec = Record(name=e["name"][:120], host=host, port=port, source="syncterm", source_label="SyncTERM directory",
                     source_url=url, description=desc, protocol=kind)
        mode = (e.get("screenmode") or "").strip().upper()
        if mode in ("C64", "C128-40", "C128-80"):
            rec.petscii = "unverified"
            rec.compat_note = f"SyncTERM's entry sets ScreenMode={mode} (PETSCII) — not tested here"
        elif (hint := petscii_hint(desc, None)):
            rec.petscii = "unverified"
            rec.compat_note = hint
        else:
            # an entry in an ANSI-BBS terminal's directory, without a C64 screen mode: expected to be ANSI
            rec.ansi = "unverified"
            rec.compat_note = "listed in SyncTERM's directory (an ANSI terminal) — not tested here"
        out.append(rec)
    return out


class SyncTermSource:
    name, label, url = "syncterm", "SyncTERM directory", SYNCTERM_URL
    terms = ("Published by the SyncTERM project as a dialing directory for its users to download. Only names, "
             "addresses and comments are used, and each board links back to the list.")
    setting = "BBS_SOURCE_SYNCTERM"

    async def fetch(self, http: httpx.AsyncClient | None = None) -> list[Record]:
        return parse_syncterm((await _get(self.url, http)).decode("utf-8", "replace"))


# ------------------------------------------------------------------ Telnet BBS Guide
def parse_tbg_csv(text: str, url: str, updated: str | None = None) -> list[Record]:
    out: list[Record] = []
    rows = csv.DictReader(io.StringIO(text), skipinitialspace=True)
    for row in rows:
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        host = normalize_host(row.get("TelnetAddress", ""))
        if not host:
            continue
        try:
            port = int(row.get("bbsPort") or 23)
        except ValueError:
            continue
        sw = row.get("software") or None
        rec = Record(name=(row.get("bbsName") or host)[:120], host=host, port=port, source="tbg",
                     source_label="Telnet BBS Guide", source_url=url, location=row.get("location") or None,
                     website=row.get("WebAddress") or None, software=sw, listing_updated=updated)
        if (hint := petscii_hint(None, sw)):
            rec.petscii, rec.compat_note = "unverified", hint
        out.append(rec)
    return out


class TbgSource:
    name, label, url = "tbg", "Telnet BBS Guide", TBG_SITE
    terms = ("Free monthly list. Its terms ask software authors to request permission before including it and forbid "
             "merging it into another publication without written consent — keep this off unless you have that "
             "permission (info at telnetbbsguide dot com).")
    setting = "BBS_SOURCE_TBG"

    async def fetch(self, http: httpx.AsyncClient | None = None) -> list[Record]:
        now = datetime.now(UTC)
        last = SourceError("no list found")
        for back in (0, 1):                       # this month's list, or last month's early in the month
            y, m = now.year, now.month - back
            if m < 1:
                y, m = y - 1, 12
            url = TBG_ZIP.format(mm=f"{m:02d}", yy=f"{y % 100:02d}")
            try:
                data = await _get(url, http)
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    text = z.read("bbslist.csv").decode("latin-1")
                return parse_tbg_csv(text, TBG_SITE, f"{y}-{m:02d}")
            except (SourceError, zipfile.BadZipFile, KeyError) as exc:
                last = exc if isinstance(exc, SourceError) else SourceError(f"{url} — {exc}")
        raise last


# ------------------------------------------------------------------ The Oasis BBS
OASIS_URL = "https://theoasisbbs.com/commodore-bbs-listing/"


def parse_oasis(page: str, url: str = OASIS_URL) -> list[Record]:
    """The listing's per-board tables (rows "BBS" / "Sysop" / "Running" / "Telnet" / "Website") → records."""
    import html as _html
    updated = re.search(r"Last updated on (\d{1,2})/(\d{1,2})/(\d{4})", page)
    listing = f"{updated.group(3)}-{int(updated.group(1)):02d}-{int(updated.group(2)):02d}" if updated else None
    out: list[Record] = []
    for table in re.findall(r"<table[^>]*tablepress[^>]*>(.*?)</table>", page, re.S | re.I):
        rows: dict[str, str] = {}
        site = None
        for label, value in re.findall(r'<td class="column-1">(.*?)</td>\s*<td class="column-2">(.*?)</td>', table, re.S):
            key = _html.unescape(re.sub(r"<[^>]+>", "", label)).strip().lower()
            if key == "website":
                m = re.search(r'href="(https?://[^"]+)"', value)
                site = m.group(1) if m else None
            rows[key] = _html.unescape(re.sub(r"<[^>]+>", " ", value)).strip()
        name = re.sub(r"\s+", " ", rows.get("bbs", "")).strip()
        addr = rows.get("telnet") or rows.get("telenet") or ""        # one entry spells it "Telenet"
        m = re.match(r"^\s*([A-Za-z0-9.-]+)(?::(\d{1,5}))?\s*$", addr)
        if not name or not m:
            continue
        host = normalize_host(m.group(1))
        port = int(m.group(2) or 23)
        if not host or not 1 <= port <= 65535:
            continue
        sw = re.sub(r"\s+", " ", rows.get("running", "")).strip() or None
        rec = Record(name=name[:120], host=host, port=port, source="oasis", source_label="The Oasis BBS listing",
                     source_url=url, software=sw, website=site, listing_updated=listing)
        if (hint := petscii_hint(None, sw)):
            rec.petscii, rec.compat_note = "unverified", hint
        out.append(rec)
    return out


class OasisSource:
    name, label, url = "oasis", "The Oasis BBS Commodore listing", OASIS_URL
    terms = ("A hand-kept list of active Commodore 64/128 and Amiga boards. There's no feed, and the site is \"All Rights "
             "Reserved\" — switch this on only once The Oasis BBS has said it's fine to use their list.")
    setting = "BBS_SOURCE_OASIS"

    async def fetch(self, http: httpx.AsyncClient | None = None) -> list[Record]:
        recs = parse_oasis((await _get(self.url, http)).decode("utf-8", "replace"))
        if not recs:
            raise SourceError(f"{self.url} — no boards found (the page layout may have changed)")
        return recs


SOURCES = {"syncterm": SyncTermSource(), "tbg": TbgSource(), "oasis": OasisSource()}

# Development fixtures only (never imported by the app): the reserved .invalid TLD can't resolve.
DEV_FIXTURES = [
    Record(name="Example PETSCII Board (fixture)", host="petscii.example.invalid", port=6400, source="fixture",
           source_label="Test fixture", source_url="https://example.invalid/", petscii="unverified"),
    Record(name="Example ANSI Board (fixture)", host="ansi.example.invalid", port=23, source="fixture",
           source_label="Test fixture", source_url="https://example.invalid/", ansi="unverified"),
]
