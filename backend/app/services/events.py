"""In-process pub/sub used to push live updates to WebSocket clients."""

from __future__ import annotations

import asyncio
import time
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def publish(self, type_: str, data: Any) -> None:
        msg = {"type": type_, "ts": time.time(), "data": data}
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Slow client: drop the oldest message rather than blocking the device loop.
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# ---------------------------------------------------------------------------------------------------------------
# 📅 Retro events feature (app/features.py loads ``attach``). The service lives in retro_events.py, the 🧩 firmware
# notice in firmware_watch.py; both are imported here lazily so this module stays a light dependency of the hub.
def attach(container):  # noqa: ANN001, ANN201
    from .firmware_watch import FirmwareWatch
    from .geo import normalize_country
    from .retro_events import EventService

    def enabled() -> bool:                      # background checks follow the news monitor switch (off in tests)
        return bool(getattr(container.settings, "NEWS_MONITOR", False))

    def device_info() -> tuple[str, str] | None:
        info = getattr(container.device, "info", None)
        if info is None or not getattr(info, "product", ""):
            return None
        return info.product, info.firmware_version

    svc = EventService(container.ask, container.sf, container.hub, enabled=enabled)
    svc.home = lambda: normalize_country(getattr(container.settings, "EVENTS_HOME_COUNTRY", "") or "United States") or ""
    svc.firmware = FirmwareWatch(container.sf, container.hub, device_info=device_info, enabled=enabled)
    container.firmware = svc.firmware
    return svc
