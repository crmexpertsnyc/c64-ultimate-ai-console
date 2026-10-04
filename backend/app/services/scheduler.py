"""🔄 Update scheduler — keeps every dynamic source fresh on its own timetable.

One loop, one job at a time (polite to the sites, light on the machine). Each job has a default interval
(30 min for news … weekly for web research and the magazine archive) that can be changed or switched off in
Settings → Sources & updates, and a "run now". The last result of every job is remembered across restarts, so a
restart doesn't re-crawl everything. The whole scheduler follows the NEWS_MONITOR setting (off in tests).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.models.scheduler import CrawlJob

log = logging.getLogger("c64.scheduler")

MIN, HOUR, DAY = 60, 3600, 86400
CHOICES = {"15m": 15 * MIN, "30m": 30 * MIN, "1h": HOUR, "6h": 6 * HOUR, "12h": 12 * HOUR, "1d": DAY, "7d": 7 * DAY,
           "30d": 30 * DAY}
JOB_TIMEOUT = 45 * MIN
TICK = 30


@dataclass
class Job:
    key: str
    label: str
    area: str                                   # news | releases | events | magazines | hardware | firmware
    every: int                                  # default interval (seconds)
    run: Callable[[], Awaitable[Any]]
    available: Callable[[], str | None] = lambda: None   # a reason it can't run now (missing keys…), or None
    note: str = ""


def summarize(result: Any) -> str:
    """A job's return value → one short line for the status table."""
    if isinstance(result, str):
        return result[:300]
    if isinstance(result, int):
        return f"{result} updated"
    if isinstance(result, dict):
        parts = []
        for k in ("added", "downloaded", "found", "indexed", "updated", "new", "checked", "items"):
            v = result.get(k)
            if isinstance(v, int | float) and not isinstance(v, bool):
                parts.append(f"{v} {k}")
        if result.get("latest"):
            parts.append(f"latest {result['latest']}" + (" — newer than yours" if result.get("newer") else ""))
        errors = result.get("errors")
        if errors:
            parts.append(f"couldn't reach {', '.join(errors) if isinstance(errors, dict | list) else errors}"[:120])
        return ", ".join(parts)[:300] or "done"
    return "done"


class Scheduler:
    def __init__(self, sf, enabled: Callable[[], bool], hub=None):  # noqa: ANN001
        self.sf, self._enabled, self.hub = sf, enabled, hub
        self.jobs: dict[str, Job] = {}
        self.running: str | None = None
        self._task: asyncio.Task | None = None
        self._now_requests: list[str] = []
        self._started_at = time.time()

    def add(self, job: Job) -> None:
        self.jobs[job.key] = job

    # ------------------------------------------------------------ state
    def _row(self, s, key: str) -> CrawlJob:  # noqa: ANN001
        row = s.get(CrawlJob, key)
        if row is None:
            row = CrawlJob(key=key, enabled=True, runs=0)
            s.add(row)
        return row

    @staticmethod
    def _aware(dt: datetime | None) -> datetime | None:
        return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt

    def status(self) -> list[dict[str, Any]]:
        with self.sf() as s:
            rows = {r.key: r for r in s.query(CrawlJob).all()}
        out = []
        for job in self.jobs.values():
            r = rows.get(job.key)
            every = (r.every if r and r.every else None) or job.every
            last = self._aware(r.last_at) if r else None
            enabled = r.enabled if r else True
            blocked = job.available()
            out.append({"key": job.key, "label": job.label, "area": job.area, "note": job.note,
                        "every": every, "everyDefault": job.every, "enabled": enabled, "blocked": blocked,
                        "running": self.running == job.key, "queued": job.key in self._now_requests,
                        "lastAt": last.isoformat() if last else None, "lastOk": r.last_ok if r else None,
                        "lastSummary": r.last_summary if r else None, "lastError": r.last_error if r else None,
                        "lastSeconds": r.last_seconds if r else None, "runs": r.runs if r else 0,
                        "nextAt": ((last + timedelta(seconds=every)) if last else datetime.now(UTC)).isoformat()
                        if enabled and not blocked else None})
        return out

    def configure(self, key: str, every: int | None = None, enabled: bool | None = None) -> dict[str, Any]:
        if key not in self.jobs:
            raise LookupError(f"no such job {key!r}")
        if every is not None and every not in CHOICES.values():
            raise ValueError("choose one of the listed intervals")
        with self.sf() as s:
            row = self._row(s, key)
            if every is not None:
                row.every = None if every == self.jobs[key].every else every
            if enabled is not None:
                row.enabled = enabled
            s.commit()
        return next(j for j in self.status() if j["key"] == key)

    def request(self, key: str) -> None:
        if key not in self.jobs:
            raise LookupError(f"no such job {key!r}")
        if key not in self._now_requests and key != self.running:
            self._now_requests.append(key)
        self.ensure_loop()

    # ------------------------------------------------------------ running
    def _due(self) -> list[str]:
        now = datetime.now(UTC)
        due = []
        for j in self.status():
            if not j["enabled"] or j["blocked"]:
                continue
            last = datetime.fromisoformat(j["lastAt"]) if j["lastAt"] else None
            if last is None or (now - last).total_seconds() >= j["every"]:
                due.append(j["key"])
        return due

    async def run_job(self, key: str) -> dict[str, Any]:
        job = self.jobs[key]
        self.running = key
        t0 = time.time()
        ok, summary, error = True, "", None
        try:
            res = job.run()
            if inspect.isawaitable(res):
                res = await asyncio.wait_for(res, JOB_TIMEOUT)
            summary = summarize(res)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, tried again next time
            ok, error = False, f"{type(exc).__name__}: {str(exc)[:300]}"
            log.info("job %s failed: %s", key, error)
        finally:
            self.running = None
        with self.sf() as s:
            row = self._row(s, key)
            row.last_at, row.last_ok, row.last_seconds = datetime.now(UTC), ok, round(time.time() - t0, 1)
            row.last_summary, row.last_error = (summary or None), error
            row.runs = (row.runs or 0) + 1
            s.commit()
        if self.hub:
            self.hub.publish("updates", {"job": key, "ok": ok, "summary": summary})
        return {"key": key, "ok": ok, "summary": summary, "error": error}

    async def _loop(self) -> None:
        await asyncio.sleep(30)                         # let the console (and the C64 connection) start first
        while True:
            try:
                key = self._now_requests.pop(0) if self._now_requests else None
                if key is None and self._enabled():
                    due = self._due()
                    key = due[0] if due else None
                if key:
                    await self.run_job(key)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the scheduler keeps going
                log.warning("scheduler: %s", exc)
            await asyncio.sleep(TICK)

    def ensure_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="update-scheduler")

    def start(self) -> None:
        if self._enabled():
            self.ensure_loop()

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


def build(container) -> Scheduler:  # noqa: ANN001, C901
    """Every dynamic source of the console, with its default timetable."""
    c = container
    sch = Scheduler(c.sf, lambda: bool(c.settings.NEWS_MONITOR), c.hub)

    def ai_web() -> str | None:
        if not c.ask.provider.configured:
            return "needs an AI model (Settings → AI assistant)"
        if not c.ask.web_search_on:
            return "needs web search (a Brave Search key)"
        return None

    sch.add(Job("news", "News, new releases & videos", "news", 30 * MIN, c.news.refresh,
                note="CSDb releases, itch.io, Indie Retro News and the C64 YouTube channels"))
    sch.add(Job("release-stats", "Popularity of new releases", "releases", 6 * HOUR, c.news.update_stats,
                note="CSDb downloads, comments, votes and ratings; itch.io ratings"))
    events = getattr(c, "events", None)
    if events is not None:
        sch.add(Job("events-csdb", "Commodore scene events (CSDb)", "events", DAY, events.refresh,
                    note="CSDb's upcoming events, plus each event page's website and city"))
        if hasattr(events, "crawl_sources"):
            sch.add(Job("events-sources", "Demoparties worldwide (demoparty.net)", "events", DAY, events.crawl_sources,
                        note="Every upcoming demoparty, C64 and beyond"))
        if hasattr(events, "check_sites"):
            sch.add(Job("events-sites", "Official sites of recurring events", "events", 7 * DAY, events.check_sites,
                        note="VCF, retro gaming expos, pinball shows, Commodore shows… their next dates"))
        sch.add(Job("events-research", "🤖 Web research for retro events", "events", 7 * DAY, events.research,
                    available=ai_web, note="Retro expos, conferences, fairs and meetups — US first, then worldwide"))
    fw = getattr(c, "firmware", None)
    if fw is not None:
        sch.add(Job("firmware", "Firmware news", "firmware", DAY, fw.check,
                    note="Notification only — never downloads or installs firmware"))
    mags = getattr(c, "magazines", None)
    if mags is not None and hasattr(mags, "update_all"):
        sch.add(Job("magazines", "Magazine archive", "magazines", 7 * DAY, mags.update_all,
                    note="New scans on the Internet Archive; magazines you made searchable get their new issues indexed"))
    shop = getattr(c, "shop", None)
    if shop is not None:
        async def catalog() -> dict[str, Any]:
            await shop.refresh_remote(force=True)
            return await shop.fetch_images()
        sch.add(Job("hardware-catalog", "Hardware catalog & product photos", "hardware", DAY, catalog,
                    note="Your website's catalog (if set) and each product's photo"))
        if hasattr(shop, "crawl_products"):
            sch.add(Job("hardware-new", "New hardware from the makers", "hardware", DAY, shop.crawl_products,
                        note="New products in the C64 makers' shops"))
        sch.add(Job("price-watches", "💰 eBay price watches", "hardware", 6 * HOUR, shop.check_watches,
                    available=lambda: None if shop.ebay.configured else "needs eBay keys (Settings → Hardware shop & eBay)"))
    bbs = getattr(c, "bbs", None)
    if bbs is not None:
        sch.add(Job("bbs-directory", "BBS directory", "bbs", 30 * DAY, bbs.refresh,
                    note="Public telnet BBS lists (SyncTERM's directory; others only with permission)"))
        sch.add(Job("bbs-checks", "BBS reachability", "bbs", DAY, bbs.check_due,
                    note="A short TCP check of up to 25 boards a day, each at most weekly — no logins, no data. "
                         "With auto-approve on, boards that answer are approved"))
        sch.add(Job("bbs-art", "BBS thumbnails", "bbs", DAY, bbs.fetch_art,
                    note="Logos and screen shots from each approved board's own web page (never from the BBS itself)"))
    upd = getattr(c, "app_update", None)
    if upd is not None:
        sch.add(Job("app-update", "⬆ New version of the console", "system", DAY, upd.check,
                    note="Checks GitHub (or your git copy) for a newer version — never installs anything by itself"))
    backup = getattr(c, "backup", None)
    if backup is not None:
        async def run_backup() -> str:
            return (await asyncio.to_thread(backup.run))["summary"]
        sch.add(Job("backup", "💾 Backup of your data", "system", DAY, run_backup,
                    note="Library, saves, imports, screenshots, art and settings — to your backup folder"))
    return sch
