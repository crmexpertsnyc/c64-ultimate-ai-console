"""Software stand-in for the ESP32 joystick bridge — same protocol and failsafe, no hardware.

Run it to try the console's joystick features before the board is built::

    .venv\\Scripts\\python -m app.ultimate.joybridge_fake          # listens on UDP 6464

then set the joystick bridge address to ``127.0.0.1`` in Settings. Every change of the five
lines is printed, so you can see exactly what the C64 would receive.
"""

from __future__ import annotations

import asyncio
import struct
import time

from .joybridge import DEFAULT_PORT, MAGIC, T_PING, T_PONG, T_RELEASE, T_STATE, VERSION, inputs_of

FAILSAFE = 0.35


class FakeBridge(asyncio.DatagramProtocol):
    def __init__(self, ports: tuple[int, ...] = (2,), name: str = "fake-joybridge", verbose: bool = False):
        self.ports = ports
        self.name = name
        self.verbose = verbose
        self.masks = {1: 0, 2: 0}
        self.history: list[tuple[int, int]] = []  # (port, mask) after every change
        self.last_state = 0.0
        self.last_seq: int | None = None
        self.started = time.monotonic()
        self.transport: asyncio.DatagramTransport | None = None
        self._watch: asyncio.Task | None = None

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport
        self._watch = asyncio.get_running_loop().create_task(self._failsafe())

    def connection_lost(self, exc) -> None:  # noqa: ANN001
        if self._watch:
            self._watch.cancel()

    def _set(self, port: int, mask: int) -> None:
        if port in self.ports and self.masks[port] != mask:
            self.masks[port] = mask
            self.history.append((port, mask))
            if self.verbose:
                print(f"port {port}: {' '.join(inputs_of(mask)) or '(released)'}", flush=True)

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        if len(data) < 8 or data[:4] != MAGIC or data[4] != VERSION:
            return
        kind, seq = data[5], struct.unpack_from("<H", data, 6)[0]
        if kind == T_PING:
            ports = sum(1 << (p - 1) for p in self.ports)
            payload = struct.pack("<BBBBBbI", ports, self.masks[1], self.masks[2], 1, 0, -50,
                                  int(time.monotonic() - self.started)) + self.name.encode()
            self.transport.sendto(MAGIC + struct.pack("<BBH", VERSION, T_PONG, seq) + payload, addr)  # type: ignore[union-attr]
        elif kind == T_RELEASE:
            for p in (1, 2):
                self._set(p, 0)
        elif kind == T_STATE:
            # Drop packets that arrive out of order (UDP may reorder); accept after a long silence.
            recent = self.last_seq is not None and time.monotonic() - self.last_state < 1.0
            if recent and ((seq - self.last_seq) & 0xFFFF) >= 0x8000:  # type: ignore[operator]
                return
            self.last_seq, self.last_state = seq, time.monotonic()
            body = data[8:]
            for i in range(0, len(body) - 1, 2):
                self._set(body[i], body[i + 1] & 0x1F)

    async def _failsafe(self) -> None:
        while True:
            await asyncio.sleep(0.05)
            if any(self.masks.values()) and time.monotonic() - self.last_state > FAILSAFE:
                if self.verbose:
                    print("failsafe: no updates — releasing everything", flush=True)
                for p in (1, 2):
                    self._set(p, 0)


async def serve(host: str = "0.0.0.0", port: int = DEFAULT_PORT, **kw) -> tuple[asyncio.DatagramTransport, FakeBridge]:
    loop = asyncio.get_running_loop()
    return await loop.create_datagram_endpoint(lambda: FakeBridge(**kw), local_addr=(host, port))  # type: ignore[return-value]


async def _main() -> None:
    await serve(verbose=True, ports=(1, 2))
    print(f"fake joystick bridge listening on UDP {DEFAULT_PORT} (ports 1 and 2) — Ctrl+C to stop", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
