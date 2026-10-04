"""Logging with secret redaction.

Every log record passes through ``RedactingFilter`` which replaces any registered
secret value (Ultimate password, AI API key) and any ``X-Password`` /
``Authorization`` / ``x-api-key`` header values with ``***``.
"""

from __future__ import annotations

import logging
import re
import threading

_secrets: set[str] = set()
_lock = threading.Lock()

_HEADER_RE = re.compile(
    r"(?i)(x-password|authorization|x-api-key|api[_-]?key|password)(['\"]?\s*[:=]\s*['\"]?)([^'\",\s}]+)"
)


def register_secret(value: str | None) -> None:
    if value and len(value) >= 1:
        with _lock:
            _secrets.add(value)


def redact(text: str) -> str:
    if not text:
        return text
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for s in secrets:
        text = text.replace(s, "***")
    return _HEADER_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


def redact_obj(obj):  # noqa: ANN001 - generic helper
    """Recursively redact strings inside dicts/lists (used before persisting audit data)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() in {"password", "x-password", "api_key", "authorization",
                                                    "c64_ultimate_password", "ai_api_key"}:
                out[k] = "***" if v else v
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(isinstance(h, logging.StreamHandler) and getattr(h, "_c64", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler._c64 = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(handler)
    for h in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in h.filters):
            h.addFilter(RedactingFilter())
    # httpx logs full URLs at INFO; keep it quieter.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
