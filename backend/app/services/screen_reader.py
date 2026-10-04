"""Reads what the real C64 Ultimate is showing — from its memory, read-only (DMA reads over the REST API).

The same screen sample the browser emulator produces (frontend/public/emulator/play.html: screen()), so the
startup analyzer, 💡 hints, 🧭 co-pilot and 🏆 score tracking work on the real machine too:
  * where the screen is: the VIC bank (CIA 2 $DD00 + $DD02) and $D018; text / bitmap / blank from $D011;
  * every row decoded three ways (ROM upper case, ROM lower case, "ASCII − 32" game fonts) — the reading with
    real words wins; scrolling text stitched across samples;
  * the BASIC banner / last line (loading, READY.), whether the KERNAL keyboard routine is active ($0314);
  * every ~10 s, which joystick register the program reads ($DC00 = port 2, $DC01 = port 1 / keyboard).
Nothing is ever written to the C64. When I/O registers can't be read, the KERNAL's screen page ($0288) is used.
"""

from __future__ import annotations

import re
import time
from typing import Any

READ_OPS = {0xAD, 0xAE, 0xAC, 0x2D, 0x0D, 0x4D, 0xCD, 0x2C, 0xEC, 0xCC, 0xBD, 0xB9, 0xBE, 0xBC,
            0x3D, 0x39, 0x1D, 0x19, 0x5D, 0x59, 0xDD, 0xD9}
WRITE_OPS = {0x8D, 0x8E, 0x8C, 0x9D, 0x99}
WORDS = re.compile(r"\b(PRESS|FIRE|SPACE|START|PLAY|PLAYER|GAME|KEY|JOYSTICK|PORT|RETURN|STOP|RUN|THE|TO|FOR|AND|BY|"
                   r"OR|OF|YOU|SCORE|LEVEL|OPTIONS|SELECT|MUSIC|ON|OFF|YES|NO|LIVES|TRAINER|BUTTON|ANY|CONTINUE|"
                   r"BEGIN|HI|TOP|ROUND|STAGE|TIME|BONUS)\b")
GLYPH = "\u0001"


def _rom(code: int, lower: bool) -> str:
    c = code & 0x7F
    if c == 32:
        return " "
    if 1 <= c <= 26:
        return chr(64 + c)
    if 33 <= c <= 63:
        return chr(c)
    if c == 0:
        return "@"
    if lower and 65 <= c <= 90:
        return chr(c)
    return GLYPH


def _ascii32(code: int) -> str:
    c = code & 0x7F
    if c in (0, 32):
        return " "
    if c < 64:
        return chr(c + 32).upper()
    return GLYPH


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t.replace(GLYPH, "")).strip()


def _score(t: str) -> int:
    return len(WORDS.findall(t))


def decode_rows(screen: bytes, *, lower: bool, rom_font: bool) -> tuple[list[str], list[str]]:
    """(cleaned rows, raw 40-column rows) — raw keeps the columns, for numbers printed under their label."""
    rows, raw = [], []
    for r in range(25):
        line = screen[r * 40:(r + 1) * 40]
        t_raw = "".join(_rom(b, lower) for b in line)
        a_raw = "".join(_ascii32(b) for b in line)
        t, a = _clean(t_raw), _clean(a_raw)
        use_a = not rom_font and _score(a) > _score(t)
        rows.append(a if use_a else t)
        raw.append((a_raw if use_a else t_raw).replace(GLYPH, " "))
    return rows, raw


def port_reads(ram: bytes, base: int = 0) -> dict[str, int]:
    r0 = r1 = w0 = 0
    for i in range(len(ram) - 2):
        if ram[i + 2] != 0xDC:
            continue
        op, lo = ram[i], ram[i + 1]
        if op in READ_OPS:
            if lo == 0x00:
                r0 += 1
            elif lo == 0x01:
                r1 += 1
        elif op in WRITE_OPS and lo == 0x00:
            w0 += 1
    return {"dc00Reads": r0, "dc01Reads": r1, "dc00Writes": w0}


class ScreenWatcher:
    """Samples the real C64's screen; keeps the state needed across samples (scrollers, layout, port scan)."""

    def __init__(self, client):  # noqa: ANN001 - UltimateClient (read_memory only)
        self.client = client
        self.prev_rows: list[str] = [""] * 25
        self.scroll: dict[int, str] = {}
        self.scrolled: dict[int, bool] = {}
        self.layout = ""
        self.n = 0
        self.ports = {"dc00Reads": 0, "dc01Reads": 0, "dc00Writes": 0, "at": 0.0}
        self.last: dict[str, Any] | None = None
        self.last_at = 0.0

    def _stitch(self, row: int, text: str) -> None:
        acc = self.scroll.get(row, "")
        if not acc:
            self.scroll[row] = text
            return
        if text in acc:
            return
        k = min(len(acc), len(text))
        while k >= 6 and not acc.endswith(text[:k]):
            k -= 1
        if k >= 6:
            self.scroll[row] = (acc + text[k:])[-600:]
            self.scrolled[row] = True
        elif self.scrolled.get(row) and len(text) >= 12:
            self.scroll[row] = (acc + " | " + text)[-600:]
        else:
            self.scroll[row] = text
            self.scrolled[row] = False

    async def sample(self, min_interval: float = 0.6) -> dict[str, Any]:
        now = time.time()
        if self.last is not None and now - self.last_at < min_interval:
            return self.last  # several viewers share one read
        read = self.client.read_memory
        vic = await read(0xD011, 8)            # $D011 … $D018
        cia2 = await read(0xDD00, 3)            # PRA, PRB, DDRA
        d011, d018 = vic[0], vic[7]
        bank = 3 - ((cia2[0] | (~cia2[2] & 0xFF)) & 3)
        base = bank * 0x4000 + (d018 >> 4) * 0x400
        io_ok = not (vic == bytes(8) and cia2 == bytes(3))
        if not io_ok:  # I/O not visible to DMA: fall back to where the KERNAL thinks the screen is
            page = (await read(0x0288, 1))[0]
            base = page * 256 if 0 < page < 0xFC else 0x0400
            d011, d018 = 0x1B, 0x14
        screen = await read(base, 1000)
        charset = d018 & 0x0E
        rom_font = bank in (0, 2) and charset in (4, 6)
        mode = "blank" if not d011 & 0x10 else "bitmap" if d011 & 0x20 else "text"
        rows, raw = decode_rows(screen, lower=rom_font and charset == 6, rom_font=rom_font)
        layout = f"{base}:{mode}:{d018}"
        if layout != self.layout:
            self.scroll.clear()
            self.scrolled.clear()
            self.layout = layout
        static, moving = [], 0
        for r, t in enumerate(rows):
            if not t or not re.search(r"[A-Z]{2}", t):
                continue
            if self.prev_rows[r] == t:
                static.append(t)
            elif self.prev_rows[r]:
                moving += 1
                self._stitch(r, t)
        self.prev_rows = rows
        if now - self.ports["at"] > 10:
            ram = await read(0x0200, 0xFE00)
            self.ports = {**port_reads(ram), "at": now}
        irq = await read(0x0314, 2)
        non_empty = [r for r in rows if r]
        self.n += 1
        self.last = {
            "at": int(now * 1000), "n": self.n, "mode": mode, "base": base, "rows": rows, "rawRows": raw,
            "staticText": " / ".join(static),
            "scrollText": " / ".join(self.scroll[r] for r in sorted(self.scroll) if self.scrolled.get(r)),
            "movingRows": moving, "basicBanner": any("COMMODORE 64 BASIC" in r for r in rows),
            "lastLine": non_empty[-1] if non_empty else "",
            "kernalIrq": (irq[0] | irq[1] << 8) in (0xEA31, 0xEA81, 0xEA7E),
            "ports": {k: v for k, v in self.ports.items() if k != "at"},
            "ioVisible": io_ok, "source": "c64",
        }
        self.last_at = now
        return self.last
