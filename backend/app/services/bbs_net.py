"""📟 BBS relay networking: where the relay may connect, and the telnet protocol it speaks.

Destination policy (the relay must never become a general-purpose proxy):
* Callers pass an approved board; host and port come from the directory, never from the browser.
* The host is resolved once, server-side. EVERY address it resolves to must be a public unicast address — loopback,
  private (RFC 1918, ULA), link-local, carrier-grade NAT, multicast, reserved, unspecified, documentation and cloud
  metadata addresses are refused, for IPv4 and IPv6, including IPv4 hidden inside IPv6 (mapped, NAT64, 6to4, Teredo).
* The connection then goes to one of those already-checked IP literals ("pinning"), so a second DNS answer can't
  redirect it (DNS rebinding).
* A few well-known non-telnet service ports are refused outright.

Telnet (RFC 854/855) client: answers option negotiation (ECHO, SGA, BINARY accepted; TTYPE and NAWS offered;
everything else refused), strips IAC sequences from the data, un-doubles IAC IAC, and escapes 0xFF on the way out.
Boards that don't speak telnet (many real-C64 boards behind Wi-Fi modems) use protocol "raw": bytes pass untouched.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable

# ports that are never a BBS: mail, DNS, web, databases, remote desktops, container/cluster APIs…
BLOCKED_PORTS = {0, 21, 22, 25, 53, 67, 68, 80, 110, 111, 123, 135, 137, 138, 139, 143, 161, 389, 443, 445, 465, 587,
                 636, 993, 995, 1433, 1521, 2049, 2375, 2376, 2379, 3306, 3389, 5432, 5900, 5984, 6379, 6443, 8080, 8443,
                 9200, 10250, 11211, 27017}
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_SIXTOFOUR = ipaddress.ip_network("2002::/16")
_TEREDO = ipaddress.ip_network("2001::/32")


class DestinationError(Exception):
    pass


def _v4_public(ip: ipaddress.IPv4Address) -> bool:
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
                                 or ip.is_private or ip.is_unspecified)


def is_public_address(addr: str) -> bool:
    """True only for a globally routable unicast address (and any IPv4 embedded in an IPv6 one is too)."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return _v4_public(ip)
    if ip.ipv4_mapped is not None:                                       # ::ffff:a.b.c.d
        return _v4_public(ip.ipv4_mapped)
    for net in _NAT64:                                                   # 64:ff9b::a.b.c.d
        if ip in net:
            return _v4_public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if ip in _SIXTOFOUR and ip.sixtofour is not None:                    # 2002:aabb:ccdd::
        return _v4_public(ip.sixtofour)
    if ip in _TEREDO:                                                    # tunnels to anywhere: refused
        return False
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
                                 or ip.is_private or ip.is_unspecified or ip.is_site_local)


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolve(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), 8)
    out: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        if sockaddr[0] not in out:
            out.append(sockaddr[0])
    return out


async def resolve_destination(host: str, port: int, resolver: Resolver = system_resolve) -> list[str]:
    """The checked IP literals to connect to (in resolver order). Raises DestinationError when not allowed."""
    if not 1 <= int(port) <= 65535 or int(port) in BLOCKED_PORTS:
        raise DestinationError(f"port {port} is not allowed for BBS connections")
    host = (host or "").strip().rstrip(".").lower()
    if not host or len(host) > 253:
        raise DestinationError("no host")
    try:
        addrs = await resolver(host, port)
    except (OSError, TimeoutError) as exc:
        raise DestinationError(f"couldn't look up {host}") from exc
    if not addrs:
        raise DestinationError(f"{host} has no address")
    bad = [a for a in addrs if not is_public_address(a)]
    if bad:                          # one private answer is enough to refuse (a rebinding / split-horizon trick)
        raise DestinationError(f"{host} points to a private or reserved address — refused")
    return addrs


Opener = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


async def tcp_open(ip: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(ip, port)


async def connect_pinned(host: str, port: int, *, resolver: Resolver = system_resolve, opener: Opener = tcp_open,
                         timeout: float = 10.0) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
    """Resolve + check once, then connect to the checked IP literal itself (never the name again)."""
    addrs = await resolve_destination(host, port, resolver)
    last: Exception | None = None
    for ip in addrs[:4]:
        try:
            reader, writer = await asyncio.wait_for(opener(ip, port), timeout)
            return reader, writer, ip
        except (OSError, TimeoutError) as exc:
            last = exc
    raise DestinationError(f"couldn't connect to {host}:{port} ({type(last).__name__ if last else 'no address'})")


# ------------------------------------------------------------------ telnet
IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
NOP, DM, BRK, IP, AO, AYT, EC, EL, GA = 241, 242, 243, 244, 245, 246, 247, 248, 249
BINARY, ECHO, SGA, TTYPE, NAWS = 0, 1, 3, 24, 31
TTYPE_IS, TTYPE_SEND = 0, 1


class TelnetClient:
    """Byte-level telnet client state machine. feed(bytes) → (data for the terminal, replies for the server)."""

    ACCEPT_REMOTE = {ECHO, SGA, BINARY}      # the server may WILL these
    OFFER_LOCAL = {TTYPE, NAWS, BINARY, SGA}  # we WILL these when asked

    def __init__(self, terminal_type: str = "ANSI", cols: int = 80, rows: int = 25):
        self.terminal_type = terminal_type
        self.cols, self.rows = cols, rows
        self.remote: dict[int, bool] = {}     # options the server has enabled
        self.local: dict[int, bool] = {}      # options we have enabled
        self._state = "data"
        self._cmd = 0
        self._sb: bytearray = bytearray()

    @property
    def binary(self) -> bool:
        return bool(self.local.get(BINARY) and self.remote.get(BINARY))

    def naws(self) -> bytes:
        def esc(n: int) -> bytes:                # 255 inside SB must be doubled too
            b = n.to_bytes(2, "big")
            return b.replace(b"\xff", b"\xff\xff")
        return bytes([IAC, SB, NAWS]) + esc(self.cols) + esc(self.rows) + bytes([IAC, SE])

    def resize(self, cols: int, rows: int) -> bytes:
        self.cols, self.rows = max(20, min(255, cols)), max(10, min(255, rows))
        return self.naws() if self.local.get(NAWS) else b""

    def _negotiate(self, cmd: int, opt: int) -> bytes:
        if cmd == WILL:
            ok = opt in self.ACCEPT_REMOTE
            if self.remote.get(opt) == ok:
                return b""                      # no change: no reply (avoids negotiation loops)
            self.remote[opt] = ok
            return bytes([IAC, DO if ok else DONT, opt])
        if cmd == WONT:
            if self.remote.get(opt) is False:
                return b""
            self.remote[opt] = False
            return bytes([IAC, DONT, opt])
        if cmd == DO:
            ok = opt in self.OFFER_LOCAL
            if self.local.get(opt) == ok:
                return b""
            self.local[opt] = ok
            reply = bytes([IAC, WILL if ok else WONT, opt])
            return reply + (self.naws() if ok and opt == NAWS else b"")
        if cmd == DONT:
            if self.local.get(opt) is False:
                return b""
            self.local[opt] = False
            return bytes([IAC, WONT, opt])
        return b""

    def _subneg(self, body: bytes) -> bytes:
        if len(body) >= 2 and body[0] == TTYPE and body[1] == TTYPE_SEND and self.local.get(TTYPE):
            return bytes([IAC, SB, TTYPE, TTYPE_IS]) + self.terminal_type.encode("ascii", "ignore") + bytes([IAC, SE])
        return b""

    def feed(self, chunk: bytes) -> tuple[bytes, bytes]:
        data, replies = bytearray(), bytearray()
        for b in chunk:
            st = self._state
            if st == "data":
                if b == IAC:
                    self._state = "iac"
                else:
                    data.append(b)
            elif st == "iac":
                if b == IAC:                     # escaped 0xFF: a real data byte
                    data.append(IAC)
                    self._state = "data"
                elif b in (WILL, WONT, DO, DONT):
                    self._cmd, self._state = b, "opt"
                elif b == SB:
                    self._sb, self._state = bytearray(), "sb"
                else:                            # NOP, GA, AYT, … carry no data
                    self._state = "data"
            elif st == "opt":
                replies += self._negotiate(self._cmd, b)
                self._state = "data"
            elif st == "sb":
                if b == IAC:
                    self._state = "sb_iac"
                elif len(self._sb) < 512:
                    self._sb.append(b)
            elif st == "sb_iac":
                if b == SE:
                    replies += self._subneg(bytes(self._sb))
                    self._state = "data"
                else:                            # IAC IAC inside SB = 255
                    if len(self._sb) < 512:
                        self._sb.append(b)
                    self._state = "sb"
        return bytes(data), bytes(replies)

    def encode(self, data: bytes) -> bytes:
        """Keyboard bytes → wire: IAC doubled; a bare CR becomes CR NUL unless BINARY is on (RFC 854)."""
        out = data.replace(b"\xff", b"\xff\xff")
        if not self.binary:
            out = out.replace(b"\r\n", b"\r\x00").replace(b"\r", b"\r\x00").replace(b"\r\x00\x00", b"\r\x00")
        return out
