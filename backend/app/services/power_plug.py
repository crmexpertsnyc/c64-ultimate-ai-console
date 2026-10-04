"""⏻ Power on/off through a smart plug — the C64 Ultimate can't be woken over the network once it's off.

Leave the C64 Ultimate's own power switch ON and plug it into a smart plug with a local HTTP API; the console
then switches the plug. Supported (local network, no cloud account):
* Shelly Gen1 (Plug S, 1PM…):   http://<host>/relay/0?turn=on|off
* Shelly Gen2/Plus (Plus Plug): http://<host>/rpc/Switch.Set?id=0&on=true|false
* Tasmota:                      http://<host>/cm?cmnd=Power%20On|Off
Only the settings' host is ever contacted (a bare hostname or IP), and only when you press a button.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

TYPES = {"shelly": "Shelly (Gen1)", "shelly-gen2": "Shelly Plus / Gen2", "tasmota": "Tasmota"}
_HOST = re.compile(r"^[A-Za-z0-9.-]{1,253}(:\d{1,5})?$")


class PlugError(Exception):
    pass


class PowerPlug:
    def __init__(self, settings_provider, http=None):  # noqa: ANN001
        self._settings = settings_provider
        self._http = http                                  # tests inject a client with a mock transport

    @property
    def kind(self) -> str:
        return (self._settings().POWER_PLUG_TYPE or "").strip().lower()

    @property
    def host(self) -> str:
        return (self._settings().POWER_PLUG_HOST or "").strip()

    @property
    def configured(self) -> bool:
        return self.kind in TYPES and bool(_HOST.match(self.host))

    def _url(self, on: bool | None) -> str:
        base = f"http://{self.host}"
        if self.kind == "shelly":
            return f"{base}/relay/0" + ("" if on is None else f"?turn={'on' if on else 'off'}")
        if self.kind == "shelly-gen2":
            if on is None:
                return f"{base}/rpc/Switch.GetStatus?id=0"
            return f"{base}/rpc/Switch.Set?id=0&on={'true' if on else 'false'}"
        return f"{base}/cm?cmnd=Power" + ("" if on is None else f"%20{'On' if on else 'Off'}")

    async def _get(self, url: str) -> dict[str, Any]:
        if not self.configured:
            raise PlugError("no smart plug set up (Settings → C64 Ultimate → Smart plug)")
        client = self._http or httpx.AsyncClient(timeout=6)
        try:
            r = await client.get(url)
            r.raise_for_status()
            return r.json() if r.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            raise PlugError(f"the smart plug at {self.host} didn't answer ({type(exc).__name__})") from exc
        finally:
            if self._http is None:
                await client.aclose()

    async def state(self) -> bool | None:
        """True / False = the plug's relay; None if it can't be read."""
        try:
            d = await self._get(self._url(None))
        except PlugError:
            return None
        if self.kind == "shelly":
            return bool(d.get("ison"))
        if self.kind == "shelly-gen2":
            return bool(d.get("output"))
        return str(d.get("POWER", "")).upper() == "ON"

    async def switch(self, on: bool) -> None:
        await self._get(self._url(on))

    async def power_cycle_on(self, off_seconds: float = 3.0) -> None:
        """Off, a pause, on: the C64 Ultimate starts when power comes back (its own switch left ON)."""
        if await self.state():
            await self.switch(False)
            await asyncio.sleep(off_seconds)
        await self.switch(True)
