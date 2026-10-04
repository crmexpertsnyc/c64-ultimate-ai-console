"""📟 BBS directory: public telnet bulletin boards, with favorites, notes, reachability and admin approval.

* Records come only from the public sources in bbs_sources (each keeps its source link and the date it was seen);
  boards are merged by normalized host:port. A manual refresh any time, and a monthly one (Settings → Sources &
  updates). Until a source has been imported, the page says "directory setup pending".
* New boards are "pending" until an admin approves them; only approved boards can be opened in the browser terminal.
* Reachability = a plain TCP connect (to a checked public address, 5 s timeout), a few boards per run, each board at
  most weekly, failing boards backing off up to 60 days. "Reachable" means something answered on that port — not
  that the board works. The last successful check is kept separately from the latest status.
* Compatibility (PETSCII / ANSI): confirmed | unverified | unknown. Only an admin sets "confirmed".
* The browser-terminal sessions themselves are limited here (RelayLimits); the relay is in api/bbs_api.py.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.models.bbs import BbsBoard, BbsImportRun, BbsUserData
from app.services import bbs_art, bbs_net
from app.services.bbs_sources import SOURCES, Record, SourceError, normalize_host

COMPAT = ("confirmed", "unverified", "unknown")
CHECKS_PER_RUN = 25
CHECK_TIMEOUT = 5.0
CHECK_SPACING = 2.0
RECHECK = timedelta(days=7)
ART_RETRY = timedelta(days=30)
MAX_BACKOFF = timedelta(days=60)
_RANK = {"unknown": 0, "unverified": 1, "confirmed": 2}


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).isoformat()


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


class RelayLimits:
    """How many browser-terminal sessions may run: overall, per device, and how often they may start."""

    def __init__(self, max_sessions: int = 4, per_client: int = 2, connects: int = 10, window: float = 600.0):
        self.max_sessions, self.per_client, self.connects, self.window = max_sessions, per_client, connects, window
        self.active: dict[str, dict[str, Any]] = {}          # session id → {client, board, started}
        self._starts: dict[str, list[float]] = {}

    def acquire(self, sid: str, client: str, board_id: int) -> str | None:
        """None if the session may start (and it's now counted), else the reason it may not."""
        now = time.monotonic()
        recent = [t for t in self._starts.get(client, []) if now - t < self.window]
        self._starts[client] = recent
        if len(recent) >= self.connects:
            return "too many connections in the last few minutes — wait a little and try again"
        if len(self.active) >= self.max_sessions:
            return "the console already has its maximum of BBS sessions open"
        if sum(1 for a in self.active.values() if a["client"] == client) >= self.per_client:
            return "this device already has its maximum of BBS sessions open — close one first"
        recent.append(now)
        self.active[sid] = {"client": client, "board": board_id, "started": now}
        return None

    def release(self, sid: str) -> None:
        self.active.pop(sid, None)


class BbsService:
    def __init__(self, container):  # noqa: ANN001
        self.c = container
        self.sources = SOURCES
        self.resolver: bbs_net.Resolver = bbs_net.system_resolve      # tests inject fakes
        self.opener: bbs_net.Opener = bbs_net.tcp_open
        self.http = None
        s = container.settings
        self.limits = RelayLimits(max_sessions=max(1, int(getattr(s, "BBS_MAX_SESSIONS", 4) or 4)))
        self._checking = False

    @property
    def settings(self):  # noqa: ANN201
        return self.c.settings

    # ------------------------------------------------------------ views
    def to_dict(self, b: BbsBoard, user: BbsUserData | None = None, admin: bool = False) -> dict[str, Any]:
        thumb = self._thumb_url(b)
        d = {"id": b.id, "name": b.name, "description": b.description, "host": b.host, "port": b.port,
             "protocol": b.protocol, "location": b.location, "website": b.website, "software": b.software,
             "petscii": b.petscii, "ansi": b.ansi, "compatNote": b.compat_note,
             "sources": b.sources or [], "review": b.review, "approved": b.review == "approved",
             "status": b.status, "lastCheckAt": _iso(b.last_check_at), "lastOkAt": _iso(b.last_ok_at),
             "listed": b.listed, "addedAt": _iso(b.created_at),
             "thumbUrl": thumb, "artPage": b.art_page if thumb else None,
             "favorite": bool(user and user.favorite), "notes": (user.notes if user else None) or "",
             "lastConnectedAt": _iso(user.last_connected_at) if user else None}
        if admin:
            d.update({"lastError": b.last_error, "failCount": b.fail_count, "nextCheckAt": _iso(b.next_check_at),
                      "reviewedAt": _iso(b.reviewed_at)})
        return d

    @property
    def art_dir(self):  # noqa: ANN201
        return self.settings.data_path / "bbs-art"

    def art_file(self, board_id: int):  # noqa: ANN201
        f = self.art_dir / f"{int(board_id)}.png"
        return f if f.is_file() else None

    def _thumb_url(self, b: BbsBoard) -> str | None:
        f = self.art_file(b.id)
        return f"/api/bbs/art/{b.id}?v={int(f.stat().st_mtime)}" if f else None

    def _user_rows(self, s, ids: list[int]) -> dict[int, BbsUserData]:  # noqa: ANN001
        from app.profiles import profile_id
        if not ids:
            return {}
        rows = s.scalars(select(BbsUserData).where(BbsUserData.profile_id == profile_id(),
                                                   BbsUserData.board_id.in_(ids)))
        return {r.board_id: r for r in rows}

    def list(self, q: str | None = None, terminal: str | None = None, favorites: bool = False,
             reachable: bool = False, review: str | None = None, admin: bool = False) -> dict[str, Any]:
        with self.c.sf() as s:
            stmt = select(BbsBoard)
            if admin and review in ("pending", "approved", "rejected"):
                stmt = stmt.where(BbsBoard.review == review)
            elif not admin:
                stmt = stmt.where(BbsBoard.review == "approved")
            elif review != "all":
                stmt = stmt.where(BbsBoard.review != "rejected")
            if q:
                like = f"%{q.strip()[:80]}%"
                stmt = stmt.where(BbsBoard.name.ilike(like) | BbsBoard.description.ilike(like)
                                  | BbsBoard.location.ilike(like) | BbsBoard.host.ilike(like))
            if terminal == "petscii":
                stmt = stmt.where(BbsBoard.petscii.in_(("confirmed", "unverified")))
            elif terminal == "ansi":
                stmt = stmt.where(BbsBoard.ansi.in_(("confirmed", "unverified")))
            if reachable:
                stmt = stmt.where(BbsBoard.last_ok_at >= datetime.now(UTC) - timedelta(days=30))
            boards = s.scalars(stmt.order_by(BbsBoard.name)).all()
            users = self._user_rows(s, [b.id for b in boards])
            if favorites:
                boards = [b for b in boards if users.get(b.id) and users[b.id].favorite]
            items = [self.to_dict(b, users.get(b.id), admin) for b in boards]
            counts = {r: 0 for r in ("pending", "approved", "rejected")}
            for (rv,) in s.execute(select(BbsBoard.review)):
                counts[rv] = counts.get(rv, 0) + 1
        items.sort(key=lambda d: (not d["favorite"], d["name"].lower()))
        return {"boards": items, "counts": counts, **self.status()}

    def get(self, board_id: int, admin: bool = False) -> dict[str, Any]:
        with self.c.sf() as s:
            b = s.get(BbsBoard, board_id)
            if b is None or (not admin and b.review != "approved"):
                raise LookupError("no such board")
            return self.to_dict(b, self._user_rows(s, [b.id]).get(b.id), admin)

    def board(self, board_id: int) -> BbsBoard | None:
        with self.c.sf() as s:
            b = s.get(BbsBoard, board_id)
            if b is not None:
                s.expunge(b)
            return b

    def status(self) -> dict[str, Any]:
        with self.c.sf() as s:
            last = s.scalars(select(BbsImportRun).order_by(BbsImportRun.at.desc()).limit(1)).first()
            total = s.query(BbsBoard).count()
            last_ok = s.scalars(select(BbsBoard.last_check_at).order_by(BbsBoard.last_check_at.desc()).limit(1)).first()
        enabled = [n for n, src in self.sources.items() if getattr(self.settings, src.setting, False)]
        imported = bool(last and any(not (v or {}).get("error") for v in (last.results or {}).values()))
        return {"setupPending": total == 0 and not imported,
                "sources": [{"name": n, "label": src.label, "url": src.url, "terms": src.terms, "setting": src.setting,
                             "enabled": n in enabled} for n, src in self.sources.items()],
                "lastImport": {"at": _iso(last.at), "results": last.results} if last else None,
                "lastCheckAt": _iso(last_ok), "total": total,
                "dialOnC64": bool(getattr(self.settings, "BBS_DIAL_ON_C64", False)),
                "autoApprove": bool(getattr(self.settings, "BBS_AUTO_APPROVE", False))}

    # ------------------------------------------------------------ import
    def _merge(self, s, rec: Record, now: datetime) -> str:  # noqa: ANN001
        """Insert or update one record → 'added' | 'updated'."""
        src = {"source": rec.source, "label": rec.source_label, "url": rec.source_url,
               "seenAt": now.date().isoformat(), "listingUpdated": rec.listing_updated}
        b = s.scalars(select(BbsBoard).where(BbsBoard.host == rec.host, BbsBoard.port == rec.port)).first()
        if b is None:
            b = BbsBoard(name=rec.name, host=rec.host, port=rec.port, protocol=rec.protocol,
                         description=rec.description, location=rec.location, website=rec.website,
                         software=rec.software, petscii=rec.petscii, ansi=rec.ansi, compat_note=rec.compat_note,
                         sources=[src], review="pending", status="unknown", listed=True, created_at=now, updated_at=now)
            s.add(b)
            s.flush()
            return "added"
        sources = [x for x in (b.sources or []) if x.get("source") != rec.source] + [src]
        b.sources = sources
        b.listed = True
        for attr in ("description", "location", "website", "software"):
            if getattr(rec, attr) and not getattr(b, attr):
                setattr(b, attr, getattr(rec, attr))
        for attr in ("petscii", "ansi"):                      # sources only ever raise unknown → unverified
            if _RANK[getattr(rec, attr)] > _RANK[getattr(b, attr)] and getattr(rec, attr) != "confirmed":
                setattr(b, attr, getattr(rec, attr))
                if rec.compat_note:
                    b.compat_note = rec.compat_note
        b.updated_at = now
        return "updated"

    async def refresh(self, only: str | None = None) -> dict[str, Any]:
        """Run every enabled source (or one) and merge what it lists."""
        now = datetime.now(UTC)
        results: dict[str, Any] = {}
        seen: dict[str, set[tuple[str, int]]] = {}
        for name, src in self.sources.items():
            if only and name != only:
                continue
            if not getattr(self.settings, src.setting, False):
                continue
            try:
                records = await src.fetch(self.http)
            except SourceError as exc:
                results[name] = {"error": str(exc)[:200]}
                continue
            dedup: dict[tuple[str, int], Record] = {}
            for r in records:
                dedup.setdefault((r.host, r.port), r)
            added = updated = 0
            with self.c.sf() as s:
                for r in dedup.values():
                    if self._merge(s, r, now) == "added":
                        added += 1
                    else:
                        updated += 1
                s.commit()
            seen[name] = set(dedup)
            results[name] = {"records": len(dedup), "added": added, "updated": updated}
        if seen:                                               # boards that dropped off every list that just ran
            with self.c.sf() as s:
                for b in s.scalars(select(BbsBoard)):
                    names = {x.get("source") for x in (b.sources or [])}
                    ran = names & set(seen)
                    if ran and names <= set(seen) and not any((b.host, b.port) in seen[n] for n in ran):
                        b.listed = False
                s.commit()
        if not results:
            results["none"] = {"error": "no directory source is switched on (Settings → BBS directory)"}
        with self.c.sf() as s:
            s.add(BbsImportRun(at=now, results=results))
            s.commit()
        added = sum(v.get("added", 0) for v in results.values())
        errors = [k for k, v in results.items() if v.get("error")]
        return {"added": added, "updated": sum(v.get("updated", 0) for v in results.values()),
                "results": results, **({"errors": errors} if errors else {})}

    def import_records(self, records: list[Record]) -> dict[str, int]:
        """Merge records directly (tests and fixtures)."""
        now = datetime.now(UTC)
        out = {"added": 0, "updated": 0}
        seen: set[tuple[str, int]] = set()
        with self.c.sf() as s:
            for r in records:
                if (r.host, r.port) in seen:
                    continue
                seen.add((r.host, r.port))
                out[self._merge(s, r, now)] += 1
            s.commit()
        return out

    # ------------------------------------------------------------ admin
    def review(self, board_id: int, decision: str) -> dict[str, Any]:
        if decision not in ("approved", "rejected", "pending"):
            raise ValueError("approve, reject or reset to pending")
        with self.c.sf() as s:
            b = s.get(BbsBoard, board_id)
            if b is None:
                raise LookupError("no such board")
            b.review, b.reviewed_at = decision, datetime.now(UTC)
            s.commit()
        return self.get(board_id, admin=True)

    def approve_reachable(self) -> dict[str, int]:
        """Approve every pending board whose address answered its last check (unreachable ones stay pending)."""
        now = datetime.now(UTC)
        with self.c.sf() as s:
            rows = s.scalars(select(BbsBoard).where(BbsBoard.review == "pending", BbsBoard.status == "reachable")).all()
            for b in rows:
                b.review, b.reviewed_at = "approved", now
            s.commit()
            left = s.query(BbsBoard).filter(BbsBoard.review == "pending").count()
        return {"approved": len(rows), "stillPending": left}

    def edit(self, board_id: int, data: dict[str, Any]) -> dict[str, Any]:
        with self.c.sf() as s:
            b = s.get(BbsBoard, board_id)
            if b is None:
                raise LookupError("no such board")
            if "protocol" in data:
                if data["protocol"] not in ("telnet", "raw"):
                    raise ValueError("protocol is telnet or raw")
                b.protocol = data["protocol"]
            for attr in ("petscii", "ansi"):
                if attr in data:
                    if data[attr] not in COMPAT:
                        raise ValueError(f"{attr} is confirmed, unverified or unknown")
                    setattr(b, attr, data[attr])
            for attr in ("name", "description", "location", "website", "compat_note"):
                if attr in data:
                    v = (data[attr] or "").strip() or None
                    if attr == "name" and not v:
                        raise ValueError("a board needs a name")
                    if attr == "website" and v and not v.lower().startswith(("http://", "https://")):
                        raise ValueError("the website must start with http:// or https://")
                    setattr(b, attr, v)
            b.updated_at = datetime.now(UTC)
            s.commit()
        return self.get(board_id, admin=True)

    def add_manual(self, data: dict[str, Any], who: str = "admin") -> dict[str, Any]:
        host = normalize_host(data.get("host", ""))
        port = int(data.get("port") or 23)
        if not host:
            raise ValueError("enter a hostname or IP address")
        if not 1 <= port <= 65535 or port in bbs_net.BLOCKED_PORTS:
            raise ValueError(f"port {port} isn't allowed for BBS connections")
        src_url = (data.get("source_url") or "").strip() or None
        if src_url and not src_url.lower().startswith(("http://", "https://")):
            raise ValueError("the source link must start with http:// or https://")
        rec = Record(name=(data.get("name") or host).strip()[:120], host=host, port=port, source="manual",
                     source_label=f"Added by {who}", source_url=src_url or "",
                     description=(data.get("description") or "").strip() or None,
                     website=(data.get("website") or "").strip() or None, protocol=data.get("protocol") or "telnet")
        if rec.protocol not in ("telnet", "raw"):
            raise ValueError("protocol is telnet or raw")
        with self.c.sf() as s:
            self._merge(s, rec, datetime.now(UTC))
            s.commit()
            b = s.scalars(select(BbsBoard).where(BbsBoard.host == host, BbsBoard.port == port)).one()
            bid = b.id
        return self.get(bid, admin=True)

    # ------------------------------------------------------------ favorites & notes
    def set_user(self, board_id: int, favorite: bool | None = None, notes: str | None = None,
                 connected: bool = False) -> dict[str, Any]:
        from app.profiles import profile_id
        with self.c.sf() as s:
            b = s.get(BbsBoard, board_id)
            if b is None or b.review == "rejected":
                raise LookupError("no such board")
            u = s.scalars(select(BbsUserData).where(BbsUserData.board_id == board_id,
                                                    BbsUserData.profile_id == profile_id())).first()
            if u is None:
                u = BbsUserData(board_id=board_id, profile_id=profile_id(), favorite=False)
                s.add(u)
            if favorite is not None:
                u.favorite = favorite
            if notes is not None:
                u.notes = notes.strip()[:4000] or None
            if connected:
                u.last_connected_at = datetime.now(UTC)
            u.updated_at = datetime.now(UTC)
            s.commit()
            return {"favorite": u.favorite, "notes": u.notes or "", "lastConnectedAt": _iso(u.last_connected_at)}

    # ------------------------------------------------------------ reachability
    async def check_one(self, board_id: int) -> dict[str, Any]:
        b = self.board(board_id)
        if b is None:
            raise LookupError("no such board")
        ok, err = await self._probe(b.host, b.port)
        self._record_check(board_id, ok, err)
        return self.get(board_id, admin=True)

    async def _probe(self, host: str, port: int) -> tuple[bool, str | None]:
        try:
            _reader, writer, _ip = await bbs_net.connect_pinned(host, port, resolver=self.resolver,
                                                                 opener=self.opener, timeout=CHECK_TIMEOUT)
        except bbs_net.DestinationError as exc:
            return False, str(exc)[:200]
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 2)
        except (OSError, TimeoutError):
            pass
        return True, None

    def _record_check(self, board_id: int, ok: bool, err: str | None) -> None:
        now = datetime.now(UTC)
        with self.c.sf() as s:
            b = s.get(BbsBoard, board_id)
            if b is None:
                return
            b.last_check_at = now
            if ok:
                b.status, b.last_ok_at, b.last_error, b.fail_count = "reachable", now, None, 0
                b.next_check_at = now + RECHECK
                if b.review == "pending" and getattr(self.settings, "BBS_AUTO_APPROVE", False):
                    b.review, b.reviewed_at = "approved", now        # auto-approval (BBS_AUTO_APPROVE)
            else:
                b.status, b.last_error = "unreachable", err
                b.fail_count = (b.fail_count or 0) + 1
                b.next_check_at = now + min(MAX_BACKOFF, timedelta(days=2 ** min(b.fail_count, 6)))
            s.commit()

    async def check_due(self, limit: int = CHECKS_PER_RUN, spacing: float = CHECK_SPACING) -> dict[str, Any]:
        """A few boards per run, oldest first, never more often than weekly (backing off on failures)."""
        if self._checking:
            return {"checked": 0}
        self._checking = True
        try:
            now = datetime.now(UTC)
            with self.c.sf() as s:
                rows = s.execute(select(BbsBoard.id, BbsBoard.host, BbsBoard.port, BbsBoard.next_check_at)
                                 .where(BbsBoard.review != "rejected", BbsBoard.listed.is_(True))).all()
            due = [r for r in rows if r.next_check_at is None or _aware(r.next_check_at) <= now]
            due.sort(key=lambda r: _aware(r.next_check_at) or datetime.min.replace(tzinfo=UTC))
            ok = 0
            for i, r in enumerate(due[:limit]):
                if i and spacing:
                    await asyncio.sleep(spacing)
                good, err = await self._probe(r.host, r.port)
                self._record_check(r.id, good, err)
                ok += good
            return {"checked": min(len(due), limit), "found": ok}
        finally:
            self._checking = False


    # ------------------------------------------------------------ thumbnails
    async def fetch_art(self, limit: int = 20, gap: float = 1.0, force: bool = False) -> dict[str, Any]:
        """Thumbnails for approved boards from their own web pages; each board is retried at most monthly."""
        now = datetime.now(UTC)
        with self.c.sf() as s:
            rows = s.execute(select(BbsBoard.id, BbsBoard.name, BbsBoard.host, BbsBoard.website, BbsBoard.art_checked_at)
                             .where(BbsBoard.review == "approved")).all()
        todo = [r for r in rows if force or (not self.art_file(r.id) and
                (r.art_checked_at is None or _aware(r.art_checked_at) <= now - ART_RETRY))]
        found = 0
        self.art_dir.mkdir(parents=True, exist_ok=True)
        for i, r in enumerate(todo[:limit]):
            if i and gap:
                await asyncio.sleep(gap)
            art_url = page = None
            try:
                png, art_url, page = await bbs_art.find_art(r.name, r.host, r.website, resolver=self.resolver,
                                                            http=self.http)
                (self.art_dir / f"{r.id}.png").write_bytes(png)
                found += 1
            except bbs_art.ArtError:
                pass
            with self.c.sf() as s:
                b = s.get(BbsBoard, r.id)
                if b is not None:
                    b.art_checked_at = datetime.now(UTC)
                    if art_url:
                        b.art_url, b.art_page = art_url[:600], (page or "")[:600] or None
                    s.commit()
        return {"checked": min(len(todo), limit), "found": found}

    def clear_art(self, board_id: int) -> dict[str, Any]:
        """Remove a board's thumbnail (wrong picture); it isn't looked for again for years unless forced."""
        f = self.art_file(board_id)
        if f:
            f.unlink(missing_ok=True)
        with self.c.sf() as s:
            b = s.get(BbsBoard, board_id)
            if b is None:
                raise LookupError("no such board")
            b.art_url = b.art_page = None
            b.art_checked_at = datetime.now(UTC) + timedelta(days=3650)
            s.commit()
        return self.get(board_id, admin=True)


def attach(container) -> BbsService:  # noqa: ANN001
    return BbsService(container)
