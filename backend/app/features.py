"""Hobby features (🛒 shop, 🔧 repair, 🎵 jukebox, 📦 collection, 📚 magazines, 📅 events).

Each feature is self-contained:
* ``app/models/<name>.py``       its tables (on the shared Base)
* ``app/services/<name>.py``     ``attach(container) -> service``; the service may define ``start()`` / ``stop()``
                                 (plain or async) which run with the console
* ``app/api/<name>_api.py``      ``router`` (FastAPI APIRouter)
The service is reachable as ``container.<name>``. A feature whose module is missing is simply skipped.
"""

from __future__ import annotations

import importlib
import inspect
import logging
from types import ModuleType
from typing import Any

log = logging.getLogger("c64.features")

FEATURES = ("shop", "repair", "jukebox", "collection", "magazines", "events")


def _module(path: str) -> ModuleType | None:
    try:
        return importlib.import_module(path)
    except ModuleNotFoundError as exc:
        if exc.name == path:            # the feature isn't there (yet); real import errors still raise
            return None
        raise


def attach_all(container) -> dict[str, Any]:  # noqa: ANN001
    out: dict[str, Any] = {}
    for name in FEATURES:
        mod = _module(f"app.services.{name}")
        if mod is not None and hasattr(mod, "attach"):
            svc = mod.attach(container)
            setattr(container, name, svc)
            out[name] = svc
    return out


async def run_hook(features: dict[str, Any], hook: str) -> None:
    for name, svc in features.items():
        fn = getattr(svc, hook, None)
        if fn is None:
            continue
        try:
            res = fn()
            if inspect.isawaitable(res):
                await res
        except Exception as exc:  # noqa: BLE001 - one feature must not stop the console
            log.warning("%s.%s failed: %s", name, hook, exc)


def routers() -> list:
    out = []
    for name in FEATURES:
        mod = _module(f"app.api.{name}_api")
        if mod is not None and hasattr(mod, "router"):
            out.append(mod.router)
    return out
