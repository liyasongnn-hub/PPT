#!/usr/bin/env python3
"""把旧版 SQLite（``data/platform.db``）迁移到 PostgreSQL。

用法（在仓库根目录运行）：

    python scripts/migrate_sqlite_to_postgres.py

从环境变量读取 ``DATABASE_URL``（必须是 PostgreSQL）。旧 SQLite 路径默认
``data/platform.db``，可用 ``--source`` 覆盖。目标库已有数据时会拒绝执行，
除非显式加 ``--force``（会清空目标业务表后重灌）。

说明：
- 旧库的表结构与新版 SQLAlchemy 模型物理列名一致（``config_json`` 等），
  JSON 字段在 SQLite 里是 TEXT，这里直接 ``json.loads`` 后交给 ORM 写入 JSONB。
- 整数主键（users.id / job_events.id）原样保留，灌完后重置对应序列。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend" / "main_api"))

from sqlalchemy import func, select, text  # noqa: E402

from db import close_engine, get_session, init_engine, is_postgres  # noqa: E402
from models import Job, JobEvent, Project, User  # noqa: E402


def parse_ts(value: str | None) -> datetime:
    """旧库时间戳形如 ``2026-09-15T10:00:00Z``，转成带时区 datetime。"""
    if not value:
        return datetime.now().astimezone()
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.now().astimezone().tzinfo)


def load_sqlite(path: Path) -> dict[str, list[dict]]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            "users": [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id")],
            "projects": [dict(r) for r in conn.execute("SELECT * FROM projects ORDER BY created_at")],
            "jobs": [dict(r) for r in conn.execute("SELECT * FROM jobs ORDER BY created_at")],
            "job_events": [dict(r) for r in conn.execute("SELECT * FROM job_events ORDER BY id")],
        }
    finally:
        conn.close()
    return tables


def _json(value: str | None) -> dict:
    return json.loads(value or "{}")


async def _has_data(session) -> bool:
    return (await session.execute(select(func.count()).select_from(User))).scalar_one() > 0


async def _reset_sequence(session, table: str, column: str) -> None:
    await session.execute(
        text(f"SELECT setval(pg_get_serial_sequence('{table}','{column}'), COALESCE(MAX({column}),1), true) FROM {table}")
    )


async def migrate(source: Path, force: bool) -> int:
    if not is_postgres():
        raise SystemExit(
            "DATABASE_URL 不是 PostgreSQL。迁移脚本只把旧 SQLite 迁往 PG，"
            "本地 SQLite 开发无需迁移。"
        )

    data = load_sqlite(source)

    async with get_session() as session:
        if await _has_data(session):
            if not force:
                raise SystemExit(
                    "目标库已有数据。若确实要重灌，请加 --force（会清空业务表）。"
                )
            # 依赖顺序反着删，避免外键报错
            for table in ("files", "job_events", "jobs", "projects", "users"):
                await session.execute(text(f"DELETE FROM {table}"))
            await session.commit()
            print("已清空目标业务表。")

        users = [
            User(
                id=u["id"],
                email=u["email"],
                password_hash=u["password_hash"],
                tenant_id=u["tenant_id"],
                display_name=u["display_name"],
                created_at=parse_ts(u["created_at"]),
            )
            for u in data["users"]
        ]
        session.add_all(users)
        await session.flush()

        session.add_all(
            Project(
                id=p["id"],
                tenant_id=p["tenant_id"],
                owner_id=p["owner_id"],
                title=p["title"],
                prompt=p["prompt"],
                config=_json(p.get("config_json")),
                status=p["status"],
                outline=p.get("outline", ""),
                document=_json(p.get("document_json")),
                created_at=parse_ts(p["created_at"]),
                updated_at=parse_ts(p["updated_at"]),
            )
            for p in data["projects"]
        )
        await session.flush()

        session.add_all(
            Job(
                id=j["id"],
                project_id=j["project_id"],
                owner_id=j["owner_id"],
                type=j["type"],
                status=j["status"],
                progress=j.get("progress", 0),
                result=_json(j.get("result_json")),
                error=j.get("error", ""),
                retry_count=0,
                created_at=parse_ts(j["created_at"]),
                updated_at=parse_ts(j["updated_at"]),
            )
            for j in data["jobs"]
        )
        await session.flush()

        session.add_all(
            JobEvent(
                id=e["id"],
                job_id=e["job_id"],
                event_type=e["event_type"],
                stage=e["stage"],
                progress=e.get("progress", 0),
                payload=_json(e.get("payload_json")),
                created_at=parse_ts(e["created_at"]),
            )
            for e in data["job_events"]
        )
        await session.commit()

        await _reset_sequence(session, "users", "id")
        await _reset_sequence(session, "job_events", "id")
        await session.commit()

    total = sum(len(v) for v in data.values())
    print(
        f"迁移完成：users={len(data['users'])} projects={len(data['projects'])} "
        f"jobs={len(data['jobs'])} job_events={len(data['job_events'])}（共 {total} 行）"
    )
    return total


async def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 数据迁移")
    parser.add_argument("--source", type=Path, default=REPO_ROOT / "data" / "platform.db",
                        help="旧 SQLite 文件路径（默认 data/platform.db）")
    parser.add_argument("--force", action="store_true", help="目标库已有数据时清空重灌")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"找不到旧 SQLite 数据库：{args.source}")

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL 未设置，无法连接 PostgreSQL。")

    init_engine(database_url, REPO_ROOT / "data")
    try:
        await migrate(args.source, args.force)
    finally:
        await close_engine()


if __name__ == "__main__":
    asyncio.run(main())
