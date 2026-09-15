"""任务队列驱动：Redis Stream 与进程内两种实现。

- ``REDIS_URL`` 未设置 → ``InProcessDriver``：``asyncio.create_task`` 直接调度，
  行为与旧版等价（本地开发零依赖）。
- ``REDIS_URL`` 已设置 → ``RedisStreamDriver``：生产多实例。``main_api`` 只负责
  ``XADD``，``worker.py`` 通过 consumer group 消费；崩溃未 ack 的消息由
  ``XAUTOCLAIM`` 在 60s 后回收重投，投递次数超过上限进入死信流 ``jobs_dead_letter``。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import Any

from job_runner import run_job

logger = logging.getLogger(__name__)

STREAM_NAME = os.environ.get("JOB_STREAM", "jobs")
GROUP_NAME = os.environ.get("JOB_GROUP", "default-workers")
DEAD_LETTER_STREAM = os.environ.get("JOB_DEAD_LETTER_STREAM", "jobs_dead_letter")
MAX_DELIVERY_COUNT = int(os.environ.get("JOB_MAX_DELIVERY_COUNT", "3"))
RECLAIM_IDLE_MS = int(os.environ.get("JOB_RECLAIM_IDLE_MS", "60000"))


class BaseJobDriver:
    """统一入口：入队 + 取消 + 消费循环。"""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.runner = run_job

    async def enqueue(self, job_id: str, job_type: str) -> None:
        raise NotImplementedError

    async def cancel(self, job_id: str) -> None:
        await self.store.update_job(job_id, status="CANCELED", error="任务已取消")
        await self.store.add_event(job_id, "job.canceled", "summary", 0, {})

    async def run_consumer(self, consumer_name: str) -> None:
        raise NotImplementedError


class InProcessDriver(BaseJobDriver):
    """本地开发：直接在当前进程调度，任务存续于 asyncio 事件循环。"""

    def __init__(self, store: Any) -> None:
        super().__init__(store)
        self.running: dict[str, asyncio.Task] = {}

    async def enqueue(self, job_id: str, job_type: str) -> None:
        task = asyncio.create_task(self._run(job_id), name=f"ppt-job-{job_id}")
        self.running[job_id] = task

    async def _run(self, job_id: str) -> None:
        try:
            await self.runner(job_id, self.store)
        except Exception:  # noqa: BLE001 - 任务级兜底，避免协程异常泄漏
            logger.exception("in-process job failed job_id=%s", job_id)
        finally:
            self.running.pop(job_id, None)

    async def cancel(self, job_id: str) -> None:
        # 先落库 CANCELED（单一事实来源），再取消运行中的任务加速停止。
        await super().cancel(job_id)
        task = self.running.get(job_id)
        if task and not task.done():
            task.cancel()

    async def run_consumer(self, consumer_name: str) -> None:
        # 进程内没有独立 worker，永远挂起即可（保持与 Redis 版本接口一致）
        await asyncio.Event().wait()


class RedisStreamDriver(BaseJobDriver):
    """生产：Redis Stream + consumer group。"""

    def __init__(self, store: Any, redis_url: str, batch: int = 4) -> None:
        super().__init__(store)
        import redis.asyncio as aioredis

        password = os.environ.get("REDIS_PASSWORD", "").strip() or None
        self.redis = aioredis.from_url(
            redis_url, password=password, decode_responses=True, health_check_interval=30
        )
        self.batch = batch

    async def enqueue(self, job_id: str, job_type: str) -> None:
        await self.redis.xadd(STREAM_NAME, {"job_id": job_id, "type": job_type})

    async def _ensure_group(self) -> None:
        try:
            await self.redis.xgroup_create(STREAM_NAME, GROUP_NAME, id="$", mkstream=True)
        except Exception as exc:  # BUSYGROUP 表示已存在
            if "BUSYGROUP" not in str(exc):
                raise

    async def _ack(self, message_id: str) -> None:
        await self.redis.xack(STREAM_NAME, GROUP_NAME, message_id)

    async def _handle(self, message_id: str, fields: dict[str, str]) -> None:
        job_id = fields.get("job_id", "")
        job_type = fields.get("type", "")
        if not job_id:
            await self._ack(message_id)
            return

        job = await self.store.get_job_by_id(job_id)
        if not job or job["status"] in ("COMPLETED", "FAILED", "CANCELED"):
            await self._ack(message_id)
            return

        retry_count = (job.get("retry_count") or 0) + 1
        if retry_count > MAX_DELIVERY_COUNT:
            await self.store.update_job(job_id, status="FAILED", error="超过最大投递次数")
            await self.store.add_event(job_id, "job.failed", "summary", 0, {"error": "超过最大投递次数"})
            await self.redis.xadd(DEAD_LETTER_STREAM, {"job_id": job_id, "type": job_type, "reason": "max_delivery"})
            await self._ack(message_id)
            return

        await self.store.update_job(job_id, retry_count=retry_count)
        try:
            await self.runner(job_id, self.store)
            await self._ack(message_id)
        except Exception:  # noqa: BLE001 - 不 ack，留给 XAUTOCLAIM 重投
            logger.exception("redis job handler crashed job_id=%s", job_id)

    async def _reclaim_pending(self) -> None:
        cursor = "0-0"
        while True:
            cursor, messages, _deleted = await self.redis.xautoclaim(
                STREAM_NAME,
                GROUP_NAME,
                self._consumer,
                min_idle_time=RECLAIM_IDLE_MS,
                start_id=cursor,
                count=self.batch,
            )
            for message_id, fields in messages:
                await self._handle(message_id, fields)
            if cursor == "0-0":
                break

    async def run_consumer(self, consumer_name: str) -> None:
        self._consumer = consumer_name
        await self._ensure_group()
        while True:
            try:
                await self._reclaim_pending()
                messages = await self.redis.xreadgroup(
                    groupname=GROUP_NAME,
                    consumername=consumer_name,
                    streams={STREAM_NAME: ">"},
                    count=self.batch,
                    block=2000,
                )
                for _stream, entries in messages:
                    for message_id, fields in entries:
                        await self._handle(message_id, fields)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 消费循环异常继续，避免 worker 崩溃
                logger.exception("redis consume loop error")
                await asyncio.sleep(1)

    async def close(self) -> None:
        await self.redis.aclose()


def make_job_queue(store: Any) -> BaseJobDriver:
    """根据环境选择驱动。无 REDIS_URL → 进程内；有 → Redis Stream。"""
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        logger.info("using RedisStreamDriver")
        return RedisStreamDriver(store, redis_url)
    logger.info("using InProcessDriver")
    return InProcessDriver(store)


def make_consumer_name() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
