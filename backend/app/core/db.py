"""Async SQLAlchemy engine/session plus the declarative base."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Built on first use rather than at import.
#
# Creating the engine as a module-level side effect meant that importing
# anything under `app.models` — even a bare enum — loaded the Postgres driver.
# That is why a policy test with no database in it could not run without
# asyncpg installed, and why the compiler had to be split out to be testable.
# A connection pool is a resource; resources are acquired when something wants
# one, not when a module is read.
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            future=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession)
    return _session_factory


class _LazyProxy:
    """Keeps `engine` and `SessionLocal` usable as names.

    Callers already say `SessionLocal()` and `engine.begin()`; making them all
    call a getter would be churn for no benefit. This defers the construction
    without changing a single call site.
    """

    __slots__ = ("_get",)

    def __init__(self, get) -> None:
        object.__setattr__(self, "_get", get)

    def __call__(self, *args, **kwargs):
        return object.__getattribute__(self, "_get")()(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_get")(), name)


engine: Any = _LazyProxy(get_engine)
SessionLocal: Any = _LazyProxy(get_session_factory)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one transaction per request."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
