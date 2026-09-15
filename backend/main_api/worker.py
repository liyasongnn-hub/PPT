"""Redis 任务消费者入口（生产多实例）。

与 ``main_api`` 复用同一镜像。启动后初始化数据库，按 ``WORKER_CONSUMERS``（默认 2）
并发消费 ``jobs`` Stream，并把结果直写 PostgreSQL。进程内不建表（PostgreSQL 由
migration 服务负责）；仅在误配 SQLite 时兜底 ``create_all``。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("worker")

dotenv.load_dotenv()

from db import close_engine, create_all, init_engine, is_postgres  # noqa: E402
from job_queue import make_consumer_name, make_job_queue  # noqa: E402
from store import PlatformStore  # noqa: E402

_PROJECT_ROOT = Path(__file__).parent.parent.parent


async def main() -> None:
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        raise SystemExit("worker 需要设置 REDIS_URL（Redis Stream 队列）")

    data_dir = Path(os.environ.get("PLATFORM_DATA_DIR", str(_PROJECT_ROOT / "data")))
    init_engine(os.environ.get("DATABASE_URL") or None, data_dir)
    if not is_postgres():
        await create_all()

    store = PlatformStore()
    queue = make_job_queue(store)

    consumers = max(1, int(os.environ.get("WORKER_CONSUMERS", "2")))
    logger.info("worker starting with %s consumers", consumers)

    tasks = [asyncio.create_task(queue.run_consumer(make_consumer_name())) for _ in range(consumers)]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_engine()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
