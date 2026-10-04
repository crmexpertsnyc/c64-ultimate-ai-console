"""Relay between a browser terminal (WebSocket) and the Ultimate's Telnet remote menu (TCP 23).

The Ultimate serves its full menu UI over Telnet as VT100/ANSI text (DEC line graphics,
cursor addressing). The browser renders it with xterm.js; this module only moves bytes:

* device → browser: Telnet IAC negotiation is removed, the rest is decoded as Latin-1 text.
* browser → device: keystrokes are forwarded unchanged (the UI maps Backspace to RUN/STOP).

Nothing is sent to the device except what the user types.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("c64.telnet")

IAC = 0xFF
SB, SE = 0xFA, 0xF0
NEGOTIATION = {0xFB, 0xFC, 0xFD, 0xFE}  # WILL, WONT, DO, DONT


class TelnetFilter:
    """Streaming removal of Telnet commands (handles sequences split across reads)."""

    def __init__(self) -> None:
        self.state = "data"

    def feed(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            if self.state == "data":
                if b == IAC:
                    self.state = "iac"
                else:
                    out.append(b)
            elif self.state == "iac":
                if b == IAC:  # escaped 0xFF
                    out.append(IAC)
                    self.state = "data"
                elif b in NEGOTIATION:
                    self.state = "option"
                elif b == SB:
                    self.state = "sub"
                else:
                    self.state = "data"
            elif self.state == "option":
                self.state = "data"
            elif self.state == "sub":
                if b == IAC:
                    self.state = "sub_iac"
            elif self.state == "sub_iac":
                self.state = "data" if b == SE else "sub"
        return bytes(out)


def escape_outgoing(data: bytes) -> bytes:
    return data.replace(bytes([IAC]), bytes([IAC, IAC]))


async def open_telnet(host: str, port: int = 23, timeout: float = 5.0) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
