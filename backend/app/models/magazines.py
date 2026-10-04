"""Tables for the magazines feature (registered on the shared Base; imported by init_db).

The full-text index itself lives in its own SQLite file (``data/magazines.db``, FTS5) — see
``app.services.magazines``. These tables only hold the issue catalog and the cached game reviews.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base, utcnow


class MagazineSeries(Base):
    """When a series' issue list was last read from the Internet Archive."""
    __tablename__ = "magazine_series"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(300), nullable=True)


class MagazineIssue(Base):
    """One issue. ``key`` is the IA identifier, or ``identifier/stem`` for an issue inside a multi-file item."""
    __tablename__ = "magazine_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(String(300), unique=True, index=True)
    identifier: Mapped[str] = mapped_column(String(200))
    stem: Mapped[str | None] = mapped_column(String(200), nullable=True)       # multi-file items only
    text_file: Mapped[str | None] = mapped_column(String(300), nullable=True)  # the *_djvu.txt (once known)
    text_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    date: Mapped[str | None] = mapped_column(String(10), nullable=True)          # YYYY-MM or YYYY-MM-DD
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MagazineReview(Base):
    """🤖 The magazine reviews found for one game (cached)."""
    __tablename__ = "magazine_reviews"

    game_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    reviews: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    made_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
