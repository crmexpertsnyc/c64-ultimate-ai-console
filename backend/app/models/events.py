"""Tables for the 📅 events feature (registered on the shared Base; imported by init_db)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base, utcnow


class Event(Base):
    """📅 A retro event: a demoparty from CSDb, a fair / expo / meetup found by 🤖 web research, or one the user added."""
    __tablename__ = "retro_events"
    __table_args__ = (UniqueConstraint("ext_id", name="uq_retro_events_ext_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(10), index=True)          # csdb | web | user
    ext_id: Mapped[str] = mapped_column(String(200))                      # "csdb:3657", "web:<name>:<start>", "user:<hex>"
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str | None] = mapped_column(String(120), nullable=True)  # "Demo Party, Meeting", "Computer Fair" …
    start: Mapped[date] = mapped_column(Date, index=True)
    end: Mapped[date] = mapped_column(Date, index=True)                   # inclusive (the last day)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)   # US state / CA province code
    scope: Mapped[str | None] = mapped_column(String(12), nullable=True, index=True)    # commodore | retro
    url: Mapped[str | None] = mapped_column(String(600), nullable=True)          # the event's own website
    page_url: Mapped[str | None] = mapped_column(String(600), nullable=True)     # its CSDb page / the cited web page
    stream_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    image: Mapped[str | None] = mapped_column(String(600), nullable=True)
    tagline: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    details_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # CSDb page read
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EventState(Base):
    """Small key → value memory for the events feature (last CSDb check, last web research, firmware check)."""
    __tablename__ = "retro_event_state"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
