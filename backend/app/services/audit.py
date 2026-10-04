"""Audit log: every command records who/what/which API calls/result/duration.

Device calls made while an ``action`` is open are captured automatically through the
client's call listener (via a context variable), so the audit shows the exact REST
operations a command produced. Secrets are redacted before anything is stored.
"""

from __future__ import annotations

import contextvars
import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.logging_setup import redact, redact_obj
from app.models.db import AuditEntry
from app.ultimate.client import CallRecord

from .events import EventHub

log = logging.getLogger("c64.audit")

_current: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar("audit_calls", default=None)


class ActionRecord:
    def __init__(self, source: str, operation: str, user_command: str | None, intent: dict | None):
        self.source = source
        self.operation = operation
        self.user_command = user_command
        self.intent = intent
        self.calls: list[dict[str, Any]] = []
        self.response: Any = None
        self.success = True
        self.error: str | None = None

    def set_response(self, response: Any) -> None:
        self.response = response


class AuditService:
    def __init__(self, session_factory: sessionmaker, hub: EventHub, keep: int = 20000):
        self.sf = session_factory
        self.hub = hub
        self.keep = keep
        self.recent: deque[dict[str, Any]] = deque(maxlen=100)
        self.recent_calls: deque[dict[str, Any]] = deque(maxlen=200)

    def on_client_call(self, rec: CallRecord) -> None:
        entry = asdict(rec)
        entry["ts"] = time.time()
        self.recent_calls.append(entry)
        calls = _current.get()
        if calls is not None:
            calls.append({k: entry[k] for k in ("operation", "method", "path", "status", "elapsed_ms", "ok", "errors")})

    @asynccontextmanager
    async def action(self, source: str, operation: str, user_command: str | None = None,
                     intent: dict | None = None):
        rec = ActionRecord(source, operation, user_command, intent)
        token = _current.set(rec.calls)
        start = time.perf_counter()
        try:
            yield rec
        except Exception as exc:
            rec.success = False
            rec.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            _current.reset(token)
            self._store(rec, (time.perf_counter() - start) * 1000)

    def _store(self, rec: ActionRecord, duration_ms: float) -> None:
        response = rec.response
        if response is not None and not isinstance(response, str):
            import json
            try:
                response = json.dumps(redact_obj(response), default=str)[:4000]
            except (TypeError, ValueError):
                response = str(response)[:4000]
        entry = AuditEntry(
            source=rec.source, user_command=redact(rec.user_command) if rec.user_command else None,
            intent=redact_obj(rec.intent) if rec.intent else None, operation=rec.operation,
            api_calls=rec.calls, device_response=redact(response) if isinstance(response, str) else None,
            success=rec.success if rec.error is None else False, duration_ms=round(duration_ms, 1),
            error=redact(rec.error) if rec.error else None,
        )
        try:
            with self.sf() as s:
                s.add(entry)
                s.commit()
                data = audit_to_dict(entry)
        except Exception:  # pragma: no cover - never let auditing break a command
            log.exception("failed to write audit entry")
            return
        self.recent.appendleft(data)
        self.hub.publish("audit", data)
        (log.info if entry.success else log.warning)(
            "[%s] %s %s — %s (%.0f ms)%s", rec.source, rec.operation, rec.user_command or "",
            "ok" if entry.success else "FAILED", duration_ms, f": {entry.error}" if entry.error else "")

    def list(self, limit: int = 100, offset: int = 0, failures_only: bool = False,
             source: str | None = None) -> list[dict[str, Any]]:
        with self.sf() as s:
            stmt = select(AuditEntry).order_by(AuditEntry.id.desc())
            if failures_only:
                stmt = stmt.where(AuditEntry.success.is_(False))
            if source:
                stmt = stmt.where(AuditEntry.source == source)
            return [audit_to_dict(e) for e in s.scalars(stmt.limit(limit).offset(offset))]

    def prune(self) -> None:
        with self.sf() as s:
            cutoff = s.scalar(select(AuditEntry.id).order_by(AuditEntry.id.desc()).offset(self.keep).limit(1))
            if cutoff:
                s.execute(delete(AuditEntry).where(AuditEntry.id <= cutoff))
                s.commit()


def audit_to_dict(e: AuditEntry) -> dict[str, Any]:
    return {"id": e.id, "timestamp": e.timestamp.isoformat() if e.timestamp else None, "source": e.source,
            "userCommand": e.user_command, "intent": e.intent, "operation": e.operation, "apiCalls": e.api_calls,
            "deviceResponse": e.device_response, "success": e.success, "durationMs": e.duration_ms,
            "error": e.error}
