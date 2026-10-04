"""Tables for the 🛒 shop feature (registered on the shared Base; imported by init_db)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base, utcnow


class PriceWatch(Base):
    """💰 An eBay search to watch: alert when a listing at or under ``max_price`` shows up (wishlist / shop)."""
    __tablename__ = "price_watches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query: Mapped[str] = mapped_column(String(120))
    max_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    collection_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    lowest: Mapped[float | None] = mapped_column(Float, nullable=True)       # lowest price seen on the last check
    alerted: Mapped[list[str]] = mapped_column(JSON, default=list)            # listing ids already announced
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    extra: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
