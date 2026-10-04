"""SQLAlchemy models and engine.

Portable column types only (String/Integer/Float/Boolean/DateTime/JSON) so the same
schema runs on SQLite now and PostgreSQL later — just set DATABASE_URL to a
``postgresql+psycopg://`` URL and install the ``postgres`` extra.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    normalized_title: Mapped[str] = mapped_column(String(255), index=True)
    alternate_names: Mapped[list[str]] = mapped_column(JSON, default=list)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    genre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str] = mapped_column(String(20), default="game")  # game|music|demo|tool
    format: Mapped[str] = mapped_column(String(10))  # primary media format
    num_disks: Mapped[int] = mapped_column(Integer, default=1)
    joystick_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    players: Mapped[str | None] = mapped_column(String(20), nullable=True)
    preferred_launch: Mapped[str] = mapped_column(String(30), default="auto")
    load_command: Mapped[str | None] = mapped_column(String(80), nullable=True)
    run_after_load: Mapped[bool] = mapped_column(Boolean, default=True)
    reset_before_load: Mapped[bool] = mapped_column(Boolean, default=True)
    startup_delay: Mapped[float] = mapped_column(Float, default=3.0)
    load_timeout: Mapped[float] = mapped_column(Float, default=90.0)
    needs_fire: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    cover_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_played: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    play_count: Mapped[int] = mapped_column(Integer, default=0)
    source_root: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    media: Mapped[list[Media]] = relationship(back_populates="game", cascade="all, delete-orphan",
                                              order_by="Media.disk_number")


class Media(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(2048), unique=True)
    storage: Mapped[str] = mapped_column(String(10), default="local")  # local | device
    format: Mapped[str] = mapped_column(String(10))
    disk_number: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    mtime: Mapped[float] = mapped_column(Float, default=0.0)
    missing: Mapped[bool] = mapped_column(Boolean, default=False)
    info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    game: Mapped[Game] = relationship(back_populates="media")


class LibraryRoot(Base):
    __tablename__ = "library_roots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(2048), unique=True)
    storage: Mapped[str] = mapped_column(String(10), default="local")
    last_scan: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEntry(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(30))  # ui | api | command | mcp | system | vision
    user_command: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    operation: Mapped[str] = mapped_column(String(100))
    api_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    device_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TasteEvent(Base):
    """Something that says what the player likes: a search, a question, a play, a favorite, time played.
    Only kept on this console; the recommendation engine turns these into a taste profile."""
    __tablename__ = "taste_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)  # search | ask | play | play_browser | session | favorite
    profile_id: Mapped[int] = mapped_column(Integer, default=1, index=True)  # who (family profiles; 1 = the first)
    game_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    text: Mapped[str | None] = mapped_column(String(300), nullable=True)
    value: Mapped[float] = mapped_column(Float, default=1.0)  # e.g. minutes played for "session"


class Rating(Base):
    """Thumbs up (+1) / down (-1) for a game, by title (so games not yet in the library can be rated too)."""
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("profile_id", "title_key", name="uq_ratings_profile_title"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    title_key: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255))
    game_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Playlist(Base):
    """🎉 A game night or playlist: ordered games (with notes, players, time) made by the AI from a request."""
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    prompt: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GameIssue(Base):
    """🐞 A game that did not work in Browser Play: reported automatically (won't load, never starts, hangs,
    emulator error) or by the player, with diagnostics captured at that moment — for later investigation."""
    __tablename__ = "game_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    game_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(10), default="auto")  # auto | user
    category: Mapped[str] = mapped_column(String(20), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(15), default="open", index=True)  # open | investigating | fixed | wontfix
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    worked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Profile(Base):
    """👪 A family member: their own recommendations, ratings, weekly recap and Browser Play saves."""
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    emoji: Mapped[str] = mapped_column(String(8), default="🙂")
    color: Mapped[str] = mapped_column(String(9), default="#7c70da")
    kids: Mapped[bool] = mapped_column(Boolean, default=False)  # family-friendly suggestions only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Achievement(Base):
    """🏆 Something to reach in a game, checked from what the game shows (score, round, lives …) or play time."""
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # None = every game
    key: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(200))
    icon: Mapped[str] = mapped_column(String(8), default="🏆")
    metric: Mapped[str] = mapped_column(String(20))   # score | level | lives | time | minutes | days | gameplay
    target: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(10), default="ai")  # ai | builtin
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AchievementUnlock(Base):
    __tablename__ = "achievement_unlocks"
    __table_args__ = (UniqueConstraint("achievement_id", "profile_id", "game_id", name="uq_unlock"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    achievement_id: Mapped[int] = mapped_column(Integer, index=True)
    game_id: Mapped[int] = mapped_column(Integer, index=True)
    profile_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    value: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(10))  # browser | c64
    unlocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HighScore(Base):
    """A score read from the game's own screen (never typed in)."""
    __tablename__ = "high_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(Integer, index=True)
    profile_id: Mapped[int] = mapped_column(Integer, default=1, index=True)
    score: Mapped[int] = mapped_column(Integer)
    level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(10))  # browser | c64
    achieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NewsItem(Base):
    """📰 C64 news, 🆕 new releases and 🎬 videos picked up from the monitored feeds (CSDb, itch.io, blogs, YouTube)."""
    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("guid", name="uq_news_guid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guid: Mapped[str] = mapped_column(String(400))
    source: Mapped[str] = mapped_column(String(40), index=True)     # csdb | itch | indieretronews | youtube:<channel>
    kind: Mapped[str] = mapped_column(String(16), index=True)       # release | news | video
    category: Mapped[str | None] = mapped_column(String(24), nullable=True)  # game | demo | music | graphics | tool | other
    title: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(600))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    image: Mapped[str | None] = mapped_column(String(600), nullable=True)
    author: Mapped[str | None] = mapped_column(String(160), nullable=True)   # group / developer / channel
    release_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    download_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    csdb_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    video_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    playable: Mapped[bool] = mapped_column(Boolean, default=False)   # the console can add it and play it
    game_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # once added to the library
    # 🔥 popularity: raw counts from the source (downloads, comments, votes, rating, views, likes…) and two
    # 0-100 percentiles within the same source — "popularity" (all-time) and "trend" (per day since release)
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    popularity: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    trend: Mapped[float | None] = mapped_column(Float, nullable=True)
    stats_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def _upgrade_sqlite(engine: Engine) -> None:
    """create_all() makes new tables but never changes existing ones: add the columns and indexes newer
    versions need to an existing database (idempotent)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, column, ddl in (("taste_events", "profile_id", "INTEGER NOT NULL DEFAULT 1"),
                                   ("ratings", "profile_id", "INTEGER NOT NULL DEFAULT 1"),
                                   ("news_items", "stats", "JSON"), ("news_items", "popularity", "FLOAT"),
                                   ("news_items", "trend", "FLOAT"), ("news_items", "stats_at", "DATETIME"),
                                   ("retro_events", "region", "VARCHAR(10)"), ("retro_events", "scope", "VARCHAR(12)")):
            if table in insp.get_table_names() and column not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        if "ratings" in insp.get_table_names():
            idx = {i["name"]: i for i in insp.get_indexes("ratings")}
            old = idx.get("ix_ratings_title_key")
            if old and old.get("unique"):  # one rating per title → one per title *per profile*
                conn.execute(text("DROP INDEX ix_ratings_title_key"))
                conn.execute(text("CREATE INDEX ix_ratings_title_key ON ratings (title_key)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_ratings_profile_title ON ratings (profile_id, title_key)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_ratings_profile_id ON ratings (profile_id)"))
        if "taste_events" in insp.get_table_names():
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_taste_events_profile_id ON taste_events (profile_id)"))


def make_engine(url: str) -> Engine:
    kwargs: dict[str, Any] = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def init_db(url: str) -> sessionmaker:
    if url.startswith("sqlite:///"):
        from pathlib import Path
        Path(url[len("sqlite:///"):]).parent.mkdir(parents=True, exist_ok=True)
    engine = make_engine(url)
    from app.models import bbs, collection, events, jukebox, magazines, scheduler, shop  # noqa: F401 - feature tables
    Base.metadata.create_all(engine)
    if url.startswith("sqlite"):
        _upgrade_sqlite(engine)
    return sessionmaker(engine, expire_on_commit=False)
