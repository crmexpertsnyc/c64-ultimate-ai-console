"""Ultimate menu screen parsing and closed-loop menu control.

``GET /v1/machine:menu_screen`` returns exactly 2000 bytes:
  bytes    0..999  — 40×25 character matrix, row-major, bit 7 = reverse video
  bytes 1000..1999 — 40×25 colour matrix, bits 0-3 foreground, bits 4-7 background

The documentation does not pin down whether the low 7 bits are ASCII or C64 screen
codes, so the parser inspects the data and picks the interpretation that yields more
letters. The chosen encoding is reported so it can be checked against a real device.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from .capabilities import CapabilityMatrix
from .client import UltimateClient
from .input import InputController, InputUnsupported
from .petscii import screen_code_to_char

log = logging.getLogger("c64.menu")

COLS, ROWS = 40, 25
SCREEN_BYTES = COLS * ROWS

C64_PALETTE = [
    ("black", "#000000"), ("white", "#FFFFFF"), ("red", "#880000"), ("cyan", "#AAFFEE"),
    ("purple", "#CC44CC"), ("green", "#00CC55"), ("blue", "#0000AA"), ("yellow", "#EEEE77"),
    ("orange", "#DD8855"), ("brown", "#664400"), ("light red", "#FF7777"), ("dark grey", "#333333"),
    ("grey", "#777777"), ("light green", "#AAFF66"), ("light blue", "#0088FF"), ("light grey", "#BBBBBB"),
]

Encoding = Literal["ascii", "screencode"]


class MenuFormatError(ValueError):
    pass


@dataclass
class Cell:
    ch: str
    code: int
    reverse: bool
    fg: int
    bg: int


@dataclass
class MenuScreen:
    encoding: Encoding
    cells: list[list[Cell]]
    raw_hash: str
    selected_row: int | None = None
    background: int = 0
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.rstrip() for line in self.lines).rstrip()

    @property
    def selected_text(self) -> str | None:
        if self.selected_row is None:
            return None
        return self.lines[self.selected_row].strip()

    @property
    def title(self) -> str:
        for line in self.lines:
            if line.strip():
                return line.strip()
        return ""

    def to_dict(self, include_cells: bool = True) -> dict[str, Any]:
        rows = []
        for i, line in enumerate(self.lines):
            row: dict[str, Any] = {"index": i, "text": line, "selected": i == self.selected_row}
            if include_cells:
                row["cells"] = [[c.ch, c.fg, c.bg, int(c.reverse)] for c in self.cells[i]]
            rows.append(row)
        return {
            "encoding": self.encoding,
            "hash": self.raw_hash,
            "text": self.text,
            "title": self.title,
            "selectedRow": self.selected_row,
            "selectedText": self.selected_text,
            "background": self.background,
            "palette": [hex_ for _, hex_ in C64_PALETTE],
            "rows": rows,
        }


def _ascii_char(code: int) -> str:
    c = code & 0x7F
    if 0x20 <= c <= 0x7E:
        return chr(c)
    return " " if c == 0 else "·"


def detect_encoding(chars: bytes) -> Encoding:
    ascii_letters = sum(1 for b in chars if 0x41 <= (b & 0x7F) <= 0x5A or 0x61 <= (b & 0x7F) <= 0x7A)
    sc_letters = sum(1 for b in chars if 0x01 <= (b & 0x7F) <= 0x1A)
    return "ascii" if ascii_letters >= sc_letters else "screencode"


def parse_menu_screen(data: bytes, encoding: Encoding | None = None) -> MenuScreen:
    if len(data) != 2 * SCREEN_BYTES:
        raise MenuFormatError(f"menu_screen must be {2 * SCREEN_BYTES} bytes, got {len(data)}")
    chars, colors = data[:SCREEN_BYTES], data[SCREEN_BYTES:]
    enc = encoding or detect_encoding(chars)
    decode = _ascii_char if enc == "ascii" else screen_code_to_char
    cells: list[list[Cell]] = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            i = r * COLS + c
            code, color = chars[i], colors[i]
            ch = decode(code)
            if enc == "screencode" and (code & 0x7F) == 0x20:
                ch = " "
            row.append(Cell(ch=ch, code=code, reverse=bool(code & 0x80), fg=color & 0x0F, bg=(color >> 4) & 0x0F))
        cells.append(row)
    background = Counter(cell.bg for row in cells for cell in row).most_common(1)[0][0]
    screen = MenuScreen(
        encoding=enc,
        cells=cells,
        raw_hash=hashlib.sha1(data).hexdigest()[:16],
        background=background,
        lines=["".join(c.ch for c in row) for row in cells],
    )
    screen.selected_row = find_selected_row(screen)
    return screen


def find_selected_row(screen: MenuScreen) -> int | None:
    """The highlighted row is the one with the most reverse-video or off-background cells."""
    best, best_score = None, 0
    for r, row in enumerate(screen.cells):
        highlighted = [c for c in row if c.reverse or c.bg != screen.background]
        score = len(highlighted)
        # Ignore full-width bars (title/status lines usually span the whole row).
        if score >= COLS:
            continue
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 3 else None


# Menu actions → REST key names (the firmware maps keyboard events to menu keystrokes
# while the menu is active).
MENU_KEYS: dict[str, tuple[str, ...]] = {
    "up": ("left_shift", "cursor_up_down"),
    "down": ("cursor_up_down",),
    "left": ("left_shift", "cursor_left_right"),
    "right": ("cursor_left_right",),
    "return": ("return",),
    "back": ("left_shift", "cursor_left_right"),  # the Ultimate UI uses cursor-left to go up a level
    "exit": ("run_stop",),
    "home": ("clr_home",),
    "page_up": ("f1",),
    "page_down": ("f7",),
}


class MenuController:
    def __init__(self, client: UltimateClient, caps: CapabilityMatrix, inputs: InputController,
                 poll_interval: float = 0.12, poll_attempts: int = 6):
        self.client = client
        self.caps = caps
        self.inputs = inputs
        self.poll_interval = poll_interval
        self.poll_attempts = poll_attempts
        self._lock = asyncio.Lock()

    async def read(self) -> MenuScreen | None:
        if self.caps.state("menuScreen") == "unsupported":
            raise InputUnsupported("this firmware does not provide machine:menu_screen")
        data = await self.client.menu_screen()
        if data is None:
            return None
        screen = parse_menu_screen(data)
        self.caps.record_use("menuScreen", True)
        return screen

    async def _poll(self, predicate) -> MenuScreen | None:  # noqa: ANN001
        screen = None
        for _ in range(self.poll_attempts):
            await asyncio.sleep(self.poll_interval)
            screen = await self.read()
            if predicate(screen):
                return screen
        return screen

    async def _can_read(self) -> bool:
        return self.caps.state("menuScreen") != "unsupported"

    async def open(self) -> dict[str, Any]:
        async with self._lock:
            if await self._can_read():
                current = await self.read()
                if current is not None:
                    return {"action": "open", "changed": False, "verified": True, "note": "menu already open",
                            "screen": current.to_dict()}
            await self.client.menu_button()
            self.caps.record_use("menuButton", True)
            if not await self._can_read():
                return {"action": "open", "changed": True, "verified": False,
                        "note": "menu_screen unavailable; toggle sent but cannot be verified"}
            after = await self._poll(lambda s: s is not None)
            return {"action": "open", "changed": after is not None, "verified": after is not None,
                    "screen": after.to_dict() if after else None,
                    "note": "" if after else "menu did not appear on menu_screen"}

    async def close(self) -> dict[str, Any]:
        async with self._lock:
            if await self._can_read():
                current = await self.read()
                if current is None:
                    return {"action": "close", "changed": False, "verified": True, "note": "menu already closed"}
            await self.client.menu_button()
            self.caps.record_use("menuButton", True)
            if not await self._can_read():
                return {"action": "close", "changed": True, "verified": False,
                        "note": "menu_screen unavailable; toggle sent but cannot be verified"}
            after = await self._poll(lambda s: s is None)
            return {"action": "close", "changed": after is None, "verified": after is None,
                    "note": "" if after is None else "menu still visible"}

    async def navigate(self, action: str) -> dict[str, Any]:
        """Closed loop: read → send exactly one key → re-read until the screen changes.

        Input is never re-sent automatically: a missing change may mean the cursor is at
        a boundary, and repeating a keystroke could select something unintended.
        """
        if action not in MENU_KEYS:
            raise ValueError(f"unknown menu action {action!r}; expected one of {sorted(MENU_KEYS)}")
        if self.inputs.mode != "rest":
            raise InputUnsupported("menu navigation needs the REST input API (machine:input)")
        async with self._lock:
            before = await self.read()
            if before is None:
                return {"action": action, "changed": False, "verified": True,
                        "note": "menu is not open — nothing sent"}
            await self.inputs._send([{"kind": "keyboard", "inputs": list(MENU_KEYS[action]), "transition": "tap"}])
            after = await self._poll(lambda s: s is None or s.raw_hash != before.raw_hash)
            changed = after is None or after.raw_hash != before.raw_hash
            note = ""
            if not changed:
                note = "screen unchanged after key press (at a boundary, or key ignored); not retried"
            elif after is None:
                note = "menu closed"
            return {
                "action": action,
                "changed": changed,
                "verified": True,
                "before": {"selectedRow": before.selected_row, "selectedText": before.selected_text},
                "after": {"selectedRow": after.selected_row if after else None,
                          "selectedText": after.selected_text if after else None},
                "note": note,
                "screen": after.to_dict() if after else None,
            }
