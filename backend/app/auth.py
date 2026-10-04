"""Optional password for the console (off until a password is set).

* This computer never needs it: requests that really come from this machine (loopback address, a
  localhost Host header, and no proxy headers) are always allowed — auto-start, OBS, the MCP server.
  Tailscale Serve also connects from 127.0.0.1, but it adds proxy headers, so it counts as remote.
* Everything else must sign in once per device; a signed, HttpOnly cookie keeps it signed in for 30 days.
* The password is stored only as a salted PBKDF2 hash (APP_PASSWORD_HASH). Changing it signs every
  device out, because session cookies are signed with a key derived from the hash.
* Only the data/control API and WebSockets are protected; the app shell (HTML/JS/icons) has no data
  and must load so the sign-in screen can show.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from typing import Any

from app.config import ConfigStore

COOKIE = "c64_session"
SESSION_DAYS = 30
ITERATIONS = 240_000
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
PROXY_HEADERS = (b"x-forwarded-for", b"x-forwarded-host", b"tailscale-user-login", b"forwarded", b"x-real-ip")
PUBLIC_API = ("/api/auth/", "/api/health")
MAX_FAILURES, FAILURE_WINDOW = 5, 300.0

_failures: dict[str, list[float]] = {}
# devices other than this computer that used the console while no password was set: client → last seen (in memory)
_remote_seen: dict[str, float] = {}
REMOTE_WINDOW = 7 * 86400


# ------------------------------------------------------------------ passwords
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iters))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# ------------------------------------------------------------------- sessions
def _server_secret(data_path: Path) -> bytes:
    path = data_path / "auth.key"
    if not path.is_file():
        data_path.mkdir(parents=True, exist_ok=True)
        path.write_bytes(secrets.token_bytes(32))
    return path.read_bytes()


def _key(config: ConfigStore) -> bytes:
    s = config.settings
    return hmac.new(_server_secret(s.data_path), s.APP_PASSWORD_HASH.encode(), hashlib.sha256).digest()


def make_session(config: ConfigStore) -> str:
    # No "=" padding: cookie values containing "=" get quoted by browsers and clients.
    body = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + SESSION_DAYS * 86400}).encode()).decode().rstrip("=")
    sig = hmac.new(_key(config), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def session_valid(config: ConfigStore, token: str | None) -> bool:
    if not token or "." not in token:
        return False
    body, sig = token.rsplit(".", 1)
    good = hmac.new(_key(config), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return False
    try:
        return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))["exp"] > time.time()
    except (ValueError, KeyError):
        return False


# ------------------------------------------------------------------- requests
def _headers(scope: dict[str, Any]) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope.get("headers", [])}


def is_local(scope: dict[str, Any]) -> bool:
    """True only for requests made on this computer itself (not via a proxy such as Tailscale Serve)."""
    client = (scope.get("client") or ("", 0))[0]
    if client not in LOCAL_HOSTS:
        return False
    h = _headers(scope)
    if any(p in h for p in PROXY_HEADERS):
        return False
    host = h.get(b"host", b"").decode("latin-1").lower()
    host = host.split("]")[0].strip("[") if host.startswith("[") else host.rsplit(":", 1)[0]
    return host in LOCAL_HOSTS


def cookie_from(scope: dict[str, Any]) -> str | None:
    raw = _headers(scope).get(b"cookie", b"").decode("latin-1")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value.strip('"')
    return None


def is_https(scope: dict[str, Any]) -> bool:
    h = _headers(scope)
    return scope.get("scheme") in ("https", "wss") or h.get(b"x-forwarded-proto", b"").decode() == "https"


def signed_in(config: ConfigStore, scope: dict[str, Any]) -> bool:
    return not config.settings.APP_PASSWORD_HASH or is_local(scope) or session_valid(config, cookie_from(scope))


def client_id(scope: dict[str, Any]) -> str:
    h = _headers(scope)
    fwd = h.get(b"x-forwarded-for", b"").decode().split(",")[0].strip()
    return fwd or (scope.get("client") or ("?", 0))[0]


def note_remote(scope: dict[str, Any]) -> None:
    if not is_local(scope):
        now = time.time()
        _remote_seen[client_id(scope)] = now
        if len(_remote_seen) > 200:                        # bounded: forget the oldest
            for k, _ in sorted(_remote_seen.items(), key=lambda kv: kv[1])[:50]:
                _remote_seen.pop(k, None)


def remote_devices() -> int:
    cutoff = time.time() - REMOTE_WINDOW
    return sum(1 for t in _remote_seen.values() if t >= cutoff)


def too_many_failures(who: str) -> bool:
    now = time.time()
    recent = [t for t in _failures.get(who, []) if now - t < FAILURE_WINDOW]
    _failures[who] = recent
    return len(recent) >= MAX_FAILURES


def record_failure(who: str) -> None:
    _failures.setdefault(who, []).append(time.time())


def clear_failures(who: str) -> None:
    _failures.pop(who, None)


class AuthGate:
    """ASGI middleware: when a password is set, the API and WebSockets require a session (or this PC)."""

    def __init__(self, app, config: ConfigStore):  # noqa: ANN001
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        if not self.config.settings.APP_PASSWORD_HASH:
            if scope.get("path", "").startswith(("/api/", "/ws")):
                note_remote(scope)                      # for the "set a password" reminder
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        protected = (path.startswith("/api/") and not path.startswith(PUBLIC_API)) or path.startswith("/ws") \
            or path.startswith(("/docs", "/redoc", "/openapi.json"))
        if not protected or signed_in(self.config, scope):
            return await self.app(scope, receive, send)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4401})
            return
        body = b'{"detail":"Sign in to use the console","kind":"auth"}'
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
