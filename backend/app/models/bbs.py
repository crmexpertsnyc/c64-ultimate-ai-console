"""Tables for the 📟 BBS directory (registered on the shared Base; imported by init_db)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base, utcnow


class BbsBoard(Base):
    """A bulletin board system reachable over telnet. One row per (normalized host, port)."""
    __tablename__ = "bbs_boards"
    __table_args__ = (UniqueConstraint("host", "port", name="uq_bbs_host_port"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    host: Mapped[str] = mapped_column(String(255), index=True)            # normalized: lower case, no trailing dot
    port: Mapped[int] = mapped_column(Integer, default=23)
    protocol: Mapped[str] = mapped_column(String(10), default="telnet")    # telnet | raw (no telnet negotiation)
    location: Mapped[str | None] = mapped_column(String(160), nullable=True)
    website: Mapped[str | None] = mapped_column(String(400), nullable=True)
    software: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # terminal compatibility: confirmed | unverified | unknown — never guessed from the word "Commodore"
    petscii: Mapped[str] = mapped_column(String(12), default="unknown")
    ansi: Mapped[str] = mapped_column(String(12), default="unknown")
    compat_note: Mapped[str | None] = mapped_column(String(300), nullable=True)   # why (which source said what)
    # where it came from: [{source, label, url, seenAt, listingUpdated}]
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # admin approval before the browser terminal may connect: pending | approved | rejected
    review: Mapped[str] = mapped_column(String(10), default="pending", index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # connectivity: unknown | reachable | unreachable (a TCP connect, not proof the board works)
    status: Mapped[str] = mapped_column(String(12), default="unknown")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    listed: Mapped[bool] = mapped_column(Boolean, default=True)          # still in a source on the last refresh
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BbsUserData(Base):
    """⭐ favorite and 📝 notes, per family profile."""
    __tablename__ = "bbs_user_data"
    __table_args__ = (UniqueConstraint("board_id", "profile_id", name="uq_bbs_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    board_id: Mapped[int] = mapped_column(Integer, index=True)
    profile_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BbsImportRun(Base):
    """One directory refresh: which sources ran, what they gave, what failed."""
    __tablename__ = "bbs_import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)    # {source: {records, added, updated, error}}
