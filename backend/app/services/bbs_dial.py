"""☎ Dial on my C64: the console starts CCGMS on the real C64 and dials an approved board through the C64 Ultimate's
modem emulation — the exact steps a person would do, typed through the app's keyboard input.

Verified on real hardware on 2026-10-04 (C64 Ultimate firmware 1.1.0s2, legacy input, Modem Interface
"ACIA / SwiftLink" at DE00/NMI, CCGMS Future v0.2): launch → F7 → modem type SwiftLink → AT/OK → ATDT → CONNECT.

What it changes: only CCGMS's own in-memory setting (modem type), each time it starts. It never changes the
C64 Ultimate's modem, network or firmware settings — if the Ultimate's modem isn't ACIA/SwiftLink, it stops and says
so. The dial string comes from the approved board (hostname/IP characters and a port only), never from the browser.
Off unless BBS_DIAL_ON_C64 is set.

"Add CCGMS" downloads one pinned file (CCGMS Future v0.2 from its GitHub release) and checks its SHA-256 before it
goes into the library.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Any

import httpx
from sqlalchemy import select

CCGMS_URL = "https://github.com/mist64/ccgmsterm/releases/download/v0.2/ccgms.prg"
CCGMS_SHA256 = "33994e835a0c6f3b7b805b0fe97a921266f2f0576b89ea9622887cc2ae92fde5"
CCGMS_SIZE = 15235
CCGMS_TITLE = "CCGMS Future (terminal)"
_SAFE_DIAL = re.compile(r"^[a-z0-9.-]{1,253}:\d{1,5}$")


class DialError(Exception):
    pass


class C64Dialer:
    def __init__(self, container):  # noqa: ANN001
        self.c = container
        self.busy = False
        self.sleep = asyncio.sleep          # tests make waiting instant
        self.timeout_scale = 1.0

    # ------------------------------------------------------------ the terminal program
    def terminal_game_id(self) -> int | None:
        from app.models.db import Game
        with self.c.sf() as s:
            g = s.scalars(select(Game).where(Game.title.ilike("%ccgms%")).order_by(Game.id)).first()
            return g.id if g else None

    async def install_ccgms(self) -> dict[str, Any]:
        if (gid := self.terminal_game_id()) is not None:
            return {"gameId": gid, "added": False}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
            try:
                r = await http.get(CCGMS_URL)
                r.raise_for_status()
            except httpx.HTTPError as exc:
                raise DialError(f"couldn't download CCGMS ({type(exc).__name__})") from exc
        data = r.content
        if len(data) != CCGMS_SIZE or hashlib.sha256(data).hexdigest() != CCGMS_SHA256:
            raise DialError("the downloaded CCGMS file isn't the expected one — not added")
        gid = await self.c.imports.import_bytes("ccgms.prg", data, title=CCGMS_TITLE, source="download", url=CCGMS_URL)
        return {"gameId": gid, "added": True}

    # ------------------------------------------------------------ screen helpers
    async def _screen(self) -> str | None:
        """The text screen, or None while it can't be read (e.g. the C64 is resetting to start a program)."""
        lines = await self.c.device.inputs.read_text_screen()
        return None if lines is None else "\n".join(lines)

    async def _wait_for(self, patterns: dict[str, str], seconds: float, *, tail: int = 0) -> str:
        """Poll the screen until one of the regexes matches (on the last `tail` lines if given) → its key."""
        deadline = time.monotonic() + seconds * self.timeout_scale
        ever_read = False
        while True:
            text = await self._screen()
            if text is None:                         # mid-reset: keep waiting, unless it never becomes readable
                if time.monotonic() > deadline:
                    if not ever_read:
                        raise DialError("the console can't read the C64's screen (memory reads unavailable)")
                    return "timeout"
                await self.sleep(0.7)
                continue
            ever_read = True
            if tail:
                text = "\n".join([ln for ln in text.splitlines() if ln.strip()][-tail:])
            for key, pat in patterns.items():
                if re.search(pat, text):
                    return key
            if time.monotonic() > deadline:
                return "timeout"
            await self.sleep(0.7)

    async def _key(self, key: str) -> None:
        await self.c.device.inputs.tap_key(key)

    async def _type(self, text: str, enter: bool = True) -> None:
        await self.c.device.inputs.type_text(text + ("\r" if enter else ""))

    async def modem_ok(self) -> str | None:
        """The C64 Ultimate's modem settings, read only. None if fine, else what's wrong."""
        client = self.c.device.client
        try:
            data = await asyncio.wait_for(client.config_category("Modem Settings"), 6)
        except Exception:  # noqa: BLE001 - older firmware: carry on, the AT test will tell
            return None
        items = data.get("Modem Settings") if isinstance(data.get("Modem Settings"), dict) else data
        val = items.get("Modem Interface") if isinstance(items, dict) else None
        if isinstance(val, dict):
            val = val.get("current", val.get("value"))
        if val and "swiftlink" not in str(val).lower() and "acia" not in str(val).lower():
            return (f"the C64 Ultimate's modem is set to \"{val}\" — set Modem Interface to ACIA / SwiftLink in its menu "
                    "(F2 → Modem Settings); the console doesn't change it for you")
        return None

    # ------------------------------------------------------------ dial / hang up
    async def dial(self, host: str, port: int) -> dict[str, Any]:
        target = f"{host}:{port}".lower()
        if not _SAFE_DIAL.match(target):
            raise DialError("this board's address can't be dialed from the C64")
        if self.busy:
            raise DialError("already dialing")
        if not self.c.device.connected or self.c.device.simulator is not None:
            raise DialError("the console isn't connected to a real C64 Ultimate")
        self.busy = True
        steps: list[str] = []
        try:
            if (problem := await self.modem_ok()):
                raise DialError(problem)
            gid = self.terminal_game_id()
            if gid is None:
                raise DialError("CCGMS isn't in your library yet — press \"Add CCGMS\" first")
            job = await self.c.launcher.launch(gid, source="ui", wait=True)
            if job.status == "failed":
                raise DialError(f"CCGMS didn't start: {job.error}")
            if await self._wait_for({"ready": r"IALER"}, 20) != "ready":
                raise DialError("CCGMS didn't show its menu")
            steps.append("CCGMS started")
            await self._key("f7")
            if await self._wait_for({"menu": r"ODEM .YPE"}, 8) != "menu":
                raise DialError("couldn't open CCGMS's Dialer/Params menu")
            for _ in range(8):                        # cycle the modem type to SwiftLink (CCGMS's own setting)
                screen = await self._screen() or ""
                line = next((ln for ln in screen.splitlines() if re.search(r"ODEM .YPE", ln)), "")
                if "WIFT" in line:
                    break
                await self._key("m")
                await self.sleep(1.2)
            else:
                raise DialError("couldn't select SwiftLink in CCGMS")
            await self._key("return")
            await self.sleep(1.5)
            steps.append("modem: SwiftLink / DE00")
            await self._type("at")
            if await self._wait_for({"ok": r"(?m)^OK\s*$"}, 6, tail=2) != "ok":
                raise DialError("the modem didn't answer AT with OK — check the Ultimate's Modem Settings")
            steps.append("AT → OK")
            await self._type(f"atdt {target}")
            res = await self._wait_for({"connect": r"CONNECT", "busy": r"BUSY", "nocarrier": r"NO CARRIER",
                                        "noanswer": r"NO ANSWER", "error": r"(?m)^ERROR"}, 30, tail=4)
            if res != "connect":
                raise DialError({"busy": "the board is busy — try again in a few minutes",
                                 "timeout": "no answer from the board within 30 seconds"}.get(
                                     res, f"the modem said {res.upper().replace('NOCARRIER', 'NO CARRIER')}"))
            steps.append(f"ATDT {target} → CONNECT")
            return {"ok": True, "steps": steps}
        except DialError as exc:
            return {"ok": False, "steps": steps, "error": str(exc)}
        finally:
            self.busy = False
            # taps never leave a key held, but release anything just in case (safety rule)
            try:
                await self.c.device.release_all_inputs("bbs dial finished")
            except Exception:  # noqa: BLE001
                pass

    async def hang_up(self) -> dict[str, Any]:
        await self.sleep(1.2)                         # guard time before and after the escape sequence
        await self._type("+++", enter=False)
        await self.sleep(2.5)
        await self._type("ath")
        res = await self._wait_for({"ok": r"(?m)NO CARRIER|^OK\s*$"}, 6, tail=3)
        return {"ok": res == "ok"}
