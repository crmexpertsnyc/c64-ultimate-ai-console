"""Universal game launcher and multi-disk session state.

launch_game() picks a method from the media type (or the game's stored preference):

  prg  → run_prg (upload or device path)
  crt  → run_crt
  sid  → sidplay, mod → modplay
  t64  → extract first file locally → run_prg upload
  disk → mount drive A (read-only upload for local files) → reset → wait for READY.
         → type load command (default LOAD"*",8,1) → wait for READY. → RUN
         Optionally fall back to "dma_first_prg" (extract first PRG from the image).

Each launch runs as a job with step-by-step progress published over the event hub.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import sessionmaker

from app.library.media import DiskImage, t64_extract_prg
from app.library.repository import LibraryRepository, game_to_dict
from app.models.db import Game, Media
from app.ultimate.input import InputUnsupported

from .audit import AuditService
from .device import DeviceService
from .events import EventHub

log = logging.getLogger("c64.launcher")

DISK_FORMATS = {"d64", "d71", "d81", "g64", "g71"}
DEFAULT_LOAD = 'LOAD"*",8,1'


class LaunchError(Exception):
    pass


@dataclass
class Step:
    name: str
    status: str = "pending"  # pending | running | ok | skipped | failed
    detail: str = ""
    at: float = 0.0


@dataclass
class LaunchJob:
    id: str
    game_id: int
    title: str
    method: str
    status: str = "running"  # running | done | failed
    steps: list[Step] = field(default_factory=list)
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gameId"] = d.pop("game_id")
        d["startedAt"] = d.pop("started_at")
        d["finishedAt"] = d.pop("finished_at")
        return d


@dataclass
class SessionState:
    game_id: int | None = None
    title: str | None = None
    format: str | None = None
    disks: list[dict[str, Any]] = field(default_factory=list)  # {mediaId, diskNumber, label, path}
    current_disk: int | None = None  # disk number
    drive: str = "a"
    joystick_port: int | None = None
    started_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"gameId": self.game_id, "title": self.title, "format": self.format, "disks": self.disks,
                "currentDisk": self.current_disk, "drive": self.drive, "joystickPort": self.joystick_port,
                "startedAt": self.started_at, "diskCount": len(self.disks)}


def resolve_method(game: Game, media: Media) -> str:
    if game.preferred_launch and game.preferred_launch != "auto":
        return game.preferred_launch
    fmt = media.format
    if fmt == "prg":
        return "run_prg"
    if fmt == "crt":
        return "run_crt"
    if fmt == "sid":
        return "sid"
    if fmt == "mod":
        return "mod"
    if fmt == "t64":
        return "t64_extract"
    if fmt in DISK_FORMATS:
        return "mount_and_load"
    raise LaunchError(f"don't know how to launch format {fmt}")


class Launcher:
    def __init__(self, device: DeviceService, session_factory: sessionmaker, hub: EventHub, audit: AuditService):
        self.device = device
        self.sf = session_factory
        self.hub = hub
        self.audit = audit
        self.session = SessionState()
        self.jobs: dict[str, LaunchJob] = {}
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        # Called (as background tasks) after a successful launch, e.g. automatic cover art.
        self.after_launch: list = []

    # ----------------------------------------------------------------- public
    def current_job(self) -> LaunchJob | None:
        running = [j for j in self.jobs.values() if j.status == "running"]
        return running[-1] if running else None

    async def launch(self, game_id: int, *, disk: int | None = None, method: str | None = None,
                     source: str = "api", user_command: str | None = None, wait: bool = False) -> LaunchJob:
        with self.sf() as s:
            repo = LibraryRepository(s)
            game = repo.get(game_id)
            if game is None:
                raise LaunchError(f"game {game_id} not found")
            media_list = [m for m in game.media if not m.missing]
            if not media_list:
                raise LaunchError(f"{game.title} has no available media files")
            media = next((m for m in media_list if disk and m.disk_number == disk), None) or \
                sorted(media_list, key=lambda m: (m.disk_number, m.path))[0]
            chosen = method or resolve_method(game, media)
            snapshot = game_to_dict(game)
            media_snapshot = {"id": media.id, "path": media.path, "storage": media.storage, "format": media.format,
                              "diskNumber": media.disk_number}
        if self.current_job():
            raise LaunchError("another launch is in progress")
        job = LaunchJob(id=uuid.uuid4().hex[:10], game_id=game_id, title=snapshot["title"], method=chosen)
        self.jobs[job.id] = job
        if len(self.jobs) > 50:
            for old in list(self.jobs)[:-50]:
                self.jobs.pop(old, None)
        coro = self._run(job, snapshot, media_snapshot, source, user_command)
        if wait:
            await coro
        else:
            self._task = asyncio.create_task(coro, name=f"launch-{job.id}")
        return job

    async def mount_disk(self, number: int, source: str = "api", user_command: str | None = None) -> dict[str, Any]:
        if not self.session.game_id or not self.session.disks:
            raise LaunchError("no multi-disk game is active; start a game first")
        disk = next((d for d in self.session.disks if d["diskNumber"] == number), None)
        if disk is None:
            available = ", ".join(str(d["diskNumber"]) for d in self.session.disks)
            raise LaunchError(f"{self.session.title} has no disk {number} (available: {available})")
        async with self.audit.action(source, "session.mount_disk", user_command,
                                     {"disk": number, "game": self.session.title}) as rec:
            await self._mount(disk["path"], disk["storage"], self.session.drive)
            self.session.current_disk = number
            rec.set_response({"mounted": disk["path"], "disk": number})
        self._publish()
        return self.session.to_dict()

    async def next_disk(self, source: str = "api", user_command: str | None = None) -> dict[str, Any]:
        return await self.mount_disk(self._relative_disk(+1), source, user_command)

    async def previous_disk(self, source: str = "api", user_command: str | None = None) -> dict[str, Any]:
        return await self.mount_disk(self._relative_disk(-1), source, user_command)

    def _relative_disk(self, delta: int) -> int:
        numbers = sorted(d["diskNumber"] for d in self.session.disks)
        if not numbers:
            raise LaunchError("no multi-disk game is active")
        cur = self.session.current_disk or numbers[0]
        idx = numbers.index(cur) if cur in numbers else 0
        new = idx + delta
        if not 0 <= new < len(numbers):
            raise LaunchError(f"already at {'last' if delta > 0 else 'first'} disk ({cur} of {len(numbers)})")
        return numbers[new]

    # -------------------------------------------------------------- internals
    def _publish(self, job: LaunchJob | None = None) -> None:
        if job:
            self.hub.publish("launch", job.to_dict())
        self.hub.publish("session", self.session.to_dict())

    async def _step(self, job: LaunchJob, name: str, coro=None, detail: str = ""):  # noqa: ANN001
        step = Step(name=name, status="running", detail=detail, at=time.time())
        job.steps.append(step)
        self._publish(job)
        try:
            result = await coro if coro is not None else None
        except Exception as exc:
            step.status, step.detail = "failed", str(exc)
            self._publish(job)
            raise
        step.status = "ok"
        if isinstance(result, str) and result:
            step.detail = result
        self._publish(job)
        return result

    def _skip(self, job: LaunchJob, name: str, detail: str) -> None:
        job.steps.append(Step(name=name, status="skipped", detail=detail, at=time.time()))
        self._publish(job)

    async def _mount(self, path: str, storage: str, drive: str = "a") -> None:
        dev = self.device
        drives = {d.id: d for d in dev.drive_list}
        if drive in drives and not drives[drive].enabled and dev.caps.usable("driveControl"):
            await dev.drives.power(drive, True)
        if storage == "device":
            await dev.drives.mount_device_path(drive, path)
        else:
            await dev.drives.mount_local_file(drive, Path(path))
        if dev.caps.usable("driveStatus"):
            await dev.refresh_drives()
            dev.publish_status()

    async def _run(self, job: LaunchJob, game: dict[str, Any], media: dict[str, Any], source: str,
                   user_command: str | None) -> None:
        async with self._lock:
            try:
                async with self.audit.action(source, f"launch.{job.method}", user_command,
                                             {"gameId": job.game_id, "title": job.title, "method": job.method}) as rec:
                    await self._execute(job, game, media)
                    rec.set_response({"job": job.id, "steps": [s.name for s in job.steps]})
                job.status = "done"
                for hook in self.after_launch:
                    asyncio.create_task(hook(job))
                with self.sf() as s:
                    g = s.get(Game, job.game_id)
                    if g:
                        LibraryRepository(s).record_play(g)
            except Exception as exc:  # noqa: BLE001
                job.status, job.error = "failed", str(exc)
                log.warning("launch %s failed: %s", job.title, exc)
                await self.device.release_all_inputs(reason="launch failure")
            finally:
                job.finished_at = time.time()
                self._publish(job)

    async def _execute(self, job: LaunchJob, game: dict[str, Any], media: dict[str, Any]) -> None:
        dev = self.device
        method = job.method
        path, storage, fmt = media["path"], media["storage"], media["format"]
        local = storage == "local"
        if local and not Path(path).is_file():
            raise LaunchError(f"file not found: {path}")

        disks = [d for d in game["media"] if d["format"] in DISK_FORMATS and not d["missing"]]
        self.session = SessionState(
            game_id=game["id"], title=game["title"], format=fmt,
            disks=[{"mediaId": d["id"], "diskNumber": d["diskNumber"], "label": d["label"], "path": d["path"],
                    "storage": d["storage"]} for d in disks],
            current_disk=media["diskNumber"] if fmt in DISK_FORMATS else None,
            joystick_port=game.get("joystickPort"), started_at=time.time(),
        )
        if game.get("joystickPort") in (1, 2):
            dev.inputs.set_joystick_port(game["joystickPort"])
        self._publish(job)

        runner_kinds = {"run_prg": "run_prg", "load_prg": "load_prg", "run_crt": "run_crt", "sid": "sid", "mod": "mod"}
        if method in runner_kinds:
            kind = runner_kinds[method]
            kw = {"device_path": path} if not local else {"local_path": Path(path)}
            await self._step(job, f"{kind} {Path(path).name}", dev.runners.run(kind, **kw))
        elif method == "t64_extract":
            if not local:
                raise LaunchError("T64 extraction needs a local file")
            name, prg = t64_extract_prg(Path(path).read_bytes())
            await self._step(job, f"extract '{name}' from T64", None, f"{len(prg)} bytes")
            await self._step(job, "run_prg (upload)", dev.client.run_prg_upload(prg, f"{name or 'tape'}.prg"))
        elif method == "dma_first_prg":
            if not local or fmt not in ("d64", "d71", "d81"):
                raise LaunchError("dma_first_prg needs a local D64/D71/D81 image")
            found = DiskImage(Path(path).read_bytes(), fmt).boot_prg()
            if not found:
                raise LaunchError("no PRG file on the disk image")
            entry, prg = found
            # Mount as well so games that load further files from disk keep working.
            await self._step(job, f"mount {Path(path).name} on drive A", self._mount(path, storage))
            if not dev.caps.usable("runPrg"):
                raise InputUnsupported("runPrg is not supported by this device")
            await self._step(job, f"run_prg '{entry.name}' via DMA", dev.client.run_prg_upload(prg, f"{entry.name}.prg"))
        elif method in ("mount_and_load", "mount_only"):
            await self._step(job, f"mount {Path(path).name} on drive A", self._mount(path, storage))
            if method == "mount_only":
                return
            await self._load_from_disk(job, game)
        else:
            raise LaunchError(f"unknown launch method {method}")

        if game.get("needsFire"):
            await asyncio.sleep(float(game.get("startupDelay") or 3))
            if dev.caps.supported("directJoystick"):
                port = game.get("joystickPort") or dev.inputs.joystick_port
                await self._step(job, f"press fire (port {port})", dev.inputs.tap_joystick(["fire"], port))
            else:
                self._skip(job, "press fire", "joystick injection not supported on this firmware")

    async def _load_from_disk(self, job: LaunchJob, game: dict[str, Any]) -> None:
        dev = self.device
        mode = dev.inputs.mode
        if mode == "none":
            raise InputUnsupported("disk mounted, but this firmware offers no way to type LOAD. Use a PRG, or "
                                   "set the game's launch method to dma_first_prg.")
        if game.get("resetBeforeLoad", True):
            await self._step(job, "reset C64", dev.client.reset())
            dev.caps.record_use("machineReset", True)
        delay = float(game.get("startupDelay") or 3.0)
        if dev.simulator is not None:
            delay = min(delay, 0.05)
        await self._step(job, "wait for BASIC READY.", self._wait_ready(delay, timeout=delay + 10))
        cmd = (game.get("loadCommand") or self._boot_command(game) or DEFAULT_LOAD).strip()
        run_after = bool(game.get("runAfterLoad", True)) and "RUN" not in cmd.upper().split(":")[-1:]
        if mode == "legacy":
            await self._step(job, f"type {cmd} (keyboard buffer)", dev.inputs.legacy_type_text(cmd + "\r"))
            if run_after:
                # Queued in the KERNAL buffer; BASIC executes it when the load returns to READY.
                await self._step(job, "queue RUN", dev.inputs.legacy_type_text("RUN\r", wait_drain=False))
                return
        else:
            await self._step(job, f"type {cmd}", dev.inputs.type_text(cmd + "\r"))
        if not run_after:
            return
        timeout = float(game.get("loadTimeout") or 90)
        await self._step(job, "wait for load to finish", self._wait_load_done(timeout))
        await self._step(job, "type RUN", dev.inputs.type_text("RUN\r"))

    def _boot_command(self, game: dict[str, Any]) -> str | None:
        """LOAD"<boot program>",8,1 when the disk's boot program isn't its first directory entry
        (directory art, notes or data parts first); None → the default LOAD"*",8,1."""
        from app.library.media import loadable_name
        disk_no = self.session.current_disk or 1
        media = next((m for m in game.get("media", []) if m.get("diskNumber") == disk_no), None)
        info = (media or {}).get("info") or {}
        boot = info.get("boot")
        if boot and not info.get("bootIsFirst") and loadable_name(boot):
            return f'LOAD"{boot}",8,1'
        return None

    async def _wait_ready(self, delay: float, timeout: float) -> str:
        await asyncio.sleep(delay)
        dev = self.device
        if not dev.caps.usable("memoryRead"):
            return f"waited {delay:.0f}s (screen not readable on this firmware)"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            lines = await dev.inputs.read_text_screen()
            if lines is None:
                return f"waited {delay:.0f}s (screen not readable)"
            if any(line.strip() == "READY." for line in lines):
                return "READY. detected"
            await asyncio.sleep(0.25)
        raise LaunchError("C64 did not reach READY. after reset")

    async def _wait_load_done(self, timeout: float) -> str:
        dev = self.device
        if not dev.caps.usable("memoryRead"):
            await asyncio.sleep(min(timeout, 20))
            return "waited 20s (screen not readable; timing is a guess)"
        deadline = time.monotonic() + timeout
        poll = 0.05 if dev.simulator is not None else 0.5
        while time.monotonic() < deadline:
            await asyncio.sleep(poll)
            lines = await dev.inputs.read_text_screen() or []
            load_rows = [i for i, line in enumerate(lines) if 'LOAD"' in line]
            if not load_rows:
                continue
            after = lines[load_rows[-1] + 1:]
            if any("ERROR" in line for line in after):
                raise LaunchError("C64 reported: " + next(line.strip() for line in after if "ERROR" in line))
            if any(line.strip() == "READY." for line in after):
                return "load complete"
        raise LaunchError(f"load did not finish within {timeout:.0f}s")
