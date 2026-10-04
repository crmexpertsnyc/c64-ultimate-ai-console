"""Ultimate video/audio stream adapter.

The Ultimate sends raw UDP (not browser-playable):

* video → 780-byte datagrams: 12-byte LE header (seq, frame, line|0x8000 on last packet,
  pixels/line=384, lines/packet=4, bpp=4, encoding=0) + 4 lines × 384 px of 4-bit VIC
  colour indices, left pixel in the low nibble. 68 packets per PAL frame (384×272).
* audio → 770-byte datagrams: 2-byte seq + 192 stereo s16le samples (L,R), ~47983 Hz PAL.

This module receives those datagrams, reassembles frames and converts them into
MJPEG (for an <img> tag) and raw PCM chunks (for a WebAudio player over WebSocket).
It runs inside the backend's event loop and never blocks the rest of the application;
if the UDP ports cannot be bound the stream is reported unavailable.
"""

from __future__ import annotations

import asyncio
import io
import logging
import socket
import struct
import time
from collections import deque
from typing import Any

from PIL import Image, ImageDraw

log = logging.getLogger("c64.streams")

WIDTH = 384
PAL_HEIGHT = 272
AUDIO_RATE_PAL = 47983

# "Pepto" PAL palette (VICE default family).
PALETTE = [
    (0x00, 0x00, 0x00), (0xFF, 0xFF, 0xFF), (0x68, 0x37, 0x2B), (0x70, 0xA4, 0xB2),
    (0x6F, 0x3D, 0x86), (0x58, 0x8D, 0x43), (0x35, 0x28, 0x79), (0xB8, 0xC7, 0x6F),
    (0x6F, 0x4F, 0x25), (0x43, 0x39, 0x00), (0x9A, 0x67, 0x59), (0x44, 0x44, 0x44),
    (0x6C, 0x6C, 0x6C), (0x9A, 0xD2, 0x84), (0x6C, 0x5E, 0xB5), (0x95, 0x95, 0x95),
]
_FLAT_PALETTE = [c for rgb in PALETTE for c in rgb] + [0] * (768 - 48)
_LO = bytes(b & 0x0F for b in range(256))
_HI = bytes(b >> 4 for b in range(256))

VIDEO_HEADER = struct.Struct("<HHHHBBH")


def unpack_pixels(payload: bytes) -> bytes:
    """4-bit packed → one byte per pixel (left pixel = low nibble)."""
    out = bytearray(len(payload) * 2)
    out[0::2] = payload.translate(_LO)
    out[1::2] = payload.translate(_HI)
    return bytes(out)


class VideoAssembler:
    def __init__(self) -> None:
        self.buffer = bytearray(WIDTH * PAL_HEIGHT)
        self.height = PAL_HEIGHT
        self.frame: bytes | None = None
        self.frame_number = -1
        self.frames = 0
        self.packets = 0
        self.bad_packets = 0
        self.last_frame_at = 0.0
        self.new_frame = asyncio.Event()
        # Latency instrumentation (wall-clock seconds, same clock the browser is compared against).
        self._current = -1
        self._started_at = 0.0
        self.assembly_ms = 0.0
        self._completions: deque[float] = deque(maxlen=120)

    @property
    def source_fps(self) -> float:
        """Frames per second arriving from the Ultimate over the last ~2 s."""
        now = time.time()
        recent = [t for t in self._completions if now - t <= 2.0]
        if len(recent) < 2:
            return 0.0
        return (len(recent) - 1) / max(1e-6, recent[-1] - recent[0])

    def feed(self, data: bytes) -> bool:
        """Consume one datagram. Returns True when a frame completed."""
        if len(data) < VIDEO_HEADER.size:
            self.bad_packets += 1
            return False
        _seq, frame, line, ppl, lpp, bpp, enc = VIDEO_HEADER.unpack_from(data)
        if ppl != WIDTH or bpp != 4 or enc != 0 or lpp == 0:
            self.bad_packets += 1
            return False
        self.packets += 1
        last = bool(line & 0x8000)
        line &= 0x7FFF
        if frame != self._current:  # first packet of a new frame
            self._current = frame
            self._started_at = time.time()
        pixels = unpack_pixels(data[VIDEO_HEADER.size:VIDEO_HEADER.size + WIDTH * lpp // 2])
        start = line * WIDTH
        if start + len(pixels) <= len(self.buffer):
            self.buffer[start:start + len(pixels)] = pixels
        if last:
            self.height = min(PAL_HEIGHT, line + lpp)
            self.frame = bytes(self.buffer[: WIDTH * self.height])
            self.frame_number = frame
            self.frames += 1
            self.last_frame_at = time.time()
            self.assembly_ms = (self.last_frame_at - self._started_at) * 1000 if self._started_at else 0.0
            self._completions.append(self.last_frame_at)
            self.new_frame.set()
            self.new_frame = asyncio.Event()
            return True
        return False


def frame_to_jpeg(frame: bytes, height: int, quality: int = 80, scale: int = 2) -> bytes:
    img = Image.frombytes("P", (WIDTH, height), frame)
    img.putpalette(_FLAT_PALETTE)
    img = img.convert("RGB")
    if scale != 1:
        img = img.resize((WIDTH * scale, height * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def render_text_frame(lines: list[str], label: str = "SIMULATED", fmt: str = "JPEG") -> bytes:
    """Synthesise a C64-looking frame (for SIMULATE_C64) as JPEG (or PNG)."""
    img = Image.new("RGB", (WIDTH, PAL_HEIGHT), PALETTE[14])
    draw = ImageDraw.Draw(img)
    draw.rectangle([32, 36, 32 + 320 - 1, 36 + 200 - 1], fill=PALETTE[6])
    for i, line in enumerate(lines[:25]):
        draw.text((32, 36 + i * 8 - 1), line[:40], fill=PALETTE[14])
    draw.text((4, 4), label, fill=PALETTE[1])
    img = img.resize((WIDTH * 2, PAL_HEIGHT * 2), Image.NEAREST)
    buf = io.BytesIO()
    if fmt.upper() == "PNG":
        img.save(buf, "PNG")
    else:
        img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, on_packet):  # noqa: ANN001
        self.on_packet = on_packet

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.on_packet(data)


class AudioFanout:
    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue[bytes]] = set()
        self.packets = 0
        self.last_packet_at = 0.0

    def feed(self, data: bytes) -> None:
        if len(data) < 2 + 4:
            return
        self.packets += 1
        self.last_packet_at = time.time()
        pcm = data[2:]
        for q in list(self.subscribers):
            if q.qsize() < 64:  # drop when a client falls behind instead of buffering forever
                q.put_nowait(pcm)

    def subscribe(self) -> asyncio.Queue[bytes]:
        q: asyncio.Queue[bytes] = asyncio.Queue()
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[bytes]) -> None:
        self.subscribers.discard(q)


def detect_local_ip(device_host: str) -> str:
    """The local address the OS would use to reach the device."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect((device_host.split(":")[0], 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class StreamService:
    def __init__(self, video_port: int = 11000, audio_port: int = 11001):
        self.video_port = video_port
        self.audio_port = audio_port
        self.video = VideoAssembler()
        self.audio = AudioFanout()
        self._transports: dict[str, asyncio.DatagramTransport] = {}
        self.active: dict[str, str] = {}  # stream -> target sent to the device
        self.errors: dict[str, str] = {}

    async def bind(self, kind: str) -> bool:
        if kind in self._transports:
            return True
        loop = asyncio.get_running_loop()
        port = self.video_port if kind == "video" else self.audio_port
        handler = self.video.feed if kind == "video" else self.audio.feed
        try:
            transport, _ = await loop.create_datagram_endpoint(lambda: _Proto(handler), local_addr=("0.0.0.0", port))
        except OSError as exc:
            self.errors[kind] = f"cannot bind UDP {port}: {exc}"
            log.warning(self.errors[kind])
            return False
        self._transports[kind] = transport  # type: ignore[assignment]
        self.errors.pop(kind, None)
        return True

    def unbind(self, kind: str) -> None:
        t = self._transports.pop(kind, None)
        if t:
            t.close()

    def close(self) -> None:
        for kind in list(self._transports):
            self.unbind(kind)

    def status(self) -> dict[str, Any]:
        now = time.time()
        return {
            "video": {"active": "video" in self.active, "target": self.active.get("video"),
                      "bound": "video" in self._transports, "frames": self.video.frames,
                      "packets": self.video.packets, "badPackets": self.video.bad_packets,
                      "lastFrameAge": round(now - self.video.last_frame_at, 1) if self.video.last_frame_at else None,
                      "error": self.errors.get("video")},
            "audio": {"active": "audio" in self.active, "target": self.active.get("audio"),
                      "bound": "audio" in self._transports, "packets": self.audio.packets,
                      "sampleRate": AUDIO_RATE_PAL, "listeners": len(self.audio.subscribers),
                      "error": self.errors.get("audio")},
        }

    def latest_jpeg(self) -> bytes | None:
        if self.video.frame is None:
            return None
        return frame_to_jpeg(self.video.frame, self.video.height)
