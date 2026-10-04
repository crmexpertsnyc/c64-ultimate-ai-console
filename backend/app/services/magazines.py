"""📚 Classic C64 magazines (scanned on the Internet Archive): read them in the app, search them, ask them.

* Issue catalog — read from the Internet Archive's advanced search (one item per issue), or from one item's
  file list when a whole series sits in a single item (Commodore Format: one ``*_djvu.txt`` per issue).
  Cached in ``magazine_issues``; re-read at most once a day.
* Index on demand — "Make searchable" downloads each issue's OCR text (``*_djvu.txt``) one at a time, politely
  spaced, and stores ~1,500-character overlapping chunks in a local SQLite FTS5 index (``data/magazines.db``).
  The Internet Archive's own full-text search is not used. Without FTS5 a plain LIKE search is the fallback.
* Search / 🤖 Ask / 🤖 game reviews — answers come only from the indexed excerpts, each cited.

The scans stay on the Internet Archive: the app links to and embeds its reader, it never re-hosts them.
Every link is built here from Internet Archive identifiers; the AI model never makes URLs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from sqlalchemy import select

from app.models.db import Game
from app.models.magazines import MagazineIssue, MagazineReview, MagazineSeries

from .ai_json import ask_json, strs
from .ask import AskError

log = logging.getLogger("c64.magazines")

IA = "https://archive.org"
UA = {"User-Agent": "C64UltimateAIConsole/1.0 (+magazine archive)"}
CATALOG_MAX_AGE = timedelta(days=1)
GAP = 1.0                       # seconds between Internet Archive requests while indexing
MAX_TEXT = 5_000_000            # OCR texts larger than this are skipped
CHUNK = 1500                    # characters per indexed chunk …
OVERLAP = 200                   # … overlapping the previous one by this much
TOP = 8                         # excerpts handed to the AI model
NOTHING_INDEXED = "Index a magazine first (📚 → Make searchable)"

SERIES: list[dict[str, Any]] = [
    {"id": "zzap64", "name": "Zzap!64", "about": "UK · 1985–1992 · games, famous for its reviews",
     "query": "identifier:zzap64-magazine-*", "alias": r"\bzzap\s*!?\s*(?:64)?\b"},
    {"id": "gazette", "name": "Compute!'s Gazette", "about": "US · 1983–1990 · type-in programs and reviews",
     "query": "identifier:*-computegazette", "alias": r"\b(?:compute\s*!?\s*'?s?\s+)?gazette\b"},
    {"id": "commodore-user", "name": "Commodore User", "about": "UK · 1983–1990",
     "query": "identifier:commodore-user-magazine-*", "alias": r"\bcommodore\s+user\b"},
    {"id": "your-commodore", "name": "Your Commodore", "about": "UK · 1984–1991",
     "query": "identifier:*-your-commodore-magazine", "alias": r"\byour\s+commodore\b"},
    {"id": "run", "name": "RUN", "about": "US · 1984–1992",
     "query": "identifier:run-magazine-*", "alias": r"\bRUN\b(?:\s+magazine)?|\brun\s+magazine\b",
     "alias_case": True, "exclude": ("run-magazine-collection",)},
    {"id": "horizons", "name": "Commodore Horizons", "about": "UK · 1983–1986",
     "query": "identifier:commodore-horizons-*", "alias": r"\bcommodore\s+horizons\b"},
    {"id": "commodore-format", "name": "Commodore Format", "about": "UK · 1990–1995",
     "item": "commodore-format", "first_month": (1990, 10), "alias": r"\bcommodore\s+format\b"},
]
SERIES_BY_ID = {s["id"]: s for s in SERIES}

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")

_STOP = """
a about above after again against all also am an and any are as at be been before being below between both but by
can could did do does doing down during each ever every few for from further get got had has have having he her here
hers him his how i if in into is it its just like me more most my no nor not of off on once only or other our out over
own same she should so some such than that the their them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your yours
anything something someone anyone tell me know please find show list give gave given say said says think thought
thoughts rate rated rating ratings score scored scores review reviews reviewed reviewing magazine magazines issue
issues mag mags article articles write wrote written mention mentioned mentions much many back old c64 commodore
"""
STOPWORDS = frozenset(_STOP.split())


# ---------------------------------------------------------------- Internet Archive links (built here only)
def _safe(part: str | None) -> str | None:
    return part if part and _SAFE_ID.fullmatch(part) else None


def reader_url(identifier: str, stem: str | None = None) -> str:
    """The Internet Archive's embeddable book reader for one issue (a multi-file item takes the file stem)."""
    return f"{IA}/embed/{quote(identifier)}" + (f"/{quote(stem)}" if stem else "")


def details_url(identifier: str, stem: str | None = None, q: str | None = None) -> str:
    """The issue's page on archive.org; with ``q`` its reader opens with that term searched and highlighted."""
    url = f"{IA}/details/{quote(identifier)}" + (f"/{quote(stem)}" if stem else "")
    return url + (f"?q={quote(q.strip()[:80])}" if q and q.strip() else "")


def cover_source(identifier: str, stem: str | None = None, width: int = 300) -> str:
    """The issue's front page as a JPEG from the Internet Archive's page service (a multi-file item takes the stem)."""
    return f"{IA}/download/{quote(identifier)}/" + (f"{quote(stem)}/" if stem else "") + f"page/n0_w{width}.jpg"


def cover_url(key: str) -> str:
    """The console's own (cached) address for an issue's cover."""
    return f"/api/magazines/cover?key={quote(key, safe='')}"


def search_url(query: str) -> str:
    params = [("q", query), ("fl[]", "identifier"), ("fl[]", "title"), ("fl[]", "date"),
              ("rows", "1000"), ("output", "json"), ("sort[]", "date asc")]
    return f"{IA}/advancedsearch.php?{urlencode(params)}"


def files_url(identifier: str) -> str:
    return f"{IA}/metadata/{quote(identifier)}/files"


def download_url(identifier: str, filename: str) -> str:
    return f"{IA}/download/{quote(identifier)}/{quote(filename)}"


# ---------------------------------------------------------------- catalog parsing
def _number(title: str) -> int | None:
    m = re.search(r"\bissue\D{0,3}(\d{1,4})\b", title, re.I)
    return int(m.group(1)) if m else None


def parse_search(text: str, series: dict[str, Any]) -> list[dict[str, Any]]:
    """advancedsearch JSON → issues (one IA item each). Collections and other non-issues are left out."""
    docs = (json.loads(text).get("response") or {}).get("docs") or []
    out = []
    for d in docs:
        ident = _safe(str(d.get("identifier") or ""))
        if not ident or ident in series.get("exclude", ()) or "collection" in ident or not re.search(r"\d", ident):
            continue
        title = re.sub(r"\s+", " ", str(d.get("title") or ident)).strip()[:300]
        num = _number(title)
        if num is None and (m := re.search(r"-(\d{1,3})$", ident)):
            num = int(m.group(1))
        date = str(d.get("date") or "")[:7] if re.match(r"\d{4}-\d{2}", str(d.get("date") or "")) else None
        out.append({"key": ident, "identifier": ident, "stem": None, "text_file": None, "text_size": None,
                    "title": title, "date": date, "number": num})
    return _sorted(out)


def parse_multifile(text: str, series: dict[str, Any]) -> list[dict[str, Any]]:
    """One item holding a whole series (``…/files`` JSON): every ``*_djvu.txt`` is one issue."""
    files = json.loads(text).get("result") or []
    item = series["item"]
    out = []
    for f in files:
        name = str(f.get("name") or "")
        if not name.endswith("_djvu.txt") or "/" in name:
            continue
        stem = _safe(name[: -len("_djvu.txt")])
        if not stem:
            continue
        m = re.search(r"(\d{1,4})(?!.*\d)", stem)
        num = int(m.group(1)) if m else None
        date = None
        if num and series.get("first_month"):
            y, mo = series["first_month"]
            k = (y * 12 + mo - 1) + num - 1
            date = f"{k // 12:04d}-{k % 12 + 1:02d}"
        size = int(f["size"]) if str(f.get("size") or "").isdigit() else None
        out.append({"key": f"{item}/{stem}", "identifier": item, "stem": stem, "text_file": name, "text_size": size,
                    "title": f"{series['name']} Issue {num:03d}" if num is not None else f"{series['name']} {stem}",
                    "date": date, "number": num})
    return _sorted(out)


def _sorted(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items.sort(key=lambda i: (i["date"] or "9999", i["number"] if i["number"] is not None else 9999, i["key"]))
    for n, i in enumerate(items):
        i["sort"] = n
    return items


def pick_text_file(text: str) -> tuple[str, int | None] | None:
    """An item's ``…/files`` JSON → its OCR text file (the largest ``*_djvu.txt``) and size."""
    best: tuple[str, int | None] | None = None
    for f in json.loads(text).get("result") or []:
        name = str(f.get("name") or "")
        if not name.endswith("_djvu.txt") or "/" in name:
            continue
        size = int(f["size"]) if str(f.get("size") or "").isdigit() else None
        if best is None or (size or 0) > (best[1] or 0):
            best = (name, size)
    return best


# ---------------------------------------------------------------- text, queries, snippets
def chunk_text(text: str, size: int = CHUNK, overlap: int = OVERLAP) -> list[str]:
    """OCR text → overlapping chunks of about ``size`` characters, cut at spaces."""
    t = re.sub(r"\s+", " ", text or "").strip()
    out: list[str] = []
    start = 0
    while start < len(t):
        end = min(len(t), start + size)
        if end < len(t) and (cut := t.rfind(" ", start + size // 2, end)) > 0:
            end = cut
        out.append(t[start:end].strip())
        if end >= len(t):
            break
        nxt = max(start + 1, end - overlap)
        sp = t.find(" ", nxt, end)
        start = sp + 1 if sp != -1 else nxt
    return [c for c in out if c]


def words(q: str | None, limit: int = 12) -> list[str]:
    """Plain words of a query — never FTS syntax (quotes, *, :, NEAR, parentheses… are all dropped)."""
    return re.findall(r"[^\W_]+", q or "")[:limit]


def fts_query(ws: list[str], mode: str = "all") -> str:
    """Quoted terms: ``all`` (every word), ``any`` (OR) or ``phrase`` (the words in a row)."""
    ws = [w.replace('"', "") for w in ws if w.replace('"', "")]
    if not ws:
        return ""
    if mode == "phrase":
        return '"' + " ".join(ws) + '"'
    return (" OR " if mode == "any" else " ").join(f'"{w}"' for w in ws)


def snippet(text: str, ws: list[str], width: int = 260) -> tuple[str, list[dict[str, Any]]]:
    """A short piece of ``text`` around the first hit → (plain text, parts with ``hit`` marks)."""
    rx = re.compile(r"\b(" + "|".join(re.escape(w) for w in sorted(set(ws), key=len, reverse=True)) + r")\w*", re.I) \
        if ws else None
    m = rx.search(text) if rx else None
    at = m.start() if m else 0
    start = max(0, at - width // 3)
    if start:
        sp = text.find(" ", start)
        start = sp + 1 if 0 <= sp < at else start
    end = min(len(text), start + width)
    if end < len(text) and (sp := text.rfind(" ", start + width // 2, end)) > 0:
        end = sp
    piece = text[start:end].strip()
    pre, post = ("…" if start > 0 else ""), ("…" if end < len(text) else "")
    parts: list[dict[str, Any]] = []
    pos = 0
    for h in (rx.finditer(piece) if rx else []):
        if h.start() > pos:
            parts.append({"text": piece[pos:h.start()], "hit": False})
        parts.append({"text": h.group(0), "hit": True})
        pos = h.end()
    if pos < len(piece):
        parts.append({"text": piece[pos:], "hit": False})
    if pre:
        parts.insert(0, {"text": pre, "hit": False})
    if post:
        parts.append({"text": post, "hit": False})
    return pre + piece + post, parts


def search_terms(question: str) -> dict[str, Any]:
    """A question → {series: [ids named in it], phrases: [proper nouns / quoted], keywords: [words]}."""
    q = question or ""
    named = []
    for s in SERIES:
        rx = re.compile(s["alias"], 0 if s.get("alias_case") else re.I)
        if rx.search(q):
            named.append(s["id"])
            q = rx.sub(" ", q)
    phrases = [p.strip() for p in re.findall(r"[\"“”]([^\"“”]{2,60})[\"“”]", q) if words(p)]
    q2 = re.sub(r"[\"“”]", " ", q)
    for run in re.findall(r"\b[A-Z0-9][\w'!-]*(?:\s+(?:[A-Z0-9][\w'!-]*|of|the|and))*", q2):
        ws = words(run)
        while ws and ws[0].lower() in STOPWORDS:
            ws = ws[1:]
        while ws and ws[-1].lower() in STOPWORDS:
            ws = ws[:-1]
        if ws and not (len(ws) == 1 and ws[0].isdigit()):
            p = " ".join(ws)
            if p.lower() not in (x.lower() for x in phrases):
                phrases.append(p)
    keywords = []
    for w in words(q2, 30):
        lw = w.lower()
        if lw not in STOPWORDS and (len(lw) > 1 or lw.isdigit()) and lw not in keywords:
            keywords.append(lw)
    return {"series": named, "phrases": phrases[:4], "keywords": keywords[:10]}


# ---------------------------------------------------------------- the local full-text index
class MagIndex:
    """``magazines.db``: chunks in FTS5 (or a plain table when FTS5 is missing) and per-issue index status."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS issues (issue_key TEXT PRIMARY KEY, series TEXT, status TEXT, "
                       "chunks INTEGER DEFAULT 0, error TEXT, at TEXT)")
            try:
                db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(text, issue_key UNINDEXED, "
                           "series UNINDEXED, n UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
                self.fts = True
            except sqlite3.OperationalError:
                db.execute("CREATE TABLE IF NOT EXISTS chunks_plain (text TEXT, issue_key TEXT, series TEXT, n INTEGER)")
                db.execute("CREATE INDEX IF NOT EXISTS chunks_plain_key ON chunks_plain (issue_key)")
                self.fts = False
                log.info("SQLite FTS5 is not available — magazine search falls back to LIKE")
        self.table = "chunks" if self.fts else "chunks_plain"

    @contextlib.contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def store(self, key: str, series: str, chunks: list[str]) -> None:
        with self._db() as db:
            db.execute(f"DELETE FROM {self.table} WHERE issue_key = ?", (key,))  # noqa: S608 - fixed table name
            db.executemany(f"INSERT INTO {self.table} (text, issue_key, series, n) VALUES (?, ?, ?, ?)",  # noqa: S608
                           [(c, key, series, n) for n, c in enumerate(chunks)])
            db.execute("INSERT OR REPLACE INTO issues (issue_key, series, status, chunks, error, at) "
                       "VALUES (?, ?, 'ok', ?, NULL, ?)", (key, series, len(chunks), datetime.now(UTC).isoformat()))

    def mark(self, key: str, series: str, status: str, error: str | None = None) -> None:
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO issues (issue_key, series, status, chunks, error, at) "
                       "VALUES (?, ?, ?, 0, ?, ?)", (key, series, status, (error or "")[:300] or None,
                                                     datetime.now(UTC).isoformat()))

    def status(self, series: str | None = None) -> dict[str, dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT issue_key, status, chunks, error FROM issues"
                              + (" WHERE series = ?" if series else ""), (series,) if series else ()).fetchall()
        return {k: {"status": st, "chunks": n, "error": e} for k, st, n, e in rows}

    def counts(self) -> dict[str, int]:
        with self._db() as db:
            return dict(db.execute("SELECT series, COUNT(*) FROM issues WHERE status = 'ok' GROUP BY series").fetchall())

    def search(self, ws: list[str], mode: str = "all", series: list[str] | None = None,
               limit: int = 20) -> list[tuple[str, int, str]]:
        """→ [(issue_key, chunk number, text)], best first."""
        if not ws:
            return []
        where, args = "", []
        if series:
            where = f" AND series IN ({','.join('?' * len(series))})"
            args = list(series)
        with self._db() as db:
            if self.fts:
                sql = (f"SELECT issue_key, n, text FROM chunks WHERE chunks MATCH ?{where} "  # noqa: S608
                       "ORDER BY bm25(chunks) LIMIT ?")
                return [(k, int(n), t) for k, n, t in db.execute(sql, [fts_query(ws, mode), *args, limit])]
            like = [" ".join(ws)] if mode == "phrase" else ws
            cond = (" OR " if mode == "any" else " AND ").join(["text LIKE ? ESCAPE '\\'"] * len(like))
            pats = ["%" + re.sub(r"([%_\\])", r"\\\1", w) + "%" for w in like]
            sql = f"SELECT issue_key, n, text FROM chunks_plain WHERE ({cond}){where} LIMIT ?"  # noqa: S608
            return [(k, int(n), t) for k, n, t in db.execute(sql, [*pats, *args, limit])]


    def span(self, issue_key: str, first: int, last: int) -> list[tuple[int, str]]:
        """Consecutive chunks of one issue (a review and the ratings box that follows it)."""
        table = "chunks" if self.fts else "chunks_plain"
        with self._db() as db:
            return [(int(n), t) for n, t in db.execute(
                f"SELECT n, text FROM {table} WHERE issue_key = ? AND n BETWEEN ? AND ? ORDER BY n",  # noqa: S608
                (issue_key, first, last))]


class TooLarge(ValueError):
    pass


# ---------------------------------------------------------------- the service
class MagazineService:
    def __init__(self, ask, sf, data_path, fetch=None, gap: float = GAP):  # noqa: ANN001
        self.ask, self.sf = ask, sf
        self._data_path = data_path                      # () -> Path
        self._fetch = fetch or self._http_get
        self.gap = gap
        self._index: MagIndex | None = None
        self._net = asyncio.Lock()                       # one series is indexed at a time
        self._cover_sem = asyncio.Semaphore(4)           # covers: a few at a time from the Internet Archive
        self._cover_fail: dict[str, float] = {}
        self._catalog_locks: dict[str, asyncio.Lock] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self.jobs: dict[str, dict[str, Any]] = {}

    @property
    def index(self) -> MagIndex:
        if self._index is None:
            self._index = MagIndex(Path(self._data_path()) / "magazines.db")
        return self._index

    async def stop(self) -> None:
        for task in list(self._tasks.values()):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    @staticmethod
    async def _http_get(url: str, limit: int = MAX_TEXT) -> str:
        async with (httpx.AsyncClient(timeout=60, headers=UA, follow_redirects=True) as client,
                    client.stream("GET", url) as r):
            r.raise_for_status()
            if int(r.headers.get("content-length") or 0) > limit:
                raise TooLarge("text over 5 MB")
            buf = bytearray()
            async for part in r.aiter_bytes():
                buf += part
                if len(buf) > limit:
                    raise TooLarge("text over 5 MB")
        return bytes(buf).decode("utf-8", "replace")

    # ------------------------------------------------------------ catalog
    def _series(self, series_id: str) -> dict[str, Any]:
        if series_id not in SERIES_BY_ID:
            raise KeyError(series_id)
        return SERIES_BY_ID[series_id]

    def _stale(self, series_id: str) -> bool:
        with self.sf() as s:
            row = s.get(MagazineSeries, series_id)
        if row is None or row.fetched_at is None:
            return True
        at = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=UTC)
        return datetime.now(UTC) - at > CATALOG_MAX_AGE

    async def ensure_catalog(self, series_id: str, force: bool = False) -> None:
        """Read the series' issue list from the Internet Archive (at most daily). Errors keep the old list."""
        series = self._series(series_id)
        lock = self._catalog_locks.setdefault(series_id, asyncio.Lock())
        async with lock:
            if force or self._stale(series_id):
                await self._read_catalog(series_id, series)

    async def _read_catalog(self, series_id: str, series: dict[str, Any]) -> None:
        try:
            if series.get("item"):
                items = parse_multifile(await self._fetch(files_url(series["item"])), series)
            else:
                items = parse_search(await self._fetch(search_url(series["query"])), series)
            error = None if items else "no issues found"
        except Exception as exc:  # noqa: BLE001 - keep the cached list
            items, error = [], f"{type(exc).__name__}: {str(exc)[:200]}"
            log.info("magazine catalog %s failed: %s", series_id, error)
        with self.sf() as s:
            if items:
                existing = {i.key: i for i in s.scalars(select(MagazineIssue).where(MagazineIssue.series == series_id))}
                for it in items:
                    row = existing.pop(it["key"], None)
                    if row is None:
                        s.add(MagazineIssue(series=series_id, **it))
                        continue
                    for k in ("title", "date", "number", "sort", "identifier", "stem"):
                        setattr(row, k, it[k])
                    if it["text_file"]:
                        row.text_file, row.text_size = it["text_file"], it["text_size"]
                    row.fetched_at = datetime.now(UTC)
                for gone in existing.values():
                    s.delete(gone)
            meta = s.get(MagazineSeries, series_id) or MagazineSeries(id=series_id)
            meta.error = error
            if items:
                meta.fetched_at = datetime.now(UTC)
            elif meta.fetched_at is None:
                meta.fetched_at = datetime.now(UTC) - CATALOG_MAX_AGE + timedelta(minutes=10)   # retry soon
            s.merge(meta)
            s.commit()

    def _issue_rows(self, series_id: str | None = None, keys: list[str] | None = None) -> list[MagazineIssue]:
        with self.sf() as s:
            stmt = select(MagazineIssue)
            if series_id:
                stmt = stmt.where(MagazineIssue.series == series_id)
            if keys is not None:
                stmt = stmt.where(MagazineIssue.key.in_(keys))
            return list(s.scalars(stmt.order_by(MagazineIssue.series, MagazineIssue.sort)))

    @staticmethod
    def issue_dict(i: MagazineIssue, indexed: dict[str, Any] | None = None) -> dict[str, Any]:
        st = (indexed or {}).get(i.key) or {}
        return {"key": i.key, "identifier": i.identifier, "file": i.stem, "series": i.series, "title": i.title,
                "date": i.date, "number": i.number, "indexed": st.get("status") == "ok",
                "indexStatus": st.get("status"), "readerUrl": reader_url(i.identifier, i.stem), "coverUrl": cover_url(i.key),
                "detailsUrl": details_url(i.identifier, i.stem)}

    async def issues(self, series_id: str, refresh: bool = False) -> dict[str, Any]:
        series = self._series(series_id)
        await self.ensure_catalog(series_id, force=refresh)
        status = self.index.status(series_id)
        with self.sf() as s:
            meta = s.get(MagazineSeries, series_id)
        return {"series": {"id": series_id, "name": series["name"], "about": series["about"]},
                "error": meta.error if meta else None,
                "issues": [self.issue_dict(i, status) for i in self._issue_rows(series_id)]}

    async def overview(self) -> dict[str, Any]:
        """All series: issue count, how many are searchable, indexing progress."""
        never = [s["id"] for s in SERIES if self._never_fetched(s["id"])]
        if never:
            await asyncio.gather(*(self.ensure_catalog(sid) for sid in never))
        counts = self.index.counts()
        with self.sf() as s:
            metas = {m.id: m for m in s.scalars(select(MagazineSeries))}
            totals: dict[str, int] = {}
            firsts: dict[str, str] = {}
            for sid, key in s.execute(select(MagazineIssue.series, MagazineIssue.key).order_by(MagazineIssue.sort)):
                totals[sid] = totals.get(sid, 0) + 1
                firsts.setdefault(sid, key)
        out = []
        for sr in SERIES:
            job = self.jobs.get(sr["id"])
            m = metas.get(sr["id"])
            first = firsts.get(sr["id"])
            out.append({"id": sr["id"], "name": sr["name"], "about": sr["about"],
                        "coverUrl": cover_url(first) if first else None,
                        "issueCount": totals.get(sr["id"], 0), "indexed": counts.get(sr["id"], 0),
                        "indexing": bool(job and job["running"]), "job": job,
                        "error": m.error if m else None,
                        "fetchedAt": m.fetched_at.isoformat() if m and m.fetched_at else None})
        return {"series": out, "indexedTotal": sum(counts.values()), "fts": self.index.fts}

    async def update_all(self) -> dict[str, Any]:
        """🔄 Scheduled update: re-read every series' issue list (new scans appear on the Internet Archive), then
        index the new issues of the magazines you've already made searchable."""
        before = self._issue_counts()
        for sr in SERIES:
            await self.ensure_catalog(sr["id"], force=True)
        after = self._issue_counts()
        new = sum(max(0, after.get(sid, 0) - before.get(sid, 0)) for sid in after)
        indexed = 0
        for sid, n in self.index.counts().items():
            if n and sid in SERIES_BY_ID and not (self._tasks.get(sid) and not self._tasks[sid].done()):
                job = await self.run_index(sid)
                indexed += job.get("indexed", 0)
        covers = await self.prefetch_covers()
        return {"new": new, "indexed": indexed, "checked": len(SERIES), "downloaded": covers}

    # ------------------------------------------------------------ 🖼 covers
    @property
    def cover_dir(self) -> Path:
        return Path(self._data_path()) / "magazine_covers"

    def cover_file(self, key: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:150]
        return self.cover_dir / f"{safe}.jpg"

    async def cover(self, key: str) -> Path | None:
        """An issue's front page, fetched once from the Internet Archive and kept here (fast pages, no repeat
        requests). None when the issue is unknown or the archive has no page image (retried after a day)."""
        f = self.cover_file(key)
        if f.exists():
            return f
        if time.time() - self._cover_fail.get(key, 0) < 86400:
            return None
        with self.sf() as s:
            issue = s.scalars(select(MagazineIssue).where(MagazineIssue.key == key)).first()
            src = cover_source(issue.identifier, issue.stem) if issue else None
        if src is None:
            return None
        async with self._cover_sem:
            if f.exists():
                return f
            try:
                async with httpx.AsyncClient(timeout=40, follow_redirects=True, headers=UA) as client:
                    r = await client.get(src)
                r.raise_for_status()
                if not r.content.startswith(b"\xff\xd8") or not 1000 < len(r.content) < 3_000_000:
                    raise ValueError("not a JPEG page image")
                self.cover_dir.mkdir(parents=True, exist_ok=True)
                f.write_bytes(r.content)
                return f
            except (httpx.HTTPError, ValueError) as exc:
                self._cover_fail[key] = time.time()
                log.info("cover of %s: %s", key, str(exc)[:120])
                return None

    async def prefetch_covers(self, gap: float = 0.5) -> int:
        """Download the covers that aren't here yet (one at a time), so browsing an issue grid is instant."""
        with self.sf() as db:
            keys = [k for (k,) in db.execute(select(MagazineIssue.key).order_by(MagazineIssue.series, MagazineIssue.sort))]
        done = 0
        for key in keys:
            if self.cover_file(key).exists():
                continue
            if await self.cover(key):
                done += 1
            if gap:
                await asyncio.sleep(gap)
        return done

    def _issue_counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        with self.sf() as s:
            for (sid,) in s.execute(select(MagazineIssue.series)):
                totals[sid] = totals.get(sid, 0) + 1
        return totals

    def _never_fetched(self, series_id: str) -> bool:
        with self.sf() as s:
            row = s.get(MagazineSeries, series_id)
        return row is None or row.fetched_at is None

    # ------------------------------------------------------------ indexing
    def start_index(self, series_id: str) -> dict[str, Any]:
        self._series(series_id)
        task = self._tasks.get(series_id)
        if task is None or task.done():
            self.jobs[series_id] = {"running": True, "queued": True, "done": 0, "total": 0, "current": None,
                                    "indexed": 0, "skipped": 0, "errors": 0, "lastError": None,
                                    "cancelled": False, "startedAt": datetime.now(UTC).isoformat(),
                                    "finishedAt": None}
            self._tasks[series_id] = asyncio.create_task(self.run_index(series_id), name=f"magazines-{series_id}")
        return self.jobs[series_id]

    async def cancel_index(self, series_id: str) -> dict[str, Any] | None:
        self._series(series_id)
        task = self._tasks.get(series_id)
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        return self.jobs.get(series_id)

    async def run_index(self, series_id: str, limit: int | None = None) -> dict[str, Any]:
        """Index every not-yet-indexed issue of a series (or the first ``limit`` of them). Resumable."""
        job = self.jobs.get(series_id)
        if job is None or not job["running"]:
            job = self.jobs[series_id] = {"running": True, "queued": True, "done": 0, "total": 0, "current": None,
                                          "indexed": 0, "skipped": 0, "errors": 0, "lastError": None,
                                          "cancelled": False, "startedAt": datetime.now(UTC).isoformat(),
                                          "finishedAt": None}
        try:
            async with self._net:
                job["queued"] = False
                await self.ensure_catalog(series_id)
                status = self.index.status(series_id)
                todo = [i for i in self._issue_rows(series_id)
                        if (status.get(i.key) or {}).get("status") not in ("ok", "skipped")]
                todo = todo[:limit] if limit else todo
                job["total"] = len(todo)
                for issue in todo:
                    job["current"] = issue.title
                    try:
                        result = await self._index_issue(issue)
                        job["indexed" if result == "ok" else "skipped"] += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - one bad issue must not stop the rest
                        msg = f"{type(exc).__name__}: {str(exc)[:160]}"
                        job["errors"] += 1
                        job["lastError"] = f"{issue.title}: {msg}"
                        self.index.mark(issue.key, series_id, "error", msg)
                        log.info("indexing %s failed: %s", issue.key, msg)
                        await self._pause()
                    job["done"] += 1
        except asyncio.CancelledError:
            job["cancelled"] = True
            raise
        finally:
            job.update(running=False, queued=False, current=None, finishedAt=datetime.now(UTC).isoformat())
        return job

    async def _pause(self) -> None:
        if self.gap:
            await asyncio.sleep(self.gap)

    async def _index_issue(self, issue: MagazineIssue) -> str:
        """Download one issue's OCR text and index it → "ok" or "skipped"."""
        name, size = issue.text_file, issue.text_size
        if not name:
            picked = pick_text_file(await self._fetch(files_url(issue.identifier)))
            await self._pause()
            if not picked:
                self.index.mark(issue.key, issue.series, "skipped", "no OCR text in this item")
                return "skipped"
            name, size = picked
            with self.sf() as s:
                row = s.get(MagazineIssue, issue.id)
                if row:
                    row.text_file, row.text_size = name, size
                    s.commit()
        if size and size > MAX_TEXT:
            self.index.mark(issue.key, issue.series, "skipped", "OCR text over 5 MB")
            return "skipped"
        try:
            text = await self._fetch(download_url(issue.identifier, name))
        except TooLarge:
            text = None
        await self._pause()
        if text is None or len(text) > MAX_TEXT:
            self.index.mark(issue.key, issue.series, "skipped", "OCR text over 5 MB")
            return "skipped"
        chunks = chunk_text(text)
        await asyncio.to_thread(self.index.store, issue.key, issue.series, chunks)
        return "ok"

    # ------------------------------------------------------------ search
    def _hits(self, rows: list[tuple[str, int, str]], ws: list[str], link_q: str) -> list[dict[str, Any]]:
        issues = {i.key: i for i in self._issue_rows(keys=list({k for k, _, _ in rows}))}
        out = []
        for key, n, text in rows:
            i = issues.get(key)
            if i is None:
                continue
            plain, parts = snippet(text, ws)
            out.append({"issueKey": key, "series": i.series, "seriesName": SERIES_BY_ID.get(i.series, {}).get("name"),
                        "issueTitle": i.title, "date": i.date, "chunk": n, "snippet": plain, "parts": parts,
                        "link": details_url(i.identifier, i.stem, link_q),
                        "readerUrl": reader_url(i.identifier, i.stem), "coverUrl": cover_url(key), "text": text})
        return out

    def search(self, q: str, series: str | None = None, limit: int = 20) -> dict[str, Any]:
        if series:
            self._series(series)
        ws = words(q)
        if not ws:
            return {"q": q, "hits": [], "mode": None}
        sel = [series] if series else None
        mode = "all"
        rows = self.index.search(ws, "all", sel, limit)
        if not rows and len(ws) > 1:
            mode, rows = "any", self.index.search(ws, "any", sel, limit)
        hits = self._hits(rows, ws, " ".join(ws))
        for h in hits:
            h.pop("text")
        return {"q": q, "hits": hits, "mode": mode}

    def _retrieve(self, phrases: list[str], keywords: list[str], series: list[str] | None,
                  limit: int = TOP) -> list[tuple[str, int, str]]:
        """Best chunks for a question: each phrase, then every keyword, then any keyword (bm25 order)."""
        seen: set[tuple[str, int]] = set()
        out: list[tuple[str, int, str]] = []
        queries = [(words(p), "phrase") for p in phrases] + [(keywords, "all"), (keywords, "any")]
        for ws, mode in queries:
            if len(out) >= limit or not ws:
                continue
            for k, n, t in self.index.search(ws, mode, series, limit):
                if (k, n) not in seen and len(out) < limit:
                    seen.add((k, n))
                    out.append((k, n, t))
        return out

    def _require_index(self) -> dict[str, int]:
        counts = self.index.counts()
        if not sum(counts.values()):
            raise AskError(NOTHING_INDEXED)
        return counts

    @staticmethod
    def _excerpts(hits: list[dict[str, Any]]) -> str:
        return "\n\n".join(f"[{n}] {h['seriesName']} — {h['issueTitle']}" + (f" ({h['date']})" if h["date"] else "")
                           + f"\n{h['text']}" for n, h in enumerate(hits, 1))

    async def ask_question(self, question: str) -> dict[str, Any]:
        """🤖 Answer a question from the indexed magazines only, citing the excerpts used."""
        question = (question or "").strip()[:500]
        if not words(question):
            raise AskError("Ask a question about the magazines")
        counts = self._require_index()
        t = search_terms(question)
        series = [s for s in t["series"] if counts.get(s)] or None
        rows = self._retrieve(t["phrases"], t["keywords"], series)
        if len(rows) < 4 and (extra := await self._model_phrases(question)):
            rows = self._retrieve(t["phrases"] + extra, t["keywords"], series)
        link_q = (t["phrases"] or [" ".join(t["keywords"][:3])])[0]
        hits = self._hits(rows, words(" ".join(t["phrases"] + t["keywords"])), link_q)
        if not hits:
            return {"answer": "Nothing in the searchable magazines matches that question. Try other words, or make "
                              "more magazines searchable.", "citations": [], "terms": t}
        system = ("You answer questions about classic Commodore 64 magazines. Use ONLY the numbered magazine "
                  "excerpts given (OCR text of scanned pages, so expect typos). Cite the excerpts you used as [n] "
                  "right after the facts they support. If the excerpts do not answer the question, say so plainly. "
                  "Quote review scores exactly as printed (e.g. 93%). Never invent facts, links or issue numbers. "
                  "Reply with one JSON object: {\"answer\": str (at most 6 sentences), \"citations\": [int]}.")
        data, _ = await ask_json(self.ask, system, f"Question: {question}\n\nMagazine excerpts:\n\n{self._excerpts(hits)}",
                                 max_tokens=1200, what="magazine answer")
        answer = str(data.get("answer") or "").strip()[:3000]
        if not answer:
            raise AskError("The AI model could not make the magazine answer: no answer")
        valid = range(1, len(hits) + 1)
        used = {int(n) for n in (data.get("citations") or []) if str(n).strip().isdigit()}
        used |= {int(n) for n in re.findall(r"\[(\d{1,3})\]", answer)}
        answer = re.sub(r"\s?\[(\d{1,3})\]", lambda m: m.group(0) if int(m.group(1)) in valid else "", answer)
        citations = [{"n": n, "issueTitle": hits[n - 1]["issueTitle"], "seriesName": hits[n - 1]["seriesName"],
                      "date": hits[n - 1]["date"], "link": hits[n - 1]["link"],
                      "readerUrl": hits[n - 1]["readerUrl"], "snippet": hits[n - 1]["snippet"]}
                     for n in sorted(used) if n in valid]
        return {"answer": answer, "citations": citations, "terms": t}

    async def _model_phrases(self, question: str) -> list[str]:
        """🤖 1-3 search phrases for a question the keywords did not find much for (best effort)."""
        system = ("You turn a question about classic Commodore 64 magazines into search phrases for a full-text "
                  "index of their OCR text. Reply with one JSON object: {\"phrases\": [1-3 short phrases: game "
                  "titles, people, companies or other distinctive words]}.")
        try:
            data, _ = await ask_json(self.ask, system, question, max_tokens=200, what="search phrases")
        except AskError:
            return []
        return [p for p in strs(data.get("phrases"), 3, 60) if words(p)]

    # ------------------------------------------------------------ 🤖 game reviews
    def get_reviews(self, game_id: int) -> dict[str, Any]:
        with self.sf() as s:
            row = s.get(MagazineReview, game_id)
            if row is None:
                return {"gameId": game_id, "reviews": None, "madeAt": None}
            reviews = [dict(r) for r in row.reviews or []]
            title, made = row.title, row.made_at
        self._add_covers(reviews)
        return {"gameId": game_id, "title": title, "reviews": reviews, "madeAt": made.isoformat() if made else None}

    def _add_covers(self, reviews: list[dict[str, Any]]) -> None:
        """Each review's issue cover (reviews saved before covers existed are matched by their reader link)."""
        missing = [r for r in reviews if not r.get("coverUrl")]
        if not missing:
            return
        by_reader = {reader_url(i.identifier, i.stem): i.key for i in self._issue_rows()}
        for r in missing:
            key = r.get("issueKey") or by_reader.get(r.get("readerUrl") or "")
            if key:
                r["issueKey"], r["coverUrl"] = key, cover_url(key)

    def _review_excerpts(self, hits: list[dict[str, Any]], names: list[str], issues: int = 6, after: int = 12,
                         limit: int = 7000) -> list[dict[str, Any]]:
        """One excerpt per issue (best issues first): the passages that name the game and the chunks right after
        them, where a review's ratings box sits — so the model sees each review whole, not in fragments."""
        groups: dict[str, list[dict[str, Any]]] = {}
        for h in hits:
            groups.setdefault(h["issueKey"], []).append(h)
        out = []
        for key, hs in list(groups.items())[:issues]:
            ns = sorted(h["chunk"] for h in hs)
            chunks = self.index.span(key, ns[0], max(ns[-1], ns[0]) + after) or [(h["chunk"], h["text"]) for h in hs]
            text = "\n".join(t for _, t in chunks)
            box, box_score = ratings_box(text, names)
            if box and len(text) > limit:            # keep the review's end and its ratings box in the excerpt
                end = text.find(box) + len(box) + 200
                text = text[max(0, end - limit):end]
            out.append({**hs[0], "text": text[:limit], "box": box, "boxScore": box_score})
        return out

    async def make_reviews(self, game_id: int) -> dict[str, Any]:
        """Search the index for the game's title and have the AI model pull out each review's score and verdict."""
        with self.sf() as s:
            game = s.get(Game, game_id)
            if game is None:
                raise KeyError(game_id)
            title = game.title
        self._require_index()
        names = [title]
        if (short := re.split(r"\s*[:(\[–-]\s+", title)[0].strip()) and short != title:
            names.append(short)
        rows = self._retrieve(names, [], None)
        reviews: list[dict[str, Any]] = []
        if rows:
            hits = self._review_excerpts(self._hits(rows, words(title), title), names)
            system = ("You extract magazine REVIEWS of one Commodore 64 game from numbered excerpts of scanned "
                      "magazines (OCR text, expect typos). Each excerpt is one magazine issue. Classify what the "
                      "issue has about the game: \"review\" (the magazine's own review of it, usually with a ratings "
                      "box), \"preview\", \"diary\" (a programmer's development diary), \"retrospective\" (a later "
                      "article recalling an old score), \"mention\" (charts, adverts, letters, news). "
                      "A review's ratings box "
                      "(Presentation, Graphics, Sound, Hookability, Lastability, Value, Overall…) may follow later in "
                      "the excerpt and OCR may garble it (\"Owrall 9?%\"): give your best reading of the OVERALL score "
                      "and set scoreUncertain true when characters had to be guessed. Never invent a score that isn't "
                      "in the text. Reply with one JSON object: {\"reviews\": [{\"kind\": str, \"magazine\": str, "
                      "\"issue\": str, "
                      "\"date\": str, \"score\": str (overall score as printed, e.g. \"93%\", or \"\" if none), "
                      "\"scoreUncertain\": bool, \"verdict\": str (max 140 chars), \"n\": int (the excerpt number)}]}. "
                      "Use an empty list if there is no review.")
            excerpts = "\n\n".join(
                f"[{n}] {h['seriesName']} — {h['issueTitle']}" + (f" ({h['date']})" if h["date"] else "")
                + (f"\nRatings box found after the last mention of the game: «{h['box']}»" if h["box"] else "")
                + f"\n{h['text']}" for n, h in enumerate(hits, 1))
            data, _ = await ask_json(self.ask, system, f"Game: {title}\n\nMagazine excerpts:\n\n{excerpts}",
                                     max_tokens=4000, what="magazine reviews")   # room for reasoning models
            quoted = quoted_scores([h["text"] for h in hits], names)
            seen: set[str] = set()
            for r in data.get("reviews") or []:
                if not isinstance(r, dict):
                    continue
                try:
                    n = int(r.get("n"))
                except (TypeError, ValueError):
                    continue
                if not 1 <= n <= len(hits):
                    continue                              # must point at an excerpt we gave
                h = hits[n - 1]
                if str(r.get("kind") or "review").lower() != "review":
                    continue                              # previews, diaries, retrospectives, mentions aren't reviews
                score = strs([r.get("score")], 1, 20)
                verdict = strs([r.get("verdict")], 1, 160)
                # a score read from a garbled box ("Owrall a7°k3") is a best reading, whatever the model says
                uncertain = bool(r.get("scoreUncertain")) or bool(h["box"] and not h["boxScore"])
                if score and not _score_in_text(score[0], h["text"], uncertain):
                    score = []                            # a number the excerpt doesn't support is dropped
                if h["boxScore"]:                         # printed cleanly in the ratings box: that's the score
                    score, uncertain = [h["boxScore"]], False
                elif h["box"] and (q := quoted.get(_issue_number(h["issueTitle"]) or -1)) and _fits(q, h["box"]):
                    score, uncertain = [q], False         # a later issue quotes it ("97% back in issue 7") and it fits
                if h["issueKey"] in seen or not (score or verdict):
                    continue
                seen.add(h["issueKey"])
                reviews.append({"n": n, "issueKey": h["issueKey"], "coverUrl": h["coverUrl"],
                                "magazine": h["seriesName"], "issue": h["issueTitle"], "date": h["date"],
                                "score": score[0] if score else "", "verdict": verdict[0] if verdict else "",
                                "scoreUncertain": bool(score and uncertain),
                                "link": h["link"], "readerUrl": h["readerUrl"], "snippet": h["snippet"]})
        reviews = drop_retrospectives(reviews)
        with self.sf() as s:
            row = s.get(MagazineReview, game_id) or MagazineReview(game_id=game_id, title=title)
            row.title, row.reviews, row.made_at = title, reviews, datetime.now(UTC)
            s.merge(row)
            s.commit()
        return self.get_reviews(game_id)


_OVERALL = re.compile(r"[o0][vw]\S{0,3}a?ll|overall", re.I)


def ratings_box(text: str, names: list[str]) -> tuple[str | None, str | None]:
    """The "Overall …" entry of the ratings box after the review's last mention of the game → (raw text, clean
    score). The raw text may be OCR-garbled ("Owrall a7°k3"); the clean score is set only when it reads "97%"."""
    last = max((m.end() for n in names if n for m in re.finditer(re.escape(n), text, re.I)), default=-1)
    if last < 0:
        return None, None
    # the box's "Overall" has its number right after it ("Overall 97%"); "the overall ship design" doesn't
    m = next((m for m in _OVERALL.finditer(text, last) if re.search(r"\d|%|°", text[m.end():m.end() + 10])), None)
    if m is None or m.start() - last > 6000:
        return None, None
    raw = re.sub(r"\s+", " ", text[m.start():m.end() + 14]).strip()
    clean = re.match(r"\S+\s*(\d{2,3})\s?%", raw)
    return raw, f"{clean.group(1)}%" if clean and int(clean.group(1)) <= 100 else None


def _issue_number(title: str | None) -> int | None:
    m = re.search(r"issue\s*#?\s*0*(\d{1,3})", title or "", re.I)
    return int(m.group(1)) if m else None


def quoted_scores(texts: list[str], names: list[str]) -> dict[int, str]:
    """Scores later articles quote for an issue's review: "…scored a massive 97% back in Issue 7" → {7: "97%"}."""
    out: dict[int, str] = {}
    for text in texts:
        for name in names:
            for m in re.finditer(re.escape(name), text, re.I):
                window = text[m.start():m.end() + 300]
                q = re.search(r"(\d{2,3})\s?%[^.]{0,80}?\bissue\s*#?\s*(\d{1,3})", window, re.I)
                if q and int(q.group(1)) <= 100:
                    out.setdefault(int(q.group(2)), f"{q.group(1)}%")
    return out


def _fits(score: str, box: str) -> bool:
    """A quoted score fits a garbled box when the digits the OCR did read are part of it ("a7°k3" ~ "97%")."""
    after = _OVERALL.sub("", box, count=1)[:8]
    seen = re.findall(r"\d", after)
    return bool(seen) and all(d in score for d in seen[:1])


def drop_retrospectives(reviews: list[dict[str, Any]], years: int = 3) -> list[dict[str, Any]]:
    """A later article quoting an old score isn't a review: keep reviews from the first few years only."""
    dated = [int(r["date"][:4]) for r in reviews if str(r.get("date") or "")[:4].isdigit()]
    if len(dated) < 2:
        return reviews
    first = min(dated)
    return [r for r in reviews if not str(r.get("date") or "")[:4].isdigit() or int(r["date"][:4]) - first <= years]


def _score_in_text(score: str, text: str, uncertain: bool) -> bool:
    """A review score must be on the page: printed as-is, or (OCR-garbled) next to an "Overall" in the ratings box."""
    digits = re.sub(r"\D", "", score)
    if not digits:
        return True                                  # "Gold Medal", "9/10"… handled as text
    if re.search(rf"(?<!\d){digits}\s?%", text):
        return True
    return uncertain and any(re.search(r"\d|%|°", text[m.end():m.end() + 12]) for m in _OVERALL.finditer(text))


def attach(container) -> MagazineService:  # noqa: ANN001
    return MagazineService(container.ask, container.sf, lambda: container.settings.data_path)
