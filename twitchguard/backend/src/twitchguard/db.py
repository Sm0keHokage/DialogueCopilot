"""Async SQLAlchemy engine/session helpers. PostgreSQL in production, SQLite in tests."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def create_db_engine(url: str) -> AsyncEngine:
    kwargs: dict[str, object] = {}
    if url.startswith("sqlite"):
        # In-memory SQLite must share one connection across sessions.
        kwargs["poolclass"] = StaticPool
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_async_engine(url, **kwargs)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_models(engine: AsyncEngine) -> None:
    from . import models  # noqa: F401 - ensure tables are registered

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat naive as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt
