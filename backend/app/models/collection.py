"""Tables for the 📦 collection feature (registered on the shared Base; imported by init_db)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base, utcnow


class CollectionItem(Base):
    """One physical thing you own (or want): a boxed game, cartridge, disk, hardware…"""
    __tablename__ = "collection_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # game | cartridge | disk | tape | hardware | accessory | book | other
    kind: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(30), default="C64")
    edition: Mapped[str | None] = mapped_column(String(120), nullable=True)   # publisher / region / variant
    # sealed | mint | very-good | good | fair | poor | parts
    condition: Mapped[str | None] = mapped_column(String(20), nullable=True)
    boxed: Mapped[bool] = mapped_column(Boolean, default=False)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)            # manual, inlays…
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    serial: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(80), nullable=True)   # shelf / box
    purchase_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    purchase_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)          # per unit
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    value_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    value_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    game_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)   # matched library game (box art)
    wishlist: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
