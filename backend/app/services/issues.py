"""🐞 Compatibility log for Browser Play: games that did not work, with the evidence to investigate later.

Reports come from two places:
  * automatically — the title could not be loaded, the emulator never started, the screen froze while loading
    (the 🛟 rescue), or the emulator reported errors;
  * from the player — ⚑ Report a problem (category + note).
Each report keeps the diagnostics captured at that moment (file, format, SHA-256, emulator and console build,
browser, speed, what the screen showed, recent input, emulator errors) and a screenshot. Repeated automatic
reports of the same problem with the same file are counted on one entry. Every entry gets rule-based likely
causes; 🔍 Investigate adds an AI analysis. When the game later reaches gameplay, open entries are marked
"worked since" — a hint that the problem was intermittent or fixed.
"""

from __future__ import annotations

import base64
import csv
import io
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.models.db import Game, GameIssue

from .ai_json import ask_json, strs
from .input_profiles import boot_media, media_sha256

log = logging.getLogger("c64.issues")

CATEGORIES = {
    "wont_load": "Won't load", "no_start": "Never starts", "hangs": "Hangs / freezes", "crash": "Crashes / resets",
    "graphics": "Graphics glitch", "sound": "Sound problem", "controls": "Controls don't work", "slow": "Too slow",
    "other": "Other",
}
STATUSES = ("open", "investigating", "fixed", "wontfix")
MAX_SCREENSHOT = 600_000

SYSTEM = """You investigate why a Commodore 64 game does not work in a browser emulator (EmulatorJS running VICE
x64sc, PAL, true drive emulation off / auto-load warp on, keyboard + joystick port 2 by default).
From the report and diagnostics, explain the most likely cause and concrete next steps the owner can try
(another release: EasyFlash / OneLoad64 / a different crack, disk swap, joystick port, NTSC/PAL, fast mode,
playing it on the real C64 Ultimate instead). Be honest when the evidence is thin.
Respond with ONE JSON object only:
{"summary": "<one sentence>", "likelyCause": "<short>", "confidence": "high|medium|low",
 "steps": ["<up to 5 concrete steps>"]}"""


def _utc(ts: datetime | None) -> datetime | None:
    return ts.replace(tzinfo=UTC) if ts is not None and ts.tzinfo is None else ts


class IssueService:
    def __init__(self, ask_service, session_factory, data_path):  # noqa: ANN001
        self.ask = ask_service
        self.sf = session_factory
        self._data_path = data_path

    def _shot_path(self, issue_id: int) -> Path:
        folder = Path(self._data_path()) / "issues"
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"{issue_id}.png"

    # ---------------------------------------------------------------- report
    def report(self, game_id: int | None, *, category: str, source: str = "auto", note: str | None = None,
               diagnostics: dict[str, Any] | None = None, screenshot_b64: str | None = None,
               title: str | None = None) -> dict[str, Any]:
        if category not in CATEGORIES:
            raise ValueError("unknown category")
        diagnostics = _clean_diag(diagnostics or {})
        now = datetime.now(UTC)
        with self.sf() as s:
            g = s.get(Game, game_id) if game_id else None
            if game_id and not g:
                raise LookupError("game not found")
            media = boot_media(g) if g else None
            sha = media_sha256(media) if media else None
            file_name = str(diagnostics.get("fileName") or (Path(media.path).name if media else "") or "")[:255] or None
            if g:
                diagnostics.setdefault("format", g.format)
                diagnostics.setdefault("numDisks", g.num_disks)
                if category == "hangs":  # the 🛟 rescue warns up front next time
                    hangs = dict((g.extra or {}).get("hangs") or {})
                    hangs.update(count=int(hangs.get("count", 0)) + 1, last=time.time())
                    g.extra = {**(g.extra or {}), "hangs": hangs}
            existing = None
            if source == "auto":
                existing = s.scalars(select(GameIssue).where(
                    GameIssue.game_id == game_id, GameIssue.category == category, GameIssue.source == "auto",
                    GameIssue.sha256 == sha, GameIssue.status.in_(("open", "investigating")))).first()
            if existing:
                existing.occurrences += 1
                existing.last_seen_at = now
                existing.diagnostics = diagnostics
                issue = existing
            else:
                issue = GameIssue(game_id=game_id, title=(g.title if g else title or "Unknown game")[:255],
                                  file_name=file_name, sha256=sha, source=source, category=category,
                                  note=(note or "").strip()[:1000] or None, diagnostics=diagnostics,
                                  created_at=now, last_seen_at=now)
                s.add(issue)
            s.commit()
            issue_id = issue.id
        if screenshot_b64:
            try:
                data = base64.b64decode(screenshot_b64.split(",")[-1], validate=True)
                if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) <= MAX_SCREENSHOT:
                    self._shot_path(issue_id).write_bytes(data)
            except (ValueError, OSError) as exc:
                log.info("issue screenshot not kept: %s", exc)
        return self.get(issue_id)

    def mark_worked(self, game_id: int) -> int:
        """The game reached gameplay: note it on its open entries (maybe intermittent, maybe fixed)."""
        with self.sf() as s:
            rows = list(s.scalars(select(GameIssue).where(GameIssue.game_id == game_id,
                                                         GameIssue.status.in_(("open", "investigating")))))
            for r in rows:
                r.worked_at = datetime.now(UTC)
            s.commit()
            return len(rows)

    # ---------------------------------------------------------------- read / manage
    def get(self, issue_id: int) -> dict[str, Any]:
        with self.sf() as s:
            r = s.get(GameIssue, issue_id)
            if not r:
                raise LookupError("report not found")
            g = s.get(Game, r.game_id) if r.game_id else None
            return self._dict(r, g)

    def list(self, status: str | None = None, game_id: int | None = None) -> list[dict[str, Any]]:
        with self.sf() as s:
            stmt = select(GameIssue).order_by(GameIssue.last_seen_at.desc())
            if status == "active":
                stmt = stmt.where(GameIssue.status.in_(("open", "investigating")))
            elif status in STATUSES:
                stmt = stmt.where(GameIssue.status == status)
            if game_id:
                stmt = stmt.where(GameIssue.game_id == game_id)
            rows = list(s.scalars(stmt.limit(500)))
            games = {g.id: g for g in s.scalars(select(Game).where(Game.id.in_({r.game_id for r in rows if r.game_id})))}
            return [self._dict(r, games.get(r.game_id)) for r in rows]

    def update(self, issue_id: int, status: str | None = None, resolution: str | None = None,
               note: str | None = None) -> dict[str, Any]:
        with self.sf() as s:
            r = s.get(GameIssue, issue_id)
            if not r:
                raise LookupError("report not found")
            if status is not None:
                if status not in STATUSES:
                    raise ValueError("unknown status")
                r.status = status
            if resolution is not None:
                r.resolution = resolution.strip()[:1000] or None
            if note is not None:
                r.note = note.strip()[:1000] or None
            s.commit()
        return self.get(issue_id)

    def delete(self, issue_id: int) -> None:
        with self.sf() as s:
            r = s.get(GameIssue, issue_id)
            if r:
                s.delete(r)
                s.commit()
        self._shot_path(issue_id).unlink(missing_ok=True)

    def screenshot(self, issue_id: int) -> Path | None:
        p = self._shot_path(issue_id)
        return p if p.exists() else None

    def export_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "reported", "last seen", "times", "game", "file", "sha256", "source", "problem", "status",
                    "note", "resolution", "worked since", "likely causes", "format", "fps", "browser", "errors"])
        for i in self.list():
            d = i["diagnostics"]
            w.writerow([i["id"], i["createdAt"], i["lastSeenAt"], i["occurrences"], i["title"], i["fileName"], i["sha256"],
                        i["source"], i["categoryLabel"], i["status"], i["note"] or "", i["resolution"] or "",
                        i["workedAt"] or "", " | ".join(i["likelyCauses"]), d.get("format", ""), d.get("fps", ""),
                        d.get("userAgent", ""), " | ".join(d.get("errors") or [])])
        return buf.getvalue()

    # ---------------------------------------------------------------- 🔍 investigate
    async def investigate(self, issue_id: int) -> dict[str, Any]:
        issue = self.get(issue_id)
        d = issue["diagnostics"]
        lines = [f"Game: {issue['title']} (file {issue['fileName'] or '?'}, format {d.get('format', '?')}, "
                 f"{d.get('numDisks', 1)} disk(s))",
                 f"Problem: {issue['categoryLabel']} — reported {issue['source']}, seen {issue['occurrences']}×"
                 + (f"; the game has reached gameplay since ({issue['workedAt']})" if issue["workedAt"] else "")]
        if issue["note"]:
            lines.append(f"Player's note: {issue['note']}")
        for key in ("secondsSinceStart", "fps", "screenMode", "state", "screenText", "recentInputs", "errors",
                    "audio", "port", "keymap", "disk", "userAgent", "loadError"):
            if d.get(key) not in (None, "", []):
                lines.append(f"{key}: {d[key]}")
        lines.append("Rule-based likely causes: " + "; ".join(issue["likelyCauses"]))
        data, _ = await ask_json(self.ask, SYSTEM, "\n".join(lines), max_tokens=1500, what="investigation")
        analysis = {"summary": str(data.get("summary") or "").strip()[:300],
                    "likelyCause": str(data.get("likelyCause") or "").strip()[:160],
                    "confidence": data.get("confidence") if data.get("confidence") in ("high", "medium", "low") else "low",
                    "steps": strs(data.get("steps"), 5, 200), "at": time.time(),
                    "model": getattr(self.ask.provider, "model", None)}
        with self.sf() as s:
            r = s.get(GameIssue, issue_id)
            r.analysis = analysis
            if r.status == "open":
                r.status = "investigating"
            s.commit()
        return self.get(issue_id)

    # ---------------------------------------------------------------- helpers
    def _dict(self, r: GameIssue, g: Game | None) -> dict[str, Any]:
        return {"id": r.id, "gameId": r.game_id, "title": r.title, "fileName": r.file_name, "sha256": r.sha256,
                "source": r.source, "category": r.category, "categoryLabel": CATEGORIES.get(r.category, r.category),
                "note": r.note, "status": r.status, "resolution": r.resolution, "occurrences": r.occurrences,
                "createdAt": _utc(r.created_at).isoformat() if r.created_at else None,
                "lastSeenAt": _utc(r.last_seen_at).isoformat() if r.last_seen_at else None,
                "workedAt": _utc(r.worked_at).isoformat() if r.worked_at else None,
                "diagnostics": r.diagnostics or {}, "analysis": r.analysis,
                "hasScreenshot": self._shot_path(r.id).exists(),
                "coverUrl": g.cover_url if g else None, "likelyCauses": likely_causes(r, g)}


def likely_causes(r: GameIssue, g: Game | None) -> list[str]:
    """Quick, rule-based first guesses from the file and the diagnostics."""
    d = r.diagnostics or {}
    fmt = str(d.get("format") or (g.format if g else "") or "").lower()
    title = (r.title or "").lower()
    out = []
    if d.get("loadError"):
        out.append(f"The console could not prepare the file: {d['loadError']}")
    if fmt == "tap":
        out.append("Tape image: tapes load slowly and custom tape loaders often fail in emulation — "
                   "try a disk or cartridge release.")
    if fmt == "g64":
        out.append("G64 is a copy-protected disk image that needs exact drive timing — "
                   "try a cracked D64 or an EasyFlash release.")
    if fmt == "d64" and r.category in ("hangs", "no_start", "crash"):
        out.append("Likely a custom fast loader (crack intro / trainer / game loader) — "
                   "an EasyFlash or OneLoad64 release usually works.")
    if int(d.get("numDisks") or (g.num_disks if g else 1) or 1) > 1:
        out.append("Multi-disk game: it may be waiting for disk 2 / side B — insert it with the disk picker.")
    if "ntsc" in title or "ntsc" in str(r.file_name or "").lower():
        out.append("NTSC release on a PAL emulator: timing-sensitive code can crash or run wrong.")
    fps = d.get("fps")
    if isinstance(fps, (int, float)) and 0 < fps < 40:
        out.append(f"Emulation ran at {fps} fps (PAL is 50): this device is too slow — try ⚡ Fast mode or a faster device.")
    if r.category == "controls":
        out.append("Try the other joystick port (Ctrl+Alt+P) and Type / Play mode; some games read port 1.")
    if r.category == "sound" and "suspended" in str(d.get("audio") or ""):
        out.append("The browser kept sound off until a tap or key press on the game.")
    if d.get("errors"):
        out.append("The emulator reported errors: " + "; ".join(str(e) for e in d["errors"][:2]))
    if not out:
        out.append("No obvious cause from the file type — try 🔍 Investigate, another release, or the real C64 Ultimate.")
    return out


def _clean_diag(d: dict[str, Any]) -> dict[str, Any]:
    """Keep diagnostics small and plain (it is stored and shown)."""
    out: dict[str, Any] = {}
    for k, v in list(d.items())[:40]:
        if k == "screenshot":
            continue
        if isinstance(v, str):
            out[k] = v[:2000]
        elif isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = [str(x)[:300] for x in v[:20]]
        elif isinstance(v, dict):
            out[k] = {str(a)[:40]: str(b)[:300] for a, b in list(v.items())[:20]}
    return out
