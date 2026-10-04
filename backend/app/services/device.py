"""DeviceService: owns the connection to one C64 Ultimate and everything built on it."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.config import ConfigStore, Settings
from app.logging_setup import register_secret
from app.ultimate.capabilities import CapabilityMatrix, CapabilityProber
from app.ultimate.client import DeviceInfo, DriveInfo, UltimateClient, UltimateError
from app.ultimate.drives import DriveService, drive_to_dict
from app.ultimate.input import InputController
from app.ultimate.joybridge import JoyBridge
from app.ultimate.menu import MenuController
from app.ultimate.runners import RunnerService
from app.ultimate.simulator import SimulatedUltimate
from app.ultimate.streams import StreamService, detect_local_ip
from app.ultimate.transport import HttpTransport, SimulatedTransport

from .audit import AuditService
from .events import EventHub

log = logging.getLogger("c64.device")


class DeviceService:
    def __init__(self, config: ConfigStore, hub: EventHub, audit: AuditService):
        self.config = config
        self.hub = hub
        self.audit = audit
        self.caps = CapabilityMatrix()
        self.info: DeviceInfo | None = None
        self.api_version = ""
        self.connected = False
        self.last_error: str | None = None
        self.last_contact = 0.0
        self.drive_list: list[DriveInfo] = []
        self.simulator: SimulatedUltimate | None = None
        self.client: UltimateClient | None = None
        self.streams = StreamService(config.settings.STREAM_VIDEO_PORT, config.settings.STREAM_AUDIO_PORT)
        self._poll_task: asyncio.Task | None = None
        self._viewers: dict[str, int] = {"video": 0, "audio": 0}
        self._stoppers: dict[str, asyncio.Task] = {}
        self._connect_lock = asyncio.Lock()
        self.session_provider = None  # set by app wiring: returns current session dict
        self.bridge = JoyBridge()  # survives rebuilds: it does not depend on the Ultimate connection
        self.bridge.on_change = self._publish_status
        self._build()

    # ---------------------------------------------------------------- wiring
    @property
    def settings(self) -> Settings:
        return self.config.settings

    def _build(self) -> None:
        s = self.settings
        register_secret(s.C64_ULTIMATE_PASSWORD)
        register_secret(s.AI_API_KEY)
        if s.SIMULATE_C64:
            if self.simulator is None or self.simulator.profile != s.SIMULATE_PROFILE:
                self.simulator = SimulatedUltimate(profile=s.SIMULATE_PROFILE)
            transport = SimulatedTransport(self.simulator)
        else:
            self.simulator = None
            transport = HttpTransport(s.base_url, s.C64_ULTIMATE_PASSWORD, s.C64_REQUEST_TIMEOUT, s.C64_UPLOAD_MODE)
        self.client = UltimateClient(transport)
        self.client.listeners.append(self.audit.on_client_call)
        self.client.listeners.append(self._on_call)
        self.inputs = InputController(self.client, self.caps, type_delay_ms=0 if s.SIMULATE_C64 else s.C64_TYPE_DELAY_MS,
                                     bridge=self.bridge)
        self.menu = MenuController(self.client, self.caps, self.inputs,
                                   poll_interval=0.02 if s.SIMULATE_C64 else 0.12)
        self.drives = DriveService(self.client, self.caps)
        self.runners = RunnerService(self.client, self.caps)

    def _on_call(self, rec) -> None:  # noqa: ANN001
        if rec.status:
            self.last_contact = time.time()

    async def rebuild(self) -> None:
        old = self.client
        try:
            if old is not None and self.connected:
                await self.release_all_inputs(reason="reconfigure")
        finally:
            self.caps.__dict__.update(CapabilityMatrix().__dict__)
            self.connected = False
            self.info = None
            self._build()
            if old is not None:
                await old.aclose()
        await self.connect()

    # ------------------------------------------------------------ connection
    async def connect(self) -> dict[str, Any]:
        async with self._connect_lock:
            if not self.settings.device_configured:
                self.connected = False
                self.last_error = "no device configured"
                self._publish_status()
                return self.status()
            assert self.client is not None
            try:
                version = await self.client.version()
                self.api_version = version.api_version
                try:
                    self.info = await self.client.info()
                except UltimateError as exc:
                    # /v1/info appeared after /v1/version; keep going without it.
                    self.info = DeviceInfo(raw={"error": str(exc)})
                host = "" if self.settings.SIMULATE_C64 else self.settings.C64_ULTIMATE_HOST
                matrix = await CapabilityProber(self.client, host=host).probe()
                self.caps.__dict__.update(matrix.__dict__)
                self.connected = matrix.reachable and matrix.auth_ok
                self.last_error = None if self.connected else "authentication failed (check password)"
                if self.caps.usable("driveStatus"):
                    await self.refresh_drives()
            except UltimateError as exc:
                self.connected = False
                self.last_error = str(exc)
            self._publish_status()
            return self.status()

    async def test_connection(self, host: str, port: int, password: str, protocol: str = "http") -> dict[str, Any]:
        """Setup-wizard test against arbitrary settings without changing the live connection."""
        tmp = Settings.model_validate({**self.settings.model_dump(), "C64_ULTIMATE_HOST": host,
                                       "C64_ULTIMATE_PORT": port, "C64_ULTIMATE_PASSWORD": password,
                                       "C64_ULTIMATE_PROTOCOL": protocol, "SIMULATE_C64": False})
        register_secret(password)
        client = UltimateClient(HttpTransport(tmp.base_url, password, 5.0))
        try:
            version = await client.version()
            info = await client.info()
            matrix = await CapabilityProber(client, host=host).probe()
            return {"ok": matrix.auth_ok, "apiVersion": version.api_version, "info": _info_dict(info),
                    "capabilities": matrix.to_dict()}
        except UltimateError as exc:
            return {"ok": False, "error": str(exc), "status": exc.status,
                    "authRequired": exc.status == 403}
        finally:
            await client.aclose()

    async def refresh_drives(self) -> list[DriveInfo]:
        assert self.client is not None
        self.drive_list = await self.client.drives()
        return self.drive_list

    # --------------------------------------------------------------- polling
    async def configure_bridge(self) -> None:
        await self.bridge.configure(self.settings.JOYBRIDGE_HOST, self.settings.JOYBRIDGE_PORT)
        self._publish_status()

    def start(self) -> None:
        if self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll_loop(), name="device-poll")

    async def _poll_loop(self) -> None:
        failures = 0
        while True:
            try:
                await asyncio.sleep(5 if self.connected else min(30, 5 + failures * 5))
                if not self.settings.device_configured:
                    continue
                await self.inputs.expire_stale_holds()
                if not self.connected:
                    await self.connect()
                    failures = 0 if self.connected else failures + 1
                    continue
                before = [drive_to_dict(d) for d in self.drive_list]
                if self.caps.usable("driveStatus"):
                    await self.refresh_drives()
                else:
                    await self.client.version()  # type: ignore[union-attr]
                failures = 0
                if before != [drive_to_dict(d) for d in self.drive_list]:
                    self._publish_status()
            except asyncio.CancelledError:
                raise
            except UltimateError as exc:
                failures += 1
                if failures >= 2 and self.connected:
                    self.connected = False
                    self.last_error = f"lost contact: {exc}"
                    self._publish_status()
            except Exception:  # pragma: no cover
                log.exception("poll loop error")

    async def shutdown(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):
                pass
        if self.connected:
            await self.release_all_inputs(reason="shutdown")
            for kind in list(self.streams.active):
                try:
                    await self.client.stream_stop(kind)  # type: ignore[union-attr, arg-type]
                except UltimateError:
                    pass
        self.streams.close()
        await self.bridge.close()
        if self.client:
            await self.client.aclose()

    async def release_all_inputs(self, reason: str = "") -> bool:
        try:
            sent = await self.inputs.release_all()
            if sent:
                log.info("release_all sent (%s)", reason)
            return sent
        except Exception as exc:  # noqa: BLE001
            log.warning("release_all failed (%s): %s", reason, exc)
            return False

    # ---------------------------------------------------------------- streams
    def stream_target_host(self) -> str:
        if self.settings.STREAM_TARGET_HOST:
            return self.settings.STREAM_TARGET_HOST
        return detect_local_ip(self.settings.C64_ULTIMATE_HOST or "127.0.0.1")

    async def start_stream(self, kind: str) -> dict[str, Any]:
        if kind not in ("video", "audio"):
            raise ValueError("stream must be video or audio")
        cap = "videoStream" if kind == "video" else "audioStream"
        if self.simulator is not None:
            self.streams.active[kind] = "simulated"
            return self.streams.status()
        if not self.caps.usable(cap):
            raise UltimateError(f"streams.{kind}.start", 0, detail=f"{cap} not supported by this device")
        if not await self.streams.bind(kind):
            raise UltimateError(f"streams.{kind}.start", 0, detail=self.streams.errors.get(kind, "bind failed"))
        port = self.streams.video_port if kind == "video" else self.streams.audio_port
        target = f"{self.stream_target_host()}:{port}"
        await self.client.stream_start(kind, target)  # type: ignore[union-attr, arg-type]
        self.streams.active[kind] = target
        self.caps.record_use(cap, True)
        return self.streams.status()

    async def stop_stream(self, kind: str) -> dict[str, Any]:
        if self.simulator is None and kind in self.streams.active:
            await self.client.stream_stop(kind)  # type: ignore[union-attr, arg-type]
        self.streams.active.pop(kind, None)
        self.streams.unbind(kind)
        return self.streams.status()

    # Viewer counting: the Display page, an OBS "stream view", a live broadcast and cover
    # capture can all watch at once. The device stream starts with the first viewer and stops
    # a few seconds after the last one leaves (so page navigation doesn't flap it on and off).
    STREAM_LINGER_SECONDS = 5.0

    async def join_stream(self, kind: str) -> None:
        viewers, stoppers = self._viewers, self._stoppers
        viewers[kind] += 1
        pending = stoppers.pop(kind, None)
        if pending:
            pending.cancel()
        if kind not in self.streams.active:
            try:
                await self.start_stream(kind)
            except Exception:
                viewers[kind] -= 1
                raise

    async def leave_stream(self, kind: str) -> None:
        viewers, stoppers = self._viewers, self._stoppers
        viewers[kind] = max(0, viewers[kind] - 1)
        if viewers[kind] == 0 and kind not in stoppers:
            async def later() -> None:
                try:
                    await asyncio.sleep(self.STREAM_LINGER_SECONDS)
                    if self._viewers[kind] == 0:
                        await self.stop_stream(kind)
                except asyncio.CancelledError:
                    pass
                except UltimateError as exc:
                    log.warning("stopping %s stream failed: %s", kind, exc)
                finally:
                    stoppers.pop(kind, None)
            stoppers[kind] = asyncio.create_task(later())

    def stream_viewers(self) -> dict[str, int]:
        return dict(self._viewers)

    # ----------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        s = self.settings
        return {
            "configured": s.device_configured,
            "simulated": s.SIMULATE_C64,
            "simulatorProfile": s.SIMULATE_PROFILE if s.SIMULATE_C64 else None,
            "connected": self.connected,
            "host": "simulator" if s.SIMULATE_C64 else s.C64_ULTIMATE_HOST,
            "baseUrl": self.client.transport.base_url if self.client else None,  # type: ignore[attr-defined]
            "lastError": self.last_error,
            "lastContact": self.last_contact or None,
            "apiVersion": self.api_version,
            "info": _info_dict(self.info) if self.info else None,
            "inputMode": self.caps.input_mode,
            "input": self.inputs.status(),
            "drives": [drive_to_dict(d) for d in self.drive_list],
            "capabilities": self.caps.to_dict(),
            "streams": self.streams.status(),
            "session": self.session_provider() if self.session_provider else None,
        }

    def _publish_status(self) -> None:
        self.hub.publish("status", self.status())

    def publish_status(self) -> None:
        self._publish_status()


def _info_dict(info: DeviceInfo) -> dict[str, Any]:
    return {"product": info.product, "firmwareVersion": info.firmware_version, "fpgaVersion": info.fpga_version,
            "coreVersion": info.core_version, "hostname": info.hostname, "uniqueId": info.unique_id}
