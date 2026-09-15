"""共享的任务执行器：大纲生成与内容生成。

``main_api`` 与 ``worker`` 共用这份代码：本地无 Redis 时由 ``InProcessDriver``
在进程内 ``asyncio.create_task`` 调度，生产有 Redis 时由 ``worker.py`` 消费后调用
同一个入口 ``run_job``。执行结果（进度/事件/状态）全部直写数据库，因此
``main_api`` 的 SSE 端点只读 DB，天然支持多实例。

约定的事件流：
- 大纲：``outline.progress``（payload.text 为增量文本）、``job.started``、``job.completed``/``job.failed``
- 内容：``slide.progress``（payload.text 为单页 slide JSON）、``job.started``、``job.completed``/``job.failed``
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from content_client import A2AContentClientWrapper
from models import utcnow
from outline_client import A2AOutlineClientWrapper

logger = logging.getLogger(__name__)

# 与 main.py 保持一致的 env 解析（worker 复用同一镜像/环境变量）。
_project_root = Path(__file__).parent.parent.parent
try:
    import dotenv

    dotenv.load_dotenv(_project_root / ".env")
except Exception:  # pragma: no cover - dotenv 可选
    pass


def _default(env: str, fallback: str) -> str:
    return os.environ.get(env, fallback)


HOST = _default("HOST", "127.0.0.1")
OUTLINE_API = _default("OUTLINE_API", f"http://{HOST}:{_default('OUTLINE_API_PORT', '10001')}")
CONTENT_API = _default("CONTENT_API", f"http://{HOST}:{_default('CONTENT_API_PORT', '10011')}")


class JobCancelled(Exception):
    """任务被用户取消或服务中止。"""


def _extract_markdown(text: str) -> str:
    """从大纲文本里截取第一个 Markdown 标题之后的内容（内容生成以此为输入）。"""
    match = re.search(r"(# .*)", text or "", flags=re.DOTALL)
    return text[match.start():] if match else text


async def _heartbeat_and_cancel_watchdog(job_id: str, store, main_task: asyncio.Task) -> None:
    """周期更新心跳；发现任务被标记取消时，取消正在执行的主协程。"""
    try:
        while True:
            await asyncio.sleep(5)
            job = await store.get_job_by_id(job_id)
            if job is None:
                return
            if job["status"] == "CANCELED":
                main_task.cancel()
                return
            await store.update_job(job_id, heartbeat_at=utcnow())
    except asyncio.CancelledError:
        pass


async def _run_with_watchdog(job_id: str, store, coro) -> None:
    """让生成协程在心跳/取消看门狗的监护下运行。

    取消语义：任务被取消时（``queue.cancel`` 已先落库 CANCELED，看门狗据此取消主协程），
    这里吞掉 ``CancelledError``，避免取消信号级联到外层消费循环。
    """
    main_task = asyncio.create_task(coro)
    watchdog = asyncio.create_task(_heartbeat_and_cancel_watchdog(job_id, store, main_task))
    try:
        try:
            await main_task
        except asyncio.CancelledError:
            pass
    finally:
        watchdog.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass


async def _stream_outline_text(prompt: str, language: str) -> AsyncIterator[str]:
    outline_wrapper = A2AOutlineClientWrapper(session_id=uuid.uuid4().hex, agent_url=OUTLINE_API)
    async for chunk_data in outline_wrapper.generate(prompt, language=language):
        if chunk_data["type"] == "text":
            yield chunk_data["text"]


async def run_outline_job(job_id: str, project: dict, user_id: int, store) -> None:
    await store.update_job(job_id, status="RUNNING", progress=5, heartbeat_at=utcnow())
    await store.add_event(job_id, "job.started", "decision", 5, {"message": "已开始生成大纲"})

    async def _run() -> None:
        outline = ""
        try:
            await store.add_event(job_id, "outline.started", "planning", 15, {"message": "大纲 Agent 处理中"})
            async for chunk in _stream_outline_text(project["prompt"], project["config"].get("language", "中文")):
                outline += chunk
                progress = min(85, 15 + len(outline) // 120)
                await store.update_job(job_id, progress=progress)
                await store.add_event(job_id, "outline.progress", "planning", progress, {"text": chunk})
            if not outline.strip():
                raise RuntimeError("AI 服务未返回大纲内容")
            await store.update_project(project["id"], user_id, outline=outline, status="OUTLINE_REVIEW")
            await store.update_job(job_id, status="COMPLETED", progress=100, result={"outline": outline})
            await store.add_event(job_id, "job.completed", "summary", 100, {"outline_length": len(outline)})
        except Exception as exc:  # noqa: BLE001 - 兜底记录后由队列层决定是否重试
            logger.exception("outline job failed job_id=%s", job_id)
            await store.update_job(job_id, status="FAILED", error=str(exc)[:500])
            await store.add_event(job_id, "job.failed", "summary", 0, {"error": str(exc)[:500]})

    await _run_with_watchdog(job_id, store, _run())


async def _stream_content_slides(markdown: str, language: str, search_engines: list[str], user_id: int) -> AsyncIterator[dict]:
    """流式产出内容生成的 slide JSON（text 类型）与 references（metadata 类型）。"""
    content_wrapper = A2AContentClientWrapper(session_id=uuid.uuid4().hex, agent_url=CONTENT_API)
    metadata = {"user_id": user_id, "search_engine": search_engines, "language": language}
    async for chunk_data in content_wrapper.generate(user_question=markdown, metadata=metadata):
        kind = chunk_data.get("type")
        if kind == "text":
            yield {"kind": "slide", "text": chunk_data["text"]}
        elif kind == "metadata":
            yield {"kind": "metadata", "metadata": chunk_data.get("metadata", {})}
        elif kind == "final":
            break


async def run_content_job(job_id: str, project: dict, user_id: int, store, params: dict) -> None:
    language = project["config"].get("language", "zh")
    search_engines: list[str] = []
    if params.get("generate_from_uploaded_file"):
        search_engines.append("KnowledgeBaseSearch")
    if params.get("generate_from_web_search", True):
        search_engines.append("DocumentSearch")

    markdown = _extract_markdown(project.get("outline", ""))
    if not markdown.strip():
        await store.update_job(job_id, status="FAILED", error="项目还没有内容大纲，无法生成 PPT")
        await store.add_event(job_id, "job.failed", "summary", 0, {"error": "缺少大纲"})
        return

    await store.update_job(job_id, status="RUNNING", progress=10, heartbeat_at=utcnow())
    await store.add_event(job_id, "job.started", "decision", 10, {"message": "已开始生成 PPT 内容"})

    async def _run() -> None:
        slides: list[str] = []
        try:
            await store.add_event(job_id, "content.started", "content", 15, {"message": "内容 Agent 处理中"})
            async for item in _stream_content_slides(markdown, language, search_engines, user_id):
                if item["kind"] == "slide":
                    slides.append(item["text"])
                    progress = min(95, 15 + len(slides) * 5)
                    await store.update_job(job_id, progress=progress)
                    await store.add_event(
                        job_id, "slide.progress", "content", progress,
                        {"text": item["text"], "index": len(slides) - 1},
                    )
                elif item["kind"] == "metadata":
                    await store.add_event(job_id, "content.metadata", "content", 0, {"references": item["metadata"]})

            if not slides:
                raise RuntimeError("AI 服务未生成任何页面，请返回第一步重新生成大纲")
            await store.update_job(job_id, status="COMPLETED", progress=100, result={"slides": slides, "count": len(slides)})
            await store.add_event(job_id, "job.completed", "summary", 100, {"slide_count": len(slides)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("content job failed job_id=%s", job_id)
            await store.update_job(job_id, status="FAILED", error=str(exc)[:500])
            await store.add_event(job_id, "job.failed", "summary", 0, {"error": str(exc)[:500]})
            # 让项目从 GENERATING 回到可重试状态，避免前端崩溃后卡死
            await store.update_project(project["id"], user_id, status="OUTLINE_REVIEW")

    await _run_with_watchdog(job_id, store, _run())


async def run_job(job_id: str, store) -> None:
    """队列消费入口：幂等 + 按类型分发。返回前保证任务处于终态。"""
    job = await store.get_job_by_id(job_id)
    if not job:
        return
    if job["status"] in ("COMPLETED", "FAILED", "CANCELED"):
        return  # 幂等：重复投递直接跳过

    project = await store.get_project(job["project_id"], job["owner_id"])
    if not project:
        await store.update_job(job_id, status="FAILED", error="项目不存在或已被删除")
        return

    if job["type"] == "OUTLINE":
        await run_outline_job(job_id, project, job["owner_id"], store)
    elif job["type"] == "CONTENT":
        await run_content_job(job_id, project, job["owner_id"], store, job["result"].get("params", {}))
    else:
        await store.update_job(job_id, status="FAILED", error=f"未知任务类型 {job['type']}")
