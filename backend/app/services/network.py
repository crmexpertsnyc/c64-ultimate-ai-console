"""Addresses other devices (phones, tablets, other computers) can use to open the console."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import shutil
import socket
import time
from typing import Any

_TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
_ts_cache: tuple[float, str | None] = (0.0, None)


def _route_ip(target: str) -> str | None:
    """Local IP of the adapter that routes to ``target`` (no packet is sent for a UDP connect)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target, 9))
            ip = s.getsockname()[0]
        return None if ip.startswith(("127.", "0.")) else ip
    except OSError:
        return None


def _all_ipv4() -> list[str]:
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    return ips


async def _tailscale_name() -> str | None:
    """MagicDNS short name of this machine (cached), or None when Tailscale is not installed/running."""
    global _ts_cache
    stamp, name = _ts_cache
    if time.monotonic() - stamp < 300:
        return name
    name = None
    exe = shutil.which("tailscale")
    if exe:
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "status", "--json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 3)
            dns = json.loads(out or b"{}").get("Self", {}).get("DNSName", "")
            name = dns.split(".")[0] or None
        except (TimeoutError, OSError, ValueError):
            name = None
    _ts_cache = (time.monotonic(), name)
    return name


_serve_cache: tuple[float, str | None] = (0.0, None)


async def _tailscale_https(port: int) -> str | None:
    """https://<machine>.<tailnet>.ts.net when `tailscale serve` publishes this console (cached)."""
    global _serve_cache
    stamp, url = _serve_cache
    if time.monotonic() - stamp < 120:
        return url
    url = None
    exe = shutil.which("tailscale")
    if exe:
        try:
            proc = await asyncio.create_subprocess_exec(
                exe, "serve", "status", "--json", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(proc.communicate(), 3)
            web = (json.loads(out or b"{}") or {}).get("Web") or {}
            for hostport, cfg in web.items():
                proxies = [h.get("Proxy", "") for h in (cfg.get("Handlers") or {}).values()]
                if any(p.rstrip("/").endswith(f":{port}") for p in proxies):
                    host, _, sport = hostport.rpartition(":")
                    url = f"https://{host}" + ("" if sport == "443" else f":{sport}")
                    break
        except (TimeoutError, OSError, ValueError):
            url = None
    _serve_cache = (time.monotonic(), url)
    return url


async def access_urls(port: int, device_host: str = "", public_url: str = "") -> list[dict[str, Any]]:
    """Ordered list of {url, kind, label, primary}; the first entry is the one to show in a QR code.

    kind: "custom" (PUBLIC_URL), "secure" (Tailscale HTTPS), "lan" (home network), "tailscale", "hostname",
    "other" (virtual adapters).
    """
    urls: list[dict[str, Any]] = []

    def add(url: str, kind: str, label: str) -> None:
        if all(u["url"] != url for u in urls):
            urls.append({"url": url, "kind": kind, "label": label, "primary": False})

    if public_url:
        add(public_url.rstrip("/"), "custom", "Configured address")
    # Secure address first: works at home and away, and unlocks mic, gamepads and "install as app".
    secure = await _tailscale_https(port)
    if secure:
        add(secure, "secure", "Anywhere · secure (phone needs the Tailscale app)")
    # The adapter that reaches the C64 is by definition on the home network phones use.
    home = None
    if device_host:
        try:
            home = _route_ip(socket.gethostbyname(device_host))
        except OSError:
            home = None
    home = home or _route_ip("192.0.2.1")  # default route (TEST-NET address, never contacted)
    if home:
        add(f"http://{home}:{port}", "lan", "Home Wi-Fi only · no app needed")
    tailscale = []
    for ip in _all_ipv4():
        if ipaddress.ip_address(ip) in _TAILSCALE_NET:
            tailscale.append(ip)
        elif ip != home:
            private = ipaddress.ip_address(ip).is_private
            add(f"http://{ip}:{port}", "other", "Other network adapter" if private else "Public address")
    for ip in tailscale:
        add(f"http://{ip}:{port}", "tailscale", "Tailscale (away from home)")
    ts_name = await _tailscale_name()
    if ts_name:
        add(f"http://{ts_name}:{port}", "tailscale", "Tailscale name")
    host = socket.gethostname().lower()
    if "_" not in host:  # underscores are not valid in URLs for most browsers
        add(f"http://{host}:{port}", "hostname", "Computer name (may not work on iPhone/Android)")
    # "other" adapters (Hyper-V, WSL, Docker) are rarely useful: keep them last.
    urls.sort(key=lambda u: u["kind"] == "other")
    if urls:
        urls[0]["primary"] = True
    return urls
