from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings


def build_engine(url: str) -> AsyncEngine:
    connect_args: dict[str, Any] = {}
    if url.startswith("postgresql+asyncpg"):
        # Per-connection statement timeout (covers every transaction, unlike SET LOCAL).
        connect_args["server_settings"] = {
            "statement_timeout": str(int(settings.db_statement_timeout_ms))
        }
    engine = create_async_engine(url, pool_pre_ping=True, connect_args=connect_args)

    if engine.dialect.name == "sqlite":
        # SQLite only enforces FKs (and therefore ON DELETE CASCADE) when asked to.
        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn: Any, _record: Any) -> None:
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


engine = build_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
