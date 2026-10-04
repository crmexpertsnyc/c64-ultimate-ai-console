"""Go live (RTMP to Twitch / YouTube / any RTMP server) and record to MP4.

One ffmpeg process receives:
  * video: raw RGB frames on stdin at a constant 50 fps (the C64's PAL rate). The newest frame
    from the Ultimate's stream is written each tick; if none arrived, the previous one is repeated,
    which keeps the output constant-frame-rate as streaming services require.
  * audio: the Ultimate's PCM stream (s16le stereo, ~47983 Hz) relayed to a local UDP port.
Output: H.264 (zerolatency) + AAC; the C64 picture is doubled with sharp pixels, then scaled to fill 720p height.

The stream key is a secret: it is never logged and never returned by the API.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import subprocess
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from app.ultimate.streams import _FLAT_PALETTE, AUDIO_RATE_PAL, PAL_HEIGHT, WIDTH, render_text_frame

log = logging.getLogger("c64.live")

FPS = 50
PRESETS = {
    "twitch": "rtmp://live.twitch.tv/app",
    "youtube": "rtmp://a.rtmp.youtube.com/live2",
}


def find_ffmpeg(configured: str = "") -> str | None:
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - optional dependency
        return None


def frame_to_rgb(frame: bytes, height: int) -> bytes:
    img = Image.frombytes("P", (WIDTH, height), frame)
    img.putpalette(_FLAT_PALETTE)
    if height != PAL_HEIGHT:
        canvas = Image.new("P", (WIDTH, PAL_HEIGHT), 0)
        canvas.putpalette(_FLAT_PALETTE)
        canvas.paste(img, (0, 0))
        img = canvas
    return img.convert("RGB").tobytes()


def _sim_rgb(lines: list[str]) -> bytes:
    import io
    img = Image.open(io.BytesIO(render_text_frame(lines, "SIMULATED", "PNG"))).convert("RGB")
    return img.resize((WIDTH, PAL_HEIGHT)).tobytes()


class LiveService:
    def __init__(self, settings_provider, device, hub):  # noqa: ANN001
        self._settings = settings_provider
        self.device = device
        self.hub = hub
        self.proc: subprocess.Popen | None = None
        self.mode: str | None = None  # "live" | "record"
        self.target_label = ""
        self.output_file: Path | None = None
        self.started_at: float | None = None
        self.error: str | None = None
        self.log_tail: deque[str] = deque(maxlen=40)
        self.stats: dict[str, str] = {}
        self._tasks: list[asyncio.Task] = []
        self._udp: socket.socket | None = None
        self._lock = asyncio.Lock()
        self._stopping = False

    @property
    def settings(self):  # noqa: ANN201
        return self._settings()

    @property
    def recordings_folder(self) -> Path:
        path = self.settings.data_path / "recordings"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict[str, Any]:
        s = self.settings
        return {
            "running": self.running, "mode": self.mode if self.running else None, "target": self.target_label,
            "startedAt": self.started_at if self.running else None,
            "seconds": round(time.time() - self.started_at) if self.running and self.started_at else 0,
            "stats": self.stats if self.running else {}, "error": self.error,
            "ffmpeg": bool(find_ffmpeg(s.FFMPEG_PATH)), "rtmpUrl": s.LIVE_RTMP_URL, "streamKeySet": bool(s.LIVE_STREAM_KEY),
            "bitrateKbps": s.LIVE_VIDEO_BITRATE_KBPS, "presets": PRESETS,
            "file": self.output_file.name if self.output_file and self.mode == "record" else None,
        }

    # ------------------------------------------------------------------ start
    async def start(self, mode: str) -> dict[str, Any]:
        async with self._lock:
            if self.running:
                raise RuntimeError(f"already {'live' if self.mode == 'live' else 'recording'}")
            s = self.settings
            ffmpeg = find_ffmpeg(s.FFMPEG_PATH)
            if not ffmpeg:
                raise RuntimeError("ffmpeg not found (install ffmpeg or the imageio-ffmpeg package)")
            if mode == "live":
                if not s.LIVE_RTMP_URL or not s.LIVE_STREAM_KEY:
                    raise RuntimeError("set the RTMP server and stream key first")
                output = ["-f", "flv", s.LIVE_RTMP_URL.rstrip("/") + "/" + s.LIVE_STREAM_KEY]
                self.target_label = s.LIVE_RTMP_URL
                self.output_file = None
            elif mode == "record":
                name = datetime.now().strftime("%Y%m%d-%H%M%S") + "-c64.mp4"
                self.output_file = self.recordings_folder / name
                output = ["-movflags", "+faststart", "-f", "mp4", str(self.output_file)]
                self.target_label = name
            else:
                raise ValueError("mode must be live or record")

            simulated = self.device.simulator is not None
            if not simulated:
                await self.device.join_stream("video")
                await self.device.join_stream("audio")

            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp.bind(("127.0.0.1", 0))
            audio_port = self._udp.getsockname()[1]
            self._udp.close()
            self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            kbps = int(s.LIVE_VIDEO_BITRATE_KBPS)
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "warning", "-stats", "-stats_period", "2",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{PAL_HEIGHT}", "-framerate", str(FPS),
                "-probesize", "32", "-analyzeduration", "0", "-thread_queue_size", "256", "-i", "pipe:0",
                # Raw PCM with known parameters needs no probing; ffmpeg would otherwise analyse up to
                # 5 s of the live UDP feed before starting (stalling the video pipe and the stream).
                "-f", "s16le", "-ar", str(AUDIO_RATE_PAL), "-ac", "2", "-probesize", "32", "-analyzeduration", "0",
                "-thread_queue_size", "512",
                "-i", f"udp://127.0.0.1:{audio_port}?fifo_size=1000000&overrun_nonfatal=1&timeout=2000000",
                "-map", "0:v", "-map", "1:a",
                "-vf", "scale=768:544:flags=neighbor,scale=-2:720:flags=bilinear,pad=1280:720:(ow-iw)/2:(oh-ih)/2:black",
                "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-pix_fmt", "yuv420p",
                "-r", str(FPS), "-g", str(FPS * 2), "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k",
                "-bufsize", f"{kbps * 2}k",
                "-af", "aresample=async=1000", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                "-shortest", *output,
            ]
            self.log_tail.clear()
            self.stats = {}
            self.error = None
            self._stopping = False
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, creationflags=creationflags)
            self.mode = mode
            self.started_at = time.time()
            log.info("%s started (%s)", "live stream" if mode == "live" else "recording", self.target_label)
            self._tasks = [
                asyncio.create_task(self._feed_video(simulated)),
                asyncio.create_task(self._feed_audio(audio_port, simulated)),
                asyncio.create_task(self._read_stderr()),
            ]
        self.hub.publish("live", self.status())
        return self.status()

    # ------------------------------------------------------------------ feeds
    async def _feed_video(self, simulated: bool) -> None:
        video = self.device.streams.video
        interval = 1 / FPS
        next_tick = time.monotonic()
        last_rgb = bytes(WIDTH * PAL_HEIGHT * 3)
        last_seen = -1
        sim_rendered = 0.0
        proc = self.proc
        try:
            while proc and proc.poll() is None and not self._stopping:
                if simulated:
                    if time.monotonic() - sim_rendered > 0.2:  # the simulated screen changes rarely
                        sim_rendered = time.monotonic()
                        last_rgb = await asyncio.to_thread(_sim_rgb, self.device.simulator.lines)
                elif video.frame is not None and video.frames != last_seen:
                    last_seen = video.frames
                    last_rgb = await asyncio.to_thread(frame_to_rgb, video.frame, video.height)
                assert proc.stdin
                await asyncio.to_thread(proc.stdin.write, last_rgb)
                next_tick += interval
                delay = next_tick - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                elif delay < -1:  # fell far behind (e.g. machine asleep): resynchronise
                    next_tick = time.monotonic()
        except (BrokenPipeError, OSError, ValueError, AssertionError):
            pass
        finally:
            # Only this task writes to stdin, so it is also the one that closes it: EOF on the
            # video input makes ffmpeg finish the file / stream cleanly.
            if proc and proc.stdin:
                try:
                    await asyncio.to_thread(proc.stdin.close)
                except OSError:
                    pass

    async def _feed_audio(self, port: int, simulated: bool) -> None:
        """Relay the C64's PCM to ffmpeg, padded with silence so audio never falls behind real
        time. (ffmpeg interleaves audio and video; starved audio would stall the video input.)"""
        sock = self._udp
        if sock is None:
            return
        target = ("127.0.0.1", port)
        samples_per_packet = 192
        silence = bytes(samples_per_packet * 4)
        q = None if simulated else self.device.streams.audio.subscribe()
        start = time.monotonic()
        sent = 0
        proc = self.proc

        def send(data: bytes) -> None:
            # Windows reports "connection reset" (ICMP port unreachable) on a later send if a
            # packet arrived before ffmpeg opened its UDP input. That is transient: keep going.
            try:
                sock.sendto(data, target)
            except OSError:
                pass

        try:
            # Keep feeding until ffmpeg has exited (not just until stop is requested): its UDP
            # reader must keep receiving to notice the end of the video input and shut down.
            while proc and proc.poll() is None:
                if q is not None:
                    while not q.empty():
                        send(q.get_nowait())
                        sent += 1
                owed = int((time.monotonic() - start) * AUDIO_RATE_PAL / samples_per_packet)
                # Allow ~40 ms of slack for network jitter before filling the gap with silence.
                while sent < owed - 10:
                    send(silence)
                    sent += 1
                await asyncio.sleep(0.01)
        finally:
            if q is not None:
                self.device.streams.audio.unsubscribe(q)

    async def _read_stderr(self) -> None:
        proc = self.proc
        if not proc or not proc.stderr:
            return
        buf = b""
        while True:
            chunk = await asyncio.to_thread(proc.stderr.read1, 4096) if hasattr(proc.stderr, "read1") else b""
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.replace(b"\r", b"\n").split(b"\n")
            for raw in lines:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                key = self.settings.LIVE_STREAM_KEY
                if key:
                    line = line.replace(key, "***")
                if line.startswith("frame="):
                    self.stats = dict(
                        (k, v) for k, v in (p.split("=", 1) for p in line.replace("= ", "=").split() if "=" in p))
                    self.hub.publish("live", self.status())
                else:
                    self.log_tail.append(line)
        code = proc.wait()
        if code not in (0, 255) and self.error is None:
            self.error = (self.log_tail[-1] if self.log_tail else f"ffmpeg exited with code {code}")
        await self._cleanup()

    # ------------------------------------------------------------------- stop
    async def stop(self) -> dict[str, Any]:
        proc = self.proc
        if proc and proc.poll() is None:
            self._stopping = True
            feeder = self._tasks[0] if self._tasks else None
            if feeder is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(feeder), timeout=5)
                except (TimeoutError, asyncio.CancelledError):
                    pass
            try:
                await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=10)
            except TimeoutError:
                proc.kill()
                await asyncio.to_thread(proc.wait)
        await self._cleanup()
        return self.status()

    async def _cleanup(self) -> None:
        if self.mode is None and self.proc is None:
            return
        for t in self._tasks:
            if t is not asyncio.current_task():
                t.cancel()
        self._tasks = []
        if self._udp:
            self._udp.close()
            self._udp = None
        mode = self.mode
        self.proc = None
        if self.device.simulator is None and mode:
            await self.device.leave_stream("video")
            await self.device.leave_stream("audio")
        self.mode = None
        log.info("%s stopped", "live stream" if mode == "live" else "recording")
        self.hub.publish("live", self.status())

    # ------------------------------------------------------------- recordings
    def recordings(self) -> list[dict[str, Any]]:
        return [{"name": p.name, "url": f"/api/recordings/{p.name}", "size": p.stat().st_size,
                 "createdAt": p.stat().st_mtime}
                for p in sorted(self.recordings_folder.glob("*.mp4"), reverse=True)
                if not (self.running and self.output_file and p == self.output_file)]

    def recording_path(self, name: str) -> Path:
        import re
        if not re.fullmatch(r"[0-9]{8}-[0-9]{6}-c64\.mp4", name):
            raise ValueError("invalid recording name")
        p = self.recordings_folder / name
        if not p.is_file():
            raise FileNotFoundError(name)
        return p
