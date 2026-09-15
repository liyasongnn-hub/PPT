"""Async SQLAlchemy engine / session factory.

生产环境使用 PostgreSQL（asyncpg），本地开发未配置 ``DATABASE_URL`` 时回退到
SQLite（aiosqlite），让 ``start.py`` 在无 Docker 的情况下也能跑。Alembic 迁移只
针对 PostgreSQL 执行；SQLite 走 ``metadata.create_all`` 自动建表。
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_is_postgres = False


def normalize_database_url(url: str) -> str:
    """把各种 PostgreSQL URL 归一化为 ``postgresql+asyncpg://``，并处理 sslmode。"""
    url = url.strip()
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgresql+asyncpg") and "sslmode=" in url:
        url = (
            url.replace("sslmode=require", "ssl=require")
            .replace("sslmode=verify-full", "ssl=verify-full")
            .replace("sslmode=verify-ca", "ssl=verify-ca")
            .replace("sslmode=prefer", "ssl=prefer")
            .replace("sslmode=disable", "ssl=disable")
        )
    return url


def default_database_url(data_dir: Path) -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{(data_dir / 'platform.db').as_posix()}"


def init_engine(database_url: str | None = None, data_dir: Path | None = None) -> None:
    global _engine, _session_factory, _is_postgres
    if database_url:
        url = normalize_database_url(database_url)
    else:
        url = default_database_url(data_dir or Path("data"))
    _is_postgres = url.startswith("postgresql")

    kwargs: dict = {"pool_pre_ping": True}
    if _is_postgres:
        kwargs["pool_size"] = int(os.environ.get("DB_POOL_SIZE", "20"))
        kwargs["max_overflow"] = int(os.environ.get("DB_MAX_OVERFLOW", "10"))
        kwargs["pool_timeout"] = float(os.environ.get("DB_POOL_TIMEOUT", "30"))
    _engine = create_async_engine(url, **kwargs)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


def get_session() -> AsyncSession:
    if _session_factory is None:
        raise RuntimeError("数据库引擎尚未初始化，请先调用 init_engine()")
    return _session_factory()


def is_postgres() -> bool:
    return _is_postgres


async def create_all() -> None:
    """SQLite 模式下按模型元数据自动建表（PostgreSQL 由 Alembic 迁移负责）。"""
    from models import Base  # 延迟导入，避免循环依赖

    if _engine is None:
        raise RuntimeError("数据库引擎尚未初始化，请先调用 init_engine()")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
