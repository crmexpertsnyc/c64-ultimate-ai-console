"""Table for the 🔄 update scheduler: how often each source is checked, and how the last check went."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    every: Mapped[int | None] = mapped_column(Integer, nullable=True)        # seconds; None = the job's default
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_summary: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    runs: Mapped[int] = mapped_column(Integer, default=0)
