"""Alembic 迁移入口。

只面向 PostgreSQL：从 ``DATABASE_URL`` 读取目标库并归一化，再用 async 引擎跑迁移。
检测到 SQLite 时直接报错——本地开发由应用 ``Base.metadata.create_all()`` 自动建表，
强行对 SQLite 跑迁移会与自动建表冲突。
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 确保能 import 到同目录的 db.py / models.py（alembic.ini 已 prepend_sys_path=.，这里兜底）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import normalize_database_url  # noqa: E402
from models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _load_dotenv() -> None:
    """按 main_api/.env → 仓库根 .env 的顺序加载，生产由 compose 注入环境变量。"""
    try:
        import dotenv
    except ImportError:
        return
    candidates = [
        Path(__file__).resolve().parent.parent / ".env",
        Path(__file__).resolve().parent.parent.parent.parent / ".env",
    ]
    for path in candidates:
        if path.exists():
            dotenv.load_dotenv(path)


def _database_url() -> str:
    _load_dotenv()
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL 未设置。Alembic 迁移仅用于 PostgreSQL；本地 SQLite 开发由应用自动建表，无需迁移。"
        )
    url = normalize_database_url(url)
    if not url.startswith("postgresql"):
        raise RuntimeError(
            f"Alembic 仅用于 PostgreSQL，检测到非 PG 的 DATABASE_URL（{url.split('://')[0]}）。"
            "本地 SQLite 开发请跳过迁移。"
        )
    return url


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    url = _database_url()
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url
    connectable = async_engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
