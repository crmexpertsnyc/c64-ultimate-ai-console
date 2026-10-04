"""Application configuration.

Values come from (lowest to highest precedence):
  1. defaults below
  2. environment / .env file
  3. ``<DATA_DIR>/settings.json`` written by the setup wizard / settings UI

Secrets (the Ultimate network password and the AI API key) are never included in
``public_dict()`` and are redacted from logs by ``app.logging_setup``.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRET_FIELDS = {"C64_ULTIMATE_PASSWORD", "AI_API_KEY", "LIVE_STREAM_KEY", "MCP_TOKEN", "APP_PASSWORD_HASH", "BRAVE_API_KEY",
                 "EBAY_CLIENT_SECRET"}
# Managed only by the /api/auth endpoints, never by the generic settings API.
AUTH_FIELDS = {"APP_PASSWORD_HASH", "CLEAR_APP_PASSWORD_HASH"}

# Fields the settings UI / setup wizard may change at runtime.
EDITABLE_FIELDS = {
    "C64_ULTIMATE_HOST",
    "C64_ULTIMATE_PORT",
    "C64_ULTIMATE_PASSWORD",
    "C64_ULTIMATE_PROTOCOL",
    "SIMULATE_C64",
    "SIMULATE_PROFILE",
    "AI_PROVIDER",
    "AI_BASE_URL",
    "AI_MODEL",
    "AI_API_KEY",
    "AI_MAX_TOKENS",
    "AI_REASONING_EFFORT",
    "LIBRARY_PATHS",
    "C64_TYPE_DELAY_MS",
    "C64_UPLOAD_MODE",
    "STREAM_TARGET_HOST",
    "SETUP_COMPLETE",
    "ASSEMBLY64_URL",
    "ASSEMBLY64_CLIENT_ID",
    "LIVE_RTMP_URL",
    "LIVE_STREAM_KEY",
    "LIVE_VIDEO_BITRATE_KBPS",
    "AUTO_COVER_ART",
    "NEWS_MONITOR",
    "SHOP_CATALOG_URL",
    "EVENTS_HOME_COUNTRY",
    "EBAY_CLIENT_ID",
    "EBAY_CLIENT_SECRET",
    "EBAY_CAMPAIGN_ID",
    "EBAY_MARKETPLACE",
    "MCP_ENABLED",
    "MCP_TOKEN",
    "MCP_ALLOWED_HOSTS",
    "JOYBRIDGE_HOST",
    "BRAVE_API_KEY",
    "ASK_WEB_SEARCH",
    "APP_PASSWORD_HASH",
    "JOYBRIDGE_PORT",
    "PUBLIC_URL",
}


class Settings(BaseSettings):
    # Project-root .env (docker compose / README) and backend/.env both work; the later wins.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    # --- Device -----------------------------------------------------------
    C64_ULTIMATE_HOST: str = ""
    C64_ULTIMATE_PORT: int = 80
    C64_ULTIMATE_PASSWORD: str = ""
    C64_ULTIMATE_PROTOCOL: Literal["http", "https"] = "http"
    C64_REQUEST_TIMEOUT: float = 8.0
    # Delay between individual key taps when typing through REST input (ms).
    C64_TYPE_DELAY_MS: int = 60
    # How binary attachments (PRG/CRT/D64 uploads) are sent: raw body or multipart form.
    C64_UPLOAD_MODE: Literal["raw", "multipart"] = "raw"

    # --- Simulation -------------------------------------------------------
    SIMULATE_C64: bool = False
    # "modern" = firmware with machine:input + menu_screen, "legacy" = Commodore 1.1.0 / Spiffy style.
    SIMULATE_PROFILE: Literal["modern", "legacy"] = "modern"

    # --- App --------------------------------------------------------------
    DATA_DIR: str = "./data"
    DATABASE_URL: str = ""
    WEB_HOST: str = "0.0.0.0"
    WEB_PORT: int = 8064
    # Address shown to phones/tablets (QR code) when auto-detection is wrong, e.g. in Docker:
    # PUBLIC_URL=http://192.168.1.226:8064
    PUBLIC_URL: str = ""
    # Optional console password (salted PBKDF2 hash; set from Settings → Password). Empty = no sign-in.
    APP_PASSWORD_HASH: str = ""

    # --- Ask mode: questions answered by the AI, optionally grounded in a Brave web search ---
    BRAVE_API_KEY: str = ""          # https://brave.com/search/api/ — empty = answer from the model's own knowledge
    ASK_WEB_SEARCH: bool = True       # use Brave when a key is set

    # --- 🛒 Hardware shop (link-out only) and eBay listings ---
    EVENTS_HOME_COUNTRY: str = "United States"   # 📅 events here are highlighted (and can be listed first)
    SHOP_CATALOG_URL: str = ""        # https URL of a catalog JSON published by your website; empty = built-in catalog
    EBAY_CLIENT_ID: str = ""          # eBay developer app keys (Browse API) — empty = plain eBay search links
    EBAY_CLIENT_SECRET: str = ""
    EBAY_CAMPAIGN_ID: str = ""        # eBay Partner Network campaign id (affiliate links); empty = no affiliate tag
    EBAY_MARKETPLACE: str = "EBAY_US"
    ASK_MAX_TOKENS: int = 2500

    # --- Joystick bridge (ESP32 wired into the joystick port; docs/joystick-bridge.md) ---
    JOYBRIDGE_HOST: str = ""   # IP or name of the bridge; empty = not used
    JOYBRIDGE_PORT: int = 6464
    CORS_ORIGINS: str = "http://localhost:5173"
    LOG_LEVEL: str = "INFO"
    LIBRARY_PATHS: str = ""
    FRONTEND_DIST: str = ""
    SETUP_COMPLETE: bool = False
    # Required for POST /api/device/power-off. Even then the request must carry confirm=true.
    ALLOW_POWER_OFF: bool = True

    # --- AI ---------------------------------------------------------------
    AI_PROVIDER: Literal["none", "openai", "openwebui", "vllm", "ollama", "anthropic"] = "none"
    AI_BASE_URL: str = ""
    AI_MODEL: str = ""
    AI_API_KEY: str = ""
    AI_TIMEOUT: float = 60.0
    RECOMMEND_TIMEOUT: float = 180.0  # recommendations: a longer answer than a command
    # netplay: public-address discovery for guests away from home ('' = home network / Tailscale only)
    NETPLAY_STUN: str = "stun:stun.l.google.com:19302"
    # Reasoning models (gpt-oss, o-series, …) spend tokens thinking before answering.
    AI_MAX_TOKENS: int = 1500
    # Optional "reasoning_effort" for OpenAI-compatible servers (low | medium | high); blank = not sent.
    AI_REASONING_EFFORT: str = "low"

    # --- Streams ----------------------------------------------------------
    # Address the Ultimate should send UDP stream packets to (this machine). Auto-detected if blank.
    STREAM_TARGET_HOST: str = ""
    STREAM_VIDEO_PORT: int = 11000
    STREAM_AUDIO_PORT: int = 11001

    # --- Vision (experimental, off by default) -----------------------------
    VISION_ENABLED: bool = False
    VISION_MAX_ACTIONS_PER_SECOND: float = 2.0
    VISION_MAX_SESSION_SECONDS: int = 120
    VISION_MAX_ACTIONS_PER_SESSION: int = 200

    # --- Assembly64 online catalog -----------------------------------------
    ASSEMBLY64_URL: str = "https://hackerswithstyle.se"
    # The public service only accepts registered client ids (HTTP 464 otherwise). Spiffy
    # firmware sends "Spiffy"; set this to the id you are entitled to use.
    ASSEMBLY64_CLIENT_ID: str = ""

    # --- Live streaming / recording / screenshots --------------------------
    FFMPEG_PATH: str = ""  # blank = ffmpeg on PATH, else the bundled imageio-ffmpeg binary
    LIVE_RTMP_URL: str = ""  # e.g. rtmp://live.twitch.tv/app or rtmp://a.rtmp.youtube.com/live2
    LIVE_STREAM_KEY: str = ""
    LIVE_VIDEO_BITRATE_KBPS: int = 2500
    # Capture a frame a few seconds after a game starts and use it as cover art when none exists.
    AUTO_COVER_ART: bool = True
    NEWS_MONITOR: bool = True          # check C64 news / new releases / videos every 30 minutes
    AUTO_COVER_DELAY_SECONDS: float = 10.0
    AUTO_COVER_WINDOW_SECONDS: float = 50.0
    # Look up box art / title screens (libretro thumbnails) and CSDB screenshots online.
    COVER_ART_ONLINE: bool = True

    # --- MCP --------------------------------------------------------------
    MCP_API_BASE: str = "http://127.0.0.1:8064"
    # Built-in MCP endpoint at http://<host>:<WEB_PORT>/mcp (Streamable HTTP).
    MCP_ENABLED: bool = True
    # Bearer token for /mcp. Without one, only this machine (and MCP_ALLOWED_HOSTS) may connect.
    MCP_TOKEN: str = ""
    # Extra Host names/IPs allowed to reach /mcp without a token, comma separated (e.g. "spark-2d40").
    MCP_ALLOWED_HOSTS: str = ""

    @field_validator("C64_ULTIMATE_HOST")
    @classmethod
    def _strip_host(cls, v: str) -> str:
        v = (v or "").strip()
        for prefix in ("http://", "https://"):
            if v.lower().startswith(prefix):
                v = v[len(prefix):]
        return v.rstrip("/")

    # ------------------------------------------------------------------
    @property
    def data_path(self) -> Path:
        return Path(self.DATA_DIR).resolve()

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"sqlite:///{(self.data_path / 'c64console.db').as_posix()}"

    @property
    def base_url(self) -> str:
        host = self.C64_ULTIMATE_HOST
        default = 443 if self.C64_ULTIMATE_PROTOCOL == "https" else 80
        port = "" if default == self.C64_ULTIMATE_PORT else f":{self.C64_ULTIMATE_PORT}"
        return f"{self.C64_ULTIMATE_PROTOCOL}://{host}{port}"

    @property
    def library_paths(self) -> list[str]:
        return [p.strip() for p in self.LIBRARY_PATHS.split(";" if ";" in self.LIBRARY_PATHS else ",")
                if p.strip()]

    @property
    def device_configured(self) -> bool:
        return self.SIMULATE_C64 or bool(self.C64_ULTIMATE_HOST)

    def public_dict(self) -> dict[str, Any]:
        """Settings safe to send to the UI: secrets replaced by a presence flag."""
        out: dict[str, Any] = {}
        for key, value in self.model_dump().items():
            if key in SECRET_FIELDS:
                out[key] = ""
                out[f"{key}_SET"] = bool(value)
            else:
                out[key] = value
        return out


class ConfigStore:
    """Holds the live Settings and persists UI edits to settings.json."""

    def __init__(self, base: Settings | None = None, overrides_file: Path | None = None):
        self._lock = threading.RLock()
        self._env_settings = base or Settings()
        self._file = overrides_file or (self._env_settings.data_path / "settings.json")
        self._overrides: dict[str, Any] = self._load()
        self._settings = self._merge()
        self._listeners: list[Any] = []

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return {k: v for k, v in data.items() if k in EDITABLE_FIELDS}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}

    def _merge(self) -> Settings:
        data = self._env_settings.model_dump()
        data.update(self._overrides)
        return Settings.model_validate(data)

    @property
    def settings(self) -> Settings:
        return self._settings

    def update(self, changes: dict[str, Any]) -> Settings:
        with self._lock:
            clean = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
            # An empty secret in an update means "leave unchanged" unless explicitly cleared.
            for secret in SECRET_FIELDS:
                if secret in clean and clean[secret] == "" and not changes.get(f"CLEAR_{secret}"):
                    clean.pop(secret)
                if changes.get(f"CLEAR_{secret}"):
                    clean[secret] = ""
            candidate = dict(self._overrides)
            candidate.update(clean)
            merged = self._env_settings.model_dump()
            merged.update(candidate)
            new_settings = Settings.model_validate(merged)  # validate before persisting
            self._overrides = candidate
            self._settings = new_settings
            self._persist()
        for listener in list(self._listeners):
            listener(self._settings)
        return self._settings

    def on_change(self, callback: Any) -> None:
        self._listeners.append(callback)

    def _persist(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._overrides, indent=2), encoding="utf-8")
        os.replace(tmp, self._file)
        try:
            os.chmod(self._file, 0o600)
        except OSError:
            pass


def env_flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}



