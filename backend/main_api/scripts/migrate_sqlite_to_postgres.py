#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把旧版同步 SQLite（data/platform.db，4 张表）迁移到 PostgreSQL。

用法（在 backend/main_api 下，或任意目录）：
    python scripts/migrate_sqlite_to_postgres.py \
        --source ../data/platform.db \
        --target "postgresql+asyncpg://ppt:password@localhost:5432/ppt_platform"

- 目标库需已执行 ``alembic upgrade head``（表结构已存在）。
- 仅搬运 users / projects / jobs / job_events 四张表，files 为新增表无需迁移。
- jobs 的新增列（retry_count / heartbeat_at / cost_usd / tokens_input / tokens_output）
  使用默认值，旧数据不产生这些观测字段。
- 幂等：已存在相同主键的行会跳过。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 让脚本能在任意目录下 import 到同仓库的 db / models
_MAIN_API = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MAIN_API))

import sqlite3  # noqa: E402

from db import close_engine, get_session, init_engine  # noqa: E402
from models import Job, JobEvent, Project, User  # noqa: E402


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def loads(value: str | None, default):
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


async def migrate(source: Path, target_url: str) -> None:
    init_engine(target_url)
    conn = sqlite3.connect(str(source))
    conn.row_factory = sqlite3.Row
    try:
        async with get_session() as session:
            # users
            users = [dict(r) for r in conn.execute("SELECT * FROM users")]
            for row in users:
                if await session.get(User, row["id"]):
                    continue
                session.add(
                    User(
                        id=row["id"],
                        email=row["email"],
                        password_hash=row["password_hash"],
                        tenant_id=row["tenant_id"],
                        display_name=row["display_name"],
                        created_at=parse_dt(row["created_at"]),
                    )
                )
            print(f"users: {len(users)} rows")

            # projects
            projects = [dict(r) for r in conn.execute("SELECT * FROM projects")]
            for row in projects:
                if await session.get(Project, row["id"]):
                    continue
                session.add(
                    Project(
                        id=row["id"],
                        tenant_id=row["tenant_id"],
                        owner_id=row["owner_id"],
                        title=row["title"],
                        prompt=row["prompt"],
                        config=loads(row.get("config_json"), {}),
                        status=row["status"],
                        outline=row.get("outline", ""),
                        document=loads(row.get("document_json"), {}),
                        created_at=parse_dt(row["created_at"]),
                        updated_at=parse_dt(row["updated_at"]),
                    )
                )
            print(f"projects: {len(projects)} rows")

            # jobs
            jobs = [dict(r) for r in conn.execute("SELECT * FROM jobs")]
            for row in jobs:
                if await session.get(Job, row["id"]):
                    continue
                session.add(
                    Job(
                        id=row["id"],
                        project_id=row["project_id"],
                        owner_id=row["owner_id"],
                        type=row["type"],
                        status=row["status"],
                        progress=row["progress"],
                        result=loads(row.get("result_json"), {}),
                        error=row.get("error", ""),
                        retry_count=0,
                        created_at=parse_dt(row["created_at"]),
                        updated_at=parse_dt(row["updated_at"]),
                    )
                )
            print(f"jobs: {len(jobs)} rows")

            # job_events
            events = [dict(r) for r in conn.execute("SELECT * FROM job_events")]
            for row in events:
                if await session.get(JobEvent, row["id"]):
                    continue
                session.add(
                    JobEvent(
                        id=row["id"],
                        job_id=row["job_id"],
                        event_type=row["event_type"],
                        stage=row["stage"],
                        progress=row["progress"],
                        payload=loads(row.get("payload_json"), {}),
                        created_at=parse_dt(row["created_at"]),
                    )
                )
            print(f"job_events: {len(events)} rows")

            await session.commit()
        print("迁移完成")
    finally:
        conn.close()
        await close_engine()


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移旧版 SQLite 到 PostgreSQL")
    default_source = _MAIN_API.parent.parent / "data" / "platform.db"
    parser.add_argument("--source", default=str(default_source), help="旧版 SQLite 路径")
    parser.add_argument("--target", default=os.environ.get("DATABASE_URL", ""), help="目标 PostgreSQL URL（默认取 DATABASE_URL）")
    args = parser.parse_args()

    if not args.target:
        parser.error("请通过 --target 或 DATABASE_URL 环境变量指定目标 PostgreSQL 连接串")
    if not Path(args.source).exists():
        parser.error(f"源 SQLite 文件不存在：{args.source}")

    asyncio.run(migrate(Path(args.source), args.target))


if __name__ == "__main__":
    main()
