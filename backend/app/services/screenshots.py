"""Screenshots and cover art.

* Screenshots are made from the raw C64 frame (VIC colour indices), not from the JPEG preview,
  so the saved PNG is pixel-perfect. They live in ``DATA_DIR/screenshots`` with a small JSON
  sidecar (title, game id, time).
* Automatic cover art: a few seconds after a game starts, if it has no cover yet, one frame is
  captured and used as its cover.
* CSDB art: for catalog entries that come from CSDB, the release screenshot from CSDB's public
  web service is fetched once and cached in ``DATA_DIR/art``.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from app.ultimate.streams import _FLAT_PALETTE, WIDTH, render_text_frame

log = logging.getLogger("c64.screenshots")

NAME_RE = re.compile(r"^[0-9]{8}-[0-9]{6}(?:-[0-9]+)?-[a-z0-9-]{0,48}\.png$")
CSDB_CATEGORIES = set(range(0, 11))  # Assembly64 subcategories that mirror CSDB releases
ART_MAX_BYTES = 4 * 1024 * 1024


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48]


def frame_to_png(frame: bytes, height: int, scale: int = 2) -> bytes:
    img = Image.frombytes("P", (WIDTH, height), frame)
    img.putpalette(_FLAT_PALETTE)
    img = img.convert("RGB")
    if scale != 1:
        img = img.resize((WIDTH * scale, height * scale), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# C64 "BASIC screen" colours (blue background, light-blue text/border) in the Pepto palette and
# CSDB's typical renderings; a picture made mostly of these is a READY./LOADING/directory screen.
_BASIC_BLUES = [(0x35, 0x28, 0x79), (0x6C, 0x5E, 0xB5), (0x40, 0x31, 0x8D), (0x70, 0x6D, 0xEB),
                (0x50, 0x45, 0x9B), (0x88, 0x7E, 0xCB), (0x3E, 0x31, 0xA2), (0x7C, 0x70, 0xDA)]


def _near(c: tuple[int, int, int], ref: tuple[int, int, int], tol: int = 40) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(c, ref, strict=False))


def image_quality(img: Image.Image) -> dict[str, Any]:
    """How good a picture is as cover art: rejects blank, loading/BASIC text screens and near-
    monochrome frames; otherwise scores by colour variety and how much of the screen has detail."""
    small = img.convert("RGB").resize((96, 68), Image.NEAREST)
    # Ignore the border: look at the central (bitmap) area only.
    inner = small.crop((8, 9, 88, 59))
    quant = inner.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    counts = sorted(quant.getcolors(4000) or [], reverse=True)
    total = sum(c for c, _ in counts) or 1
    palette = quant.getpalette() or []
    colours = [(c, tuple(palette[i * 3:i * 3 + 3])) for c, i in counts if c / total >= 0.004]
    distinct = len(colours)
    dominant = colours[0][0] / total if colours else 1.0
    blue_share = sum(c for c, rgb in colours if any(_near(rgb, b) for b in _BASIC_BLUES)) / total
    if distinct <= 2 or dominant > 0.93:
        return {"ok": False, "score": 0.0, "reason": "blank or almost one colour"}
    if blue_share > 0.88 and distinct <= 4:
        return {"ok": False, "score": 0.0, "reason": "BASIC / loading screen"}
    score = distinct * 10 + (1 - dominant) * 100 - blue_share * 30
    return {"ok": distinct >= 3, "score": round(score, 1), "reason": ""}


def png_quality(png: bytes) -> dict[str, Any]:
    return image_quality(Image.open(io.BytesIO(png)))


class ScreenshotService:
    def __init__(self, data_dir_provider, device):  # noqa: ANN001
        self._data_dir = data_dir_provider
        self.device = device

    @property
    def folder(self) -> Path:
        path = self._data_dir() / "screenshots"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def art_folder(self) -> Path:
        path = self._data_dir() / "art"
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------ capture
    async def grab_png(self, timeout: float = 4.0) -> bytes:
        """PNG of the current C64 screen. Joins the video stream briefly if nobody is watching."""
        dev = self.device
        if dev.simulator is not None:
            return await asyncio.to_thread(render_text_frame, dev.simulator.lines, "SIMULATED", "PNG")
        video = dev.streams.video
        await dev.join_stream("video")
        try:
            start_frames = video.frames
            deadline = time.monotonic() + timeout
            # Wait for a frame that arrived after we started watching (fresh, not stale).
            while (video.frame is None or video.frames == start_frames) and time.monotonic() < deadline:
                try:
                    await asyncio.wait_for(video.new_frame.wait(), timeout=max(0.05, deadline - time.monotonic()))
                except TimeoutError:
                    break
            if video.frame is None:
                raise RuntimeError("no picture from the C64 (is the video stream supported and reachable?)")
            return await asyncio.to_thread(frame_to_png, video.frame, video.height)
        finally:
            await dev.leave_stream("video")

    async def capture(self, title: str = "", game_id: int | None = None, auto: bool = False) -> dict[str, Any]:
        return self.save(await self.grab_png(), title, game_id, auto)

    async def best_frame(self, window: float = 50.0, interval: float = 3.0,
                         still_wanted=None) -> tuple[bytes, dict[str, Any]] | None:  # noqa: ANN001
        """Sample the screen for ``window`` seconds and return the best-looking frame as cover art
        (skipping loading/BASIC/blank screens). ``still_wanted()`` can abort early (e.g. another
        game was launched)."""
        dev = self.device
        best: tuple[bytes, dict[str, Any]] | None = None
        simulated = dev.simulator is not None
        if not simulated:
            await dev.join_stream("video")
        try:
            deadline = time.monotonic() + window
            while time.monotonic() < deadline:
                await asyncio.sleep(interval)
                if still_wanted is not None and not still_wanted():
                    return None
                if simulated:
                    png = await asyncio.to_thread(render_text_frame, dev.simulator.lines, "SIMULATED", "PNG")
                else:
                    video = dev.streams.video
                    if video.frame is None:
                        continue
                    png = await asyncio.to_thread(frame_to_png, video.frame, video.height)
                q = await asyncio.to_thread(png_quality, png)
                if q["ok"] and (best is None or q["score"] > best[1]["score"]):
                    best = (png, q)
        finally:
            if not simulated:
                await dev.leave_stream("video")
        return best

    def save(self, png: bytes, title: str = "", game_id: int | None = None, auto: bool = False,
             score: float | None = None) -> dict[str, Any]:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{stamp}-{slug(title) or 'c64'}.png"
        n = 1
        while (self.folder / name).exists():
            n += 1
            name = f"{stamp}-{n}-{slug(title) or 'c64'}.png"
        (self.folder / name).write_bytes(png)
        if score is None:
            score = png_quality(png)["score"]
        meta = {"title": title or None, "gameId": game_id, "takenAt": time.time(), "auto": auto, "score": score}
        (self.folder / (name + ".json")).write_text(json.dumps(meta), encoding="utf-8")
        return self._describe(name, meta, len(png))

    # ------------------------------------------------------------- gallery
    def _describe(self, name: str, meta: dict[str, Any], size: int) -> dict[str, Any]:
        return {"name": name, "url": f"/api/screenshots/{name}", "size": size, **meta}

    def list(self, include_auto: bool = True) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.folder.glob("*.png"), reverse=True):
            try:
                meta = json.loads((p.parent / (p.name + ".json")).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {"title": None, "gameId": None, "takenAt": p.stat().st_mtime, "auto": False}
            if meta.get("auto") and not include_auto:
                continue
            out.append(self._describe(p.name, meta, p.stat().st_size))
        return out

    def path(self, name: str) -> Path:
        if not NAME_RE.match(name):
            raise ValueError("invalid screenshot name")
        p = self.folder / name
        if not p.is_file():
            raise FileNotFoundError(name)
        return p

    def delete(self, name: str) -> None:
        p = self.path(name)
        p.unlink()
        side = p.parent / (p.name + ".json")
        if side.exists():
            side.unlink()

    def cover_quality(self, cover_url: str | None) -> float | None:
        """Score of an existing cover (screenshot or cached CSDB art); None if unknown/missing."""
        if not cover_url:
            return None
        try:
            if cover_url.startswith("/api/screenshots/"):
                p = self.path(cover_url.rsplit("/", 1)[-1])
                try:
                    meta = json.loads((p.parent / (p.name + ".json")).read_text(encoding="utf-8"))
                    if meta.get("score") is not None:
                        return float(meta["score"])
                except (OSError, ValueError):
                    pass
                q = png_quality(p.read_bytes())
                return q["score"] if q["ok"] else 0.0
            if cover_url.startswith("/api/art/csdb/"):
                rid = cover_url.rsplit("/", 1)[-1]
                for ext in ("png", "gif", "jpg"):
                    f = self.art_folder / f"csdb-{rid}.{ext}"
                    if f.exists():
                        q = image_quality(Image.open(f))
                        return q["score"] if q["ok"] else 0.0
        except (OSError, ValueError, FileNotFoundError):
            return None
        return None

    # ----------------------------------------------------------- CSDB art
    async def csdb_art(self, release_id: str) -> Path | None:
        """Cached CSDB release screenshot for a CSDB-sourced catalog entry."""
        if not release_id.isdigit():
            return None
        for ext in ("png", "gif", "jpg"):
            cached = self.art_folder / f"csdb-{release_id}.{ext}"
            if cached.exists():
                return cached
        missing = self.art_folder / f"csdb-{release_id}.none"
        if missing.exists() and time.time() - missing.stat().st_mtime < 7 * 86400:
            return None
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                r = await c.get("https://csdb.dk/webservice/", params={"type": "release", "depth": 1, "id": release_id})
                m = re.search(r"<ScreenShot>\s*(https://csdb\.dk/[^<\s]+)\s*</ScreenShot>", r.text)
                if not m:
                    missing.touch()
                    return None
                img = await c.get(m.group(1))
        except httpx.HTTPError as exc:
            log.info("CSDB art lookup failed for %s: %s", release_id, exc)
            return None
        ext = m.group(1).rsplit(".", 1)[-1].lower()
        if img.status_code != 200 or ext not in ("png", "gif", "jpg", "jpeg") or len(img.content) > ART_MAX_BYTES:
            missing.touch()
            return None
        target = self.art_folder / f"csdb-{release_id}.{'jpg' if ext == 'jpeg' else ext}"
        target.write_bytes(img.content)
        return target
