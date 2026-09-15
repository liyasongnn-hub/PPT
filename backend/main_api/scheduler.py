"""周期维护任务入口（生产多实例）。

与 ``main_api``/``worker`` 复用同一镜像。周期性执行三类维护：

1. 心跳故障转移：把 Worker 心跳丢失的 ``RUNNING`` 任务置为 ``FAILED``；
2. 排队超时：把长时间未被消费的 ``QUEUED`` 任务置为 ``FAILED``；
3. 保留期清理：清空超过保留期的终态任务的 ``job_events`` 载荷，控制库体积。

间隔与阈值均可通过环境变量调整（见 ``SCHEDULER_*``）。进程内不建表（PostgreSQL 由
migration 服务负责）；仅在误配 SQLite 时兜底 ``create_all``。
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("scheduler")

dotenv.load_dotenv()

from db import close_engine, create_all, init_engine, is_postgres  # noqa: E402
from store import PlatformStore  # noqa: E402

_PROJECT_ROOT = Path(__file__).parent.parent.parent


async def run_once(store: PlatformStore) -> None:
    stale_running = int(os.environ.get("SCHEDULER_STALE_RUNNING_SECONDS", "120"))
    stale_queued = int(os.environ.get("SCHEDULER_STALE_QUEUED_SECONDS", "600"))
    retention_days = int(os.environ.get("SCHEDULER_RETENTION_DAYS", "14"))

    failed_running = await store.fail_stale_running_jobs(stale_running)
    failed_queued = await store.fail_stale_queued_jobs(stale_queued)
    purged = await store.purge_old_jobs(retention_days)

    if failed_running or failed_queued or purged:
        logger.info(
            "maintenance run: stale_running=%d stale_queued=%d purged=%d",
            failed_running,
            failed_queued,
            purged,
        )
    else:
        logger.debug("maintenance run: nothing to do")


async def main() -> None:
    data_dir = Path(os.environ.get("PLATFORM_DATA_DIR", str(_PROJECT_ROOT / "data")))
    init_engine(os.environ.get("DATABASE_URL") or None, data_dir)
    if not is_postgres():
        await create_all()

    store = PlatformStore()
    interval = max(5, int(os.environ.get("SCHEDULER_INTERVAL", "60")))
    logger.info("scheduler starting with interval=%ss", interval)

    try:
        while True:
            try:
                await run_once(store)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 单轮失败不终止调度循环
                logger.exception("scheduler maintenance run failed")
            await asyncio.sleep(interval)
    finally:
        await close_engine()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
