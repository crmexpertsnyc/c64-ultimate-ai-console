"""Driver for the ESP32 joystick bridge: a small board wired into the C64 joystick port(s).

The C64 reads a joystick as five switches (up, down, left, right, fire) that pull pins of the
control port to ground. The bridge closes those switches through optocouplers on command, so it
works with every firmware and every game — no REST input API needed.

Protocol (UDP, default port 6464, all integers little-endian)::

    0..3  magic  b"C64J"
    4     version (1)
    5     type
    6..7  sequence number (u16, wraps)
    8..   payload

    0x01 STATE        payload: one or more (port u8, mask u8) pairs
                      mask bits: 0 up, 1 down, 2 left, 3 right, 4 fire
    0x02 PING         no payload (may be broadcast for discovery)
    0x03 PONG         bridge → console: ports u8 (bit0 = port 1, bit1 = port 2), mask1 u8, mask2 u8,
                      fw_major u8, fw_minor u8, rssi i8, uptime_s u32, name (ASCII, rest of packet)
    0x04 RELEASE_ALL  no payload

Safety: the bridge releases everything when it has not heard a STATE packet for 350 ms while any
line is held, so the console repeats the current state every 100 ms while something is pressed. A
lost network, crashed console or unplugged PC therefore never leaves a direction held.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
import time
from typing import Any

log = logging.getLogger("c64.joybridge")

MAGIC = b"C64J"
VERSION = 1
T_STATE, T_PING, T_PONG, T_RELEASE = 0x01, 0x02, 0x03, 0x04
BITS = {"up": 0x01, "down": 0x02, "left": 0x04, "right": 0x08, "fire": 0x10}
DEFAULT_PORT = 6464
KEEPALIVE = 0.1       # resend held state this often (bridge failsafe is 0.35 s)
PING_EVERY = 2.0
OFFLINE_AFTER = 5.0
TAP_SECONDS = 0.1     # five PAL frames: long enough for any game's joystick poll


def mask_of(inputs: list[str] | tuple[str, ...]) -> int:
    mask = 0
    for name in inputs:
        bit = BITS.get(name.lower())
        if bit is None:
            raise ValueError(f"the joystick bridge supports {', '.join(BITS)} (not {name!r})")
        mask |= bit
    return mask


def inputs_of(mask: int) -> list[str]:
    return [name for name, bit in BITS.items() if mask & bit]


def packet(kind: int, seq: int, payload: bytes = b"") -> bytes:
    return MAGIC + struct.pack("<BBH", VERSION, kind, seq & 0xFFFF) + payload


def parse_pong(data: bytes) -> dict[str, Any] | None:
    if len(data) < 18 or data[:4] != MAGIC or data[5] != T_PONG:
        return None
    ports, m1, m2, major, minor, rssi, uptime = struct.unpack_from("<BBBBBbI", data, 8)
    return {"ports": [p for p in (1, 2) if ports & (1 << (p - 1))], "held": {1: inputs_of(m1), 2: inputs_of(m2)},
            "firmware": f"{major}.{minor}", "rssi": rssi, "uptime": uptime,
            "name": data[18:].decode("ascii", "replace").strip("\x00 ") or "joybridge"}


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, owner: JoyBridge):
        self.owner = owner

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        self.owner._on_datagram(data, addr)

    def error_received(self, exc: Exception) -> None:  # Windows reports ICMP "port unreachable" here
        log.debug("joybridge socket error: %s", exc)


class JoyBridge:
    def __init__(self) -> None:
        self.host = ""
        self.port = DEFAULT_PORT
        self.masks = {1: 0, 2: 0}
        self.info: dict[str, Any] | None = None
        self.last_pong = 0.0
        self.rtt_ms: float | None = None
        self.on_change = None  # callback() when online state changes
        self._seq = 0
        self._addr: tuple[str, int] | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task | None = None
        self._ping_sent: dict[int, float] = {}
        self._was_online = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- lifecycle
    @property
    def configured(self) -> bool:
        return bool(self.host)

    @property
    def online(self) -> bool:
        return self.configured and time.monotonic() - self.last_pong < OFFLINE_AFTER

    def supports(self, port: int) -> bool:
        return self.online and port in (self.info or {}).get("ports", [])

    async def configure(self, host: str, port: int = DEFAULT_PORT) -> None:
        host = (host or "").strip()
        if host == self.host and port == self.port and (self._task or not host):
            return
        await self.close()
        self.host, self.port = host, port
        if not host:
            return
        loop = asyncio.get_running_loop()
        try:
            ip = (await loop.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM))[0][4][0]
        except OSError as exc:
            log.warning("joystick bridge %s: cannot resolve (%s); will retry", host, exc)
            ip = None
        self._addr = (ip, port) if ip else None
        self._transport, _ = await loop.create_datagram_endpoint(lambda: _Proto(self), local_addr=("0.0.0.0", 0))
        self._task = asyncio.create_task(self._loop(), name="joybridge")

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._transport:
            if any(self.masks.values()):
                self._send(T_RELEASE)
            self._transport.close()
            self._transport = None
        self.masks = {1: 0, 2: 0}
        self.info, self.last_pong, self.rtt_ms, self._addr = None, 0.0, None, None
        self._notify()

    async def _loop(self) -> None:
        next_ping = 0.0
        while True:
            now = time.monotonic()
            if self._addr is None:
                try:
                    infos = await asyncio.get_running_loop().getaddrinfo(self.host, self.port, family=socket.AF_INET)
                    self._addr = (infos[0][4][0], self.port)
                except OSError:
                    await asyncio.sleep(5)
                    continue
            if now >= next_ping:
                self._send(T_PING)
                self._ping_sent[self._seq] = now
                next_ping = now + PING_EVERY
                if len(self._ping_sent) > 20:
                    self._ping_sent.clear()
            if any(self.masks.values()):
                self._send_state()
            self._notify()
            await asyncio.sleep(KEEPALIVE)

    # --------------------------------------------------------------- sending
    def _send(self, kind: int, payload: bytes = b"") -> None:
        if not self._transport or not self._addr:
            return
        self._seq = (self._seq + 1) & 0xFFFF
        try:
            self._transport.sendto(packet(kind, self._seq, payload), self._addr)
        except OSError as exc:
            log.debug("joybridge send failed: %s", exc)

    def _send_state(self) -> None:
        self._send(T_STATE, bytes([1, self.masks[1], 2, self.masks[2]]))

    def _require(self, port: int) -> None:
        if not self.online:
            raise RuntimeError("the joystick bridge is not responding")
        if port not in (self.info or {}).get("ports", []):
            raise ValueError(f"the joystick bridge is not wired to port {port}")

    async def press(self, port: int, inputs: list[str] | tuple[str, ...]) -> None:
        self._require(port)
        self.masks[port] |= mask_of(inputs)
        self._send_state()

    async def release(self, port: int, inputs: list[str] | tuple[str, ...]) -> None:
        self._require(port)
        self.masks[port] &= ~mask_of(inputs) & 0x1F
        self._send_state()

    async def set(self, port: int, inputs: list[str] | tuple[str, ...]) -> None:
        """Make exactly these lines held on ``port`` (everything else released)."""
        self._require(port)
        self.masks[port] = mask_of(inputs)
        self._send_state()

    async def tap(self, port: int, inputs: list[str] | tuple[str, ...], seconds: float = TAP_SECONDS) -> None:
        async with self._lock:
            await self.press(port, inputs)
            try:
                await asyncio.sleep(seconds)
            finally:
                self.masks[port] &= ~mask_of(inputs) & 0x1F
                self._send_state()

    def release_all(self) -> None:
        self.masks = {1: 0, 2: 0}
        self._send(T_RELEASE)
        self._send_state()

    async def test_pattern(self, port: int) -> list[str]:
        """Up, down, left, right, fire in turn — watch a joystick tester program to check the wiring."""
        done = []
        for name in BITS:
            await self.tap(port, [name], 0.25)
            await asyncio.sleep(0.15)
            done.append(name)
        return done

    # -------------------------------------------------------------- receiving
    def _on_datagram(self, data: bytes, addr) -> None:  # noqa: ANN001
        pong = parse_pong(data)
        if not pong:
            return
        seq = struct.unpack_from("<H", data, 6)[0]
        sent = self._ping_sent.pop(seq, None)
        if sent is not None:
            self.rtt_ms = round((time.monotonic() - sent) * 1000, 1)
        self.info = pong
        self.last_pong = time.monotonic()
        self._addr = (addr[0], addr[1])
        self._notify()

    def _notify(self) -> None:
        if self.online != self._was_online:
            self._was_online = self.online
            log.info("joystick bridge %s is %s", self.host, "online" if self.online else "offline")
            if self.on_change:
                self.on_change()

    def status(self) -> dict[str, Any]:
        info = self.info or {}
        return {
            "configured": self.configured,
            "host": self.host,
            "port": self.port,
            "online": self.online,
            "ports": info.get("ports", []),
            "name": info.get("name"),
            "firmware": info.get("firmware"),
            "rssi": info.get("rssi"),
            "uptime": info.get("uptime"),
            "rttMs": self.rtt_ms,
            "held": {str(p): inputs_of(m) for p, m in self.masks.items() if m},
        }


async def discover(timeout: float = 1.5, port: int = DEFAULT_PORT) -> list[dict[str, Any]]:
    """Broadcast a PING on the local network and collect the bridges that answer."""
    loop = asyncio.get_running_loop()
    found: dict[str, dict[str, Any]] = {}

    class Collect(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
            pong = parse_pong(data)
            if pong:
                found[addr[0]] = {"host": addr[0], **pong}

        def error_received(self, exc: Exception) -> None:
            pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", 0))
    transport, _ = await loop.create_datagram_endpoint(Collect, sock=sock)
    try:
        for _ in range(3):
            for target in ("255.255.255.255", *_subnet_broadcasts()):
                try:
                    transport.sendto(packet(T_PING, 0), (target, port))
                except OSError:
                    pass
            await asyncio.sleep(timeout / 3)
    finally:
        transport.close()
    return list(found.values())


def _subnet_broadcasts() -> list[str]:
    """Directed /24 broadcasts for each local IPv4 (Windows often ignores 255.255.255.255 on multi-NIC PCs)."""
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")):
                out.append(ip.rsplit(".", 1)[0] + ".255")
    except OSError:
        pass
    return list(dict.fromkeys(out))
