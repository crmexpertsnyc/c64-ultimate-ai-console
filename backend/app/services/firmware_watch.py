"""🧩 Firmware news — notification only.

Once a day the official download page for the user's device is read and its newest firmware version compared with
the version the device reports (``container.device.info``):

* "C64 Ultimate" (Commodore's FPGA C64)  → https://commodore.net/downloads  (``c64u_vX.Y.Z.zip`` + changelog)
* "Ultimate 64" / "Ultimate-II(+)"       → https://ultimate64.com/Firmware  ("Firmware version 3.15a - …")

When a newer version appears, ONE news item is added (it shows in 📰 News and the 🆕 badge) linking to the download
*page*. Nothing here ever downloads a firmware file or sends anything to the device, and there is no "update" button:
updating is a manual step that follows the maker's instructions. Only the download page and the plain-text changelog
are read.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app.models.db import NewsItem

from .news import NewsService, _plain
from .retro_events import load_state, save_state

log = logging.getLogger("c64.firmware")

CHECK_EVERY = 24 * 3600
MANUAL_NOTE = "Updating is a manual step — follow Commodore's instructions."

SOURCES: dict[str, dict[str, str]] = {
    "commodore": {"page": "https://commodore.net/downloads", "label": "Commodore", "key": "c64u",
                  "product": "C64 Ultimate"},
    "ultimate64": {"page": "https://ultimate64.com/Firmware", "label": "Gideon's Logic (ultimate64.com)", "key": "u64",
                   "product": "Ultimate 64"},
}
# The changelog is only read from the maker's own sites (plain text, never a firmware file).
CHANGELOG_HOSTS = ("commodore.net", "commodore-international.com")


# ------------------------------------------------------------------ versions
def version_key(v: str | None) -> tuple[int, ...]:
    """"1.1.0s2" → (1, 1, 0, 0, 0): build suffixes like "s2" are dropped; "3.15a" → (3, 15, 0, 0, 1) — a single
    trailing letter is Gideon's revision (3.15a is newer than 3.15). Missing parts count as 0 (1.1 == 1.1.0)."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)(.*)$", v or "", re.I)
    if not m:
        return ()
    nums = [int(x) for x in m.group(1).split(".")][:4]
    nums += [0] * (4 - len(nums))
    rest = m.group(2).strip().lower()
    letter = ord(rest) - 96 if re.fullmatch(r"[a-z]", rest) else 0
    return (*nums, letter)


def is_newer(latest: str | None, installed: str | None) -> bool:
    """True when ``latest`` is a higher version than ``installed`` (equal = current; unknown = not newer)."""
    a, b = version_key(latest), version_key(installed)
    return bool(a and b and a > b)


def source_for(product: str | None) -> str:
    p = (product or "").lower()
    if "c64 ultimate" in p or p.strip() in ("c64u", "commodore 64 ultimate"):
        return "commodore"
    if re.search(r"ultimate[\s-]?64|ultimate[\s-]?ii|\bu64|\bu2", p):
        return "ultimate64"
    return "commodore"                      # the console is made for Commodore's C64 Ultimate


# ------------------------------------------------------------------ parsers
def parse_commodore(page: str) -> dict[str, Any] | None:
    """commodore.net/downloads → {version, changelogUrl} for the highest c64u_vX.Y.Z firmware listed."""
    found = re.findall(r'href="(https://[^"\s]+/c64u_v(\d+(?:\.\d+)+)\.zip)"', page, re.I)
    if not found:
        return None
    version = max((v for _, v in found), key=version_key)
    changelog = None
    for url in re.findall(r'href="(https://[^"\s]+/changelog-([\d.]+)\.txt)"', page, re.I):
        host = (urlparse(url[0]).hostname or "").lower()
        if url[1] == version and any(host == h or host.endswith("." + h) for h in CHANGELOG_HOSTS):
            changelog = url[0]
            break
    return {"version": version, "changelogUrl": changelog}


def parse_ultimate64(page: str) -> dict[str, Any] | None:
    """ultimate64.com/Firmware → {version, platforms, dated} of the newest "Firmware version X.YZa" listed."""
    text = _plain(page, 200_000)
    rows = re.findall(r"Firmware version\s+(\d+\.\d+[a-z]?)\b(.{0,160}?)(?:-\s*)?Dated:?\s*(\d{4}-\d{2}-\d{2})", text, re.I)
    if not rows:
        return None
    version, info, dated = max(rows, key=lambda r: version_key(r[0]))
    info = re.sub(r"^\s*[-/]\s*", "", info).strip(" -")
    return {"version": version, "platforms": info or None, "dated": dated, "changelogUrl": None}


def changelog_summary(text: str, limit: int = 400) -> str:
    """The first meaningful lines of a changelog (headings and ---- underlines skipped)."""
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip().strip("*").strip()
        if not line or re.fullmatch(r"[-=_~*]{3,}", line):
            continue
        lines.append(line)
        if sum(len(x) for x in lines) > limit:
            break
    out = " ".join(lines)
    return out if len(out) <= limit else out[:limit - 1].rsplit(" ", 1)[0] + "…"


# ------------------------------------------------------------------ service
class FirmwareWatch:
    def __init__(self, sf, hub=None, device_info: Callable[[], tuple[str, str] | None] | None = None,  # noqa: ANN001
                 fetch: Callable[[str], Awaitable[str]] | None = None, enabled: Callable[[], bool] | None = None):
        self.sf, self.hub = sf, hub
        self.device_info = device_info or (lambda: None)
        self._fetch = fetch or NewsService._http_get
        self._enabled = enabled or (lambda: True)
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------ loop
    def start(self) -> None:
        if not self._enabled():
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="firmware-watch")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        await asyncio.sleep(60)                     # the device connects first
        while True:
            try:
                last = load_state(self.sf, "firmware").get("checkedAt")
                age = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() if last else None
                if age is None or age >= CHECK_EVERY:
                    await self.check()
            except Exception as exc:  # noqa: BLE001 - try again later
                log.info("firmware check failed: %s", exc)
            await asyncio.sleep(3600)

    # ------------------------------------------------------------ device
    def _device(self) -> tuple[str | None, str | None]:
        """(product, installed version) from the device; the last known values when it is offline."""
        try:
            info = self.device_info()
        except Exception:  # noqa: BLE001
            info = None
        if info and info[0] and info[1]:
            return info[0], info[1]
        st = load_state(self.sf, "firmware")
        return st.get("product"), st.get("installed")

    def status(self) -> dict[str, Any]:
        """{product, installed, latest, newer, downloadPage, changelogUrl, checkedAt, source, note}."""
        st = load_state(self.sf, "firmware")
        product, installed = self._device()
        src = source_for(product)
        same = st.get("source") == src
        latest = st.get("latest") if same else None
        return {"product": product or SOURCES[src]["product"], "installed": installed, "latest": latest,
                "newer": is_newer(latest, installed), "downloadPage": SOURCES[src]["page"],
                "changelogUrl": st.get("changelogUrl") if same else None,
                "checkedAt": st.get("checkedAt") if same else None, "source": src,
                "sourceLabel": SOURCES[src]["label"], "error": st.get("error") if same else None, "note": MANUAL_NOTE}

    # ------------------------------------------------------------ check
    async def check(self) -> dict[str, Any]:
        """Read the download page now; add the one news item if a newer version is out. Never downloads firmware."""
        async with self._lock:
            product, installed = self._device()
            src = source_for(product)
            meta = SOURCES[src]
            state: dict[str, Any] = {"product": product, "installed": installed, "source": src,
                                     "checkedAt": datetime.now(UTC).isoformat(), "error": None}
            try:
                page = await self._fetch(meta["page"])
                found = parse_commodore(page) if src == "commodore" else parse_ultimate64(page)
                if not found:
                    raise ValueError("no firmware version found on the download page")
            except Exception as exc:  # noqa: BLE001
                prev = load_state(self.sf, "firmware")
                keys = ("latest", "changelogUrl", "platforms", "dated")
                keep = {k: prev.get(k) for k in keys} if prev.get("source") == src else {}
                save_state(self.sf, "firmware", {**keep, **state, "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
                log.info("firmware check (%s) failed: %s", src, exc)
                return self.status()
            state.update(latest=found["version"], changelogUrl=found.get("changelogUrl"),
                         platforms=found.get("platforms"), dated=found.get("dated"))
            save_state(self.sf, "firmware", state)
            if is_newer(found["version"], installed):
                await self._announce(src, product or meta["product"], found)
            return self.status()

    async def _announce(self, src: str, product: str, found: dict[str, Any]) -> bool:
        meta = SOURCES[src]
        version = found["version"]
        guid = f"firmware:{meta['key']}:{version}"
        with self.sf() as s:
            if s.scalars(select(NewsItem.id).where(NewsItem.guid == guid)).first() is not None:
                return False                                     # announced once, never again
        summary = ""
        if found.get("changelogUrl"):
            try:
                summary = changelog_summary(await self._fetch(found["changelogUrl"]))
            except Exception as exc:  # noqa: BLE001 - the notice goes out without it
                log.info("firmware changelog unavailable: %s", exc)
        if not summary and found.get("platforms"):
            summary = f"Firmware {version} — {found['platforms']}" + (f" (dated {found['dated']})" if found.get("dated") else "")
        summary = (summary + " " if summary else "") + MANUAL_NOTE
        with self.sf() as s:
            if s.scalars(select(NewsItem.id).where(NewsItem.guid == guid)).first() is not None:
                return False
            s.add(NewsItem(guid=guid, source="firmware", kind="news", title=f"{product} firmware {version} is out"[:300],
                           url=meta["page"], summary=summary[:1000], author=meta["label"],
                           published_at=datetime.now(UTC)))
            s.commit()
        log.info("firmware %s %s announced", meta["key"], version)
        if self.hub:
            self.hub.publish("news", {"added": 1, "titles": [f"{product} firmware {version} is out"]})
            self.hub.publish("firmware", self.status())
        return True
