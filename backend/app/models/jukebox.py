"""Tables for the jukebox feature (registered on the shared Base; imported by init_db)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base, utcnow


class JukeboxStation(Base):
    """🎵 An AI-built station: a themed list of HVSC tunes (only ids that Assembly64 returned)."""
    __tablename__ = "jukebox_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    prompt: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tracks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # [{id, category, title, composer, ...}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class JukeboxPlay(Base):
    """One tune played on the C64's SID chip."""
    __tablename__ = "jukebox_plays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entry_id: Mapped[str] = mapped_column(String(64))
    category: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    composer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    released: Mapped[str | None] = mapped_column(String(120), nullable=True)
    song: Mapped[int | None] = mapped_column(Integer, nullable=True)
    songs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    station_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
