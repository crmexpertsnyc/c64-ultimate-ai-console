"""Application container: builds and wires every service once."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.ai.engine import CommandEngine
from app.ai.providers import make_provider
from app.ai.vision import VisionController
from app.config import ConfigStore, Settings
from app.library.scanner import ScanResult, scan_root
from app.models.db import init_db
from app.services.audit import AuditService
from app.services.catalog import CatalogService
from app.services.commands import CommandRouter
from app.services.device import DeviceService
from app.services.events import EventHub
from app.services.launcher import Launcher
from app.services.live import LiveService
from app.services.screenshots import ScreenshotService

log = logging.getLogger("c64.container")

DEVICE_KEYS = {"C64_ULTIMATE_HOST", "C64_ULTIMATE_PORT", "C64_ULTIMATE_PASSWORD", "C64_ULTIMATE_PROTOCOL",
               "SIMULATE_C64", "SIMULATE_PROFILE", "C64_UPLOAD_MODE", "C64_TYPE_DELAY_MS"}
BRIDGE_KEYS = {"JOYBRIDGE_HOST", "JOYBRIDGE_PORT"}
AI_KEYS = {"AI_PROVIDER", "AI_BASE_URL", "AI_MODEL", "AI_API_KEY", "AI_MAX_TOKENS", "AI_REASONING_EFFORT"}


class Container:
    def __init__(self, config: ConfigStore):
        self.config = config
        s = config.settings
        s.data_path.mkdir(parents=True, exist_ok=True)
        self.sf = init_db(s.database_url)
        self.hub = EventHub()
        self.audit = AuditService(self.sf, self.hub)
        self.device = DeviceService(config, self.hub, self.audit)
        self.launcher = Launcher(self.device, self.sf, self.hub, self.audit)
        self.device.session_provider = lambda: self.launcher.session.to_dict()
        self.provider = make_provider(s)
        self.engine = CommandEngine(self.provider)
        self.catalog = CatalogService(lambda: self.config.settings, self.sf)
        self.router = CommandRouter(self.device, self.launcher, self.engine, self.sf, self.audit, self.catalog)
        from app.logging_setup import register_secret
        from app.services.ask import AskService
        register_secret(s.BRAVE_API_KEY)
        register_secret(s.EBAY_CLIENT_SECRET)
        self.ask = AskService(lambda: self.config.settings, lambda: self.provider, self.sf, self.catalog)
        self.router.ask = self.ask
        from app.services.imports import ImportService
        self.imports = ImportService(lambda: self.config.settings, self.sf, cover_hook=self.ensure_cover)
        from app.services.guide import GuideService
        self.guides = GuideService(self.ask, self.sf)
        from app.services.input_profiles import InputProfiles
        self.input_profiles = InputProfiles(self.sf)
        from app.profiles import ProfileService, profile_id
        from app.services.recommend import RecommendService
        from app.services.taste import TasteService
        self.profiles = ProfileService(self.sf)
        self.taste = TasteService(self.sf)
        self.recommend = RecommendService(self.ask, self.taste, self.sf, lambda: self.config.settings.data_path)
        self.recommend.kids = lambda: self.profiles.kids(profile_id())
        self.router.taste = self.taste
        from app.services.coach import CoachService
        from app.services.enrich import EnrichService
        from app.services.playlists import PlaylistService
        from app.services.recap import RecapService
        from app.services.rescue import RescueService
        self.enrich = EnrichService(self.ask, self.sf)
        self.coach = CoachService(self.ask, self.sf)
        self.rescue = RescueService(self.sf, self.catalog)
        self.playlists = PlaylistService(self.ask, self.taste, self.sf)
        self.recap = RecapService(self.ask, self.sf, lambda: self.config.settings.data_path)
        from app.services.achievements import AchievementService
        self.achievements = AchievementService(self.ask, self.sf)
        from app.services.news import NewsService
        self.news = NewsService(self.ask, self.sf, self.hub)
        # Hobby features: each app/services/<name>.py has attach(container) -> service (optional start/stop)
        from app.features import attach_all
        self.features = attach_all(self)
        from app.services.power_plug import PowerPlug
        self.power_plug = PowerPlug(lambda: self.config.settings)
        from app.services.backup import BackupService
        self.backup = BackupService(lambda: self.config.settings)
        from app.services.scheduler import build as build_scheduler
        self.scheduler = build_scheduler(self)   # 🔄 every dynamic source on its own timetable
        from app.services.issues import IssueService
        self.issues = IssueService(self.ask, self.sf, lambda: self.config.settings.data_path)
        self.vision = VisionController(lambda: self.config.settings, self.device, self.hub)
        self.screenshots = ScreenshotService(lambda: self.config.settings.data_path, self.device)
        self.live = LiveService(lambda: self.config.settings, self.device, self.hub)
        from app.services.boxart import BoxArtService
        self.boxart = BoxArtService(lambda: self.config.settings.data_path)
        self.launcher.after_launch.append(self._auto_cover)
        self.launcher.after_launch.append(self._taste_play)
        self.catalog.cover_hook = self.ensure_cover
        self.cover_state: dict[str, Any] = {"running": False, "total": 0, "done": 0, "found": 0}
        self._cover_task: asyncio.Task | None = None
        self.scan_state: dict[str, Any] = {"running": False, "results": [], "error": None}
        self._scan_task: asyncio.Task | None = None

    @property
    def settings(self) -> Settings:
        return self.config.settings

    async def start(self) -> None:
        self.tidy_library_titles()
        await self.device.connect()
        self.device.start()
        await self.device.configure_bridge()
        self.scheduler.start()                  # news, events, magazines, hardware, firmware… (follows NEWS_MONITOR)

    def tidy_library_titles(self) -> int:
        """Readable names for titles imported as e.g. "maniac mansion" or "Last Ninja_ The"."""
        from app.library.titles import looks_untidy, tidy_title, title_key
        from app.models.db import Game
        changed = 0
        with self.sf() as s:
            for g in s.query(Game).all():
                if looks_untidy(g.title):
                    new = tidy_title(g.title)
                    if new and new != g.title:
                        log.info("library title: %r -> %r", g.title, new)
                        g.title, g.normalized_title = new, title_key(new)
                        changed += 1
            s.commit()
        return changed

    async def stop(self) -> None:
        await self.scheduler.stop()
        await self.news.stop()
        from app.features import run_hook
        await run_hook(self.features, "stop")
        await self.vision.stop("application shutdown")
        await self.live.stop()
        await self.device.shutdown()

    async def apply_settings(self, changes: dict[str, Any]) -> Settings:
        before = self.settings.model_dump()
        s = self.config.update(changes)
        from app.logging_setup import register_secret
        register_secret(s.LIVE_STREAM_KEY)
        register_secret(s.BRAVE_API_KEY)
        register_secret(s.EBAY_CLIENT_SECRET)
        after = s.model_dump()
        changed = {k for k in after if before.get(k) != after.get(k)}
        if "NEWS_MONITOR" in changed:
            self.scheduler.start()          # turned on: start checking (turned off: the loop idles)
        if changed & AI_KEYS:
            self.provider = make_provider(s)
            self.engine.provider = self.provider
        if changed & DEVICE_KEYS:
            await self.device.rebuild()
        if changed & BRIDGE_KEYS:
            await self.device.configure_bridge()
        return s

    def ai_status(self) -> dict[str, Any]:
        return self.provider.describe()

    # ------------------------------------------------------------------ scan
    def start_scan(self, paths: list[str]) -> dict[str, Any]:
        if self._scan_task and not self._scan_task.done():
            raise RuntimeError("a scan is already running")
        self.scan_state = {"running": True, "results": [], "error": None, "paths": paths}
        self._scan_task = asyncio.create_task(self._scan(paths), name="library-scan")
        self.hub.publish("scan", self.scan_state)
        return self.scan_state

    async def _scan(self, paths: list[str]) -> None:
        def run() -> list[ScanResult]:
            out = []
            for p in paths:
                with self.sf() as s:
                    out.append(scan_root(s, p))
            return out
        try:
            results = await asyncio.to_thread(run)
            self.scan_state["results"] = [r.__dict__ for r in results]
            if any(r.games_created for r in results) and not (self._cover_task and not self._cover_task.done()):
                self.start_cover_refresh()
        except Exception as exc:  # noqa: BLE001
            log.exception("scan failed")
            self.scan_state["error"] = str(exc)
        finally:
            self.scan_state["running"] = False
            self.hub.publish("scan", self.scan_state)

    # ------------------------------------------------------------ cover art
    # Cover priority: a cover the user chose > box art > title screen (libretro thumbnails) >
    # CSDB screenshot that passes the quality check > best in-game frame captured while playing.
    STRONG_SOURCES = ("user", "boxart", "title")

    def _set_cover(self, game_id: int, url: str | None, source: str | None) -> None:
        from app.models.db import Game
        with self.sf() as db:
            g = db.get(Game, game_id)
            if g is None:
                return
            g.cover_url = url
            g.extra = {**(g.extra or {}), "coverSource": source}
            db.commit()
        self.hub.publish("cover", {"gameId": game_id, "coverUrl": url, "source": source})

    async def ensure_cover(self, game_id: int, force: bool = False) -> dict[str, Any]:
        """Find the best available cover for one title. Returns {"gameId", "source", "coverUrl"}."""
        from PIL import Image

        from app.models.db import Game
        from app.services.screenshots import CSDB_CATEGORIES, image_quality
        with self.sf() as db:
            g = db.get(Game, game_id)
            if g is None:
                return {"gameId": game_id, "source": None, "coverUrl": None}
            title, alts = g.title, list(g.alternate_names or [])
            extra = dict(g.extra or {})
            current, source = g.cover_url, extra.get("coverSource")
        if current and source in self.STRONG_SOURCES and not force:
            return {"gameId": game_id, "source": source, "coverUrl": current}
        online = self.settings.COVER_ART_ONLINE
        # 1-2. Box art / title screen.
        try:
            art = await self.boxart.art_for(title, alts) if online else None
        except Exception as exc:  # noqa: BLE001 - network art is optional
            log.info("box art lookup failed for %s: %s", title, exc)
            art = None
        if art:
            kind, path = art
            url = f"/api/art/file/{path.name}"
            self._set_cover(game_id, url, kind)
            return {"gameId": game_id, "source": kind, "coverUrl": url}
        # 3. CSDB screenshot, only if it is a real picture (not a directory listing).
        a64 = extra.get("assembly64") or {}
        if online and a64 and int(a64.get("category", -1)) in CSDB_CATEGORIES:
            release = str(a64.get("id"))
            path = await self.screenshots.csdb_art(release)
            if path is not None:
                q = await asyncio.to_thread(lambda: image_quality(Image.open(path)))
                current_q = self.screenshots.cover_quality(current) or 0.0
                if q["ok"] and q["score"] > current_q:
                    url = f"/api/art/csdb/{release}"
                    self._set_cover(game_id, url, "csdb")
                    return {"gameId": game_id, "source": "csdb", "coverUrl": url}
        # A weak automatic cover (loading/BASIC screen) is worse than the generated tile.
        if current and source in (None, "auto", "csdb"):
            q = self.screenshots.cover_quality(current)
            if q is not None and q <= 0:
                self._set_cover(game_id, None, None)
                return {"gameId": game_id, "source": None, "coverUrl": None}
        return {"gameId": game_id, "source": source, "coverUrl": current}

    def start_cover_refresh(self, game_ids: list[int] | None = None, force: bool = False) -> dict[str, Any]:
        from sqlalchemy import select

        from app.models.db import Game
        if self._cover_task and not self._cover_task.done():
            raise RuntimeError("cover art search is already running")
        with self.sf() as db:
            ids = game_ids or list(db.scalars(select(Game.id)))
        self.cover_state = {"running": True, "total": len(ids), "done": 0, "found": 0}

        async def run() -> None:
            try:
                for gid in ids:
                    r = await self.ensure_cover(gid, force=force)
                    self.cover_state["done"] += 1
                    if r.get("source") in ("boxart", "title", "csdb"):
                        self.cover_state["found"] += 1
                    self.hub.publish("covers", dict(self.cover_state))
            finally:
                self.cover_state["running"] = False
                self.hub.publish("covers", dict(self.cover_state))

        self._cover_task = asyncio.create_task(run(), name="cover-refresh")
        return dict(self.cover_state)

    async def _taste_play(self, job) -> None:  # noqa: ANN001
        """A game launched on the real C64 counts as "played" for recommendations."""
        self.taste.record("play", game_id=job.game_id, title=job.title)

    async def _auto_cover(self, job) -> None:  # noqa: ANN001
        """After a launch: make sure the title has art; if no box art exists, sample the screen for
        a while and keep the best in-game frame (loading/BASIC/blank screens are skipped)."""
        from app.models.db import Game

        s = self.settings
        result = await self.ensure_cover(job.game_id)
        if not s.AUTO_COVER_ART or result.get("source") in self.STRONG_SOURCES:
            return
        if self.device.simulator is None and not self.device.caps.usable("videoStream"):
            return
        with self.sf() as db:
            game = db.get(Game, job.game_id)
            if game is None:
                return
            title, current = game.title, game.cover_url
        await asyncio.sleep(s.AUTO_COVER_DELAY_SECONDS)
        try:
            best = await self.screenshots.best_frame(
                window=s.AUTO_COVER_WINDOW_SECONDS,
                still_wanted=lambda: self.launcher.session.game_id == job.game_id)
        except Exception as exc:  # noqa: BLE001 - cover art is best effort
            log.info("automatic cover art skipped: %s", exc)
            return
        if not best:
            return
        png, q = best
        if q["score"] <= (self.screenshots.cover_quality(current) or 0.0):
            return
        shot = self.screenshots.save(png, title, job.game_id, auto=True, score=q["score"])
        with self.sf() as db:  # re-check: the user may have chosen a cover meanwhile
            game = db.get(Game, job.game_id)
            if game is None or (game.extra or {}).get("coverSource") in self.STRONG_SOURCES:
                return
        self._set_cover(job.game_id, shot["url"], "auto")
