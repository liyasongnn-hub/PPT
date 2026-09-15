"""异步 PlatformStore —— 用 SQLAlchemy 2.0 替换旧的同步 sqlite3 实现。

方法签名与旧 ``platform_store.py`` 保持一致，但全部改为 ``async``；数据通过
``db.get_session()`` 访问，因此同一套代码可跑在 PostgreSQL 或 SQLite 上。
时间戳在入库时用 ``TIMESTAMPTZ``，出库时统一转成 ``YYYY-MM-DDTHH:MM:SSZ`` 字符串，
保持与旧 API 兼容（前端 ``new Date(...)`` 可直接解析）。
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from db import get_session
from models import FileObject, Job, JobEvent, Project, User, utcnow
from security import hash_password, verify_password


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PlatformStore:
    """无状态的异步数据访问层，所有方法通过连接池取会话。"""

    # ---- converters -----------------------------------------------------
    @staticmethod
    def _user(u: User) -> dict[str, Any]:
        return {
            "id": u.id,
            "email": u.email,
            "tenant_id": u.tenant_id,
            "display_name": u.display_name,
            "created_at": _iso(u.created_at),
        }

    @staticmethod
    def _project(p: Project) -> dict[str, Any]:
        return {
            "id": p.id,
            "tenant_id": p.tenant_id,
            "owner_id": p.owner_id,
            "title": p.title,
            "prompt": p.prompt,
            "config": p.config or {},
            "status": p.status,
            "outline": p.outline or "",
            "document": p.document or {},
            "created_at": _iso(p.created_at),
            "updated_at": _iso(p.updated_at),
        }

    @staticmethod
    def _job(j: Job) -> dict[str, Any]:
        return {
            "id": j.id,
            "project_id": j.project_id,
            "owner_id": j.owner_id,
            "type": j.type,
            "status": j.status,
            "progress": j.progress,
            "result": j.result or {},
            "error": j.error or "",
            "retry_count": j.retry_count,
            "heartbeat_at": _iso(j.heartbeat_at),
            "cost_usd": j.cost_usd,
            "tokens_input": j.tokens_input,
            "tokens_output": j.tokens_output,
            "created_at": _iso(j.created_at),
            "updated_at": _iso(j.updated_at),
        }

    @staticmethod
    def _event(e: JobEvent) -> dict[str, Any]:
        return {
            "id": e.id,
            "job_id": e.job_id,
            "event_type": e.event_type,
            "stage": e.stage,
            "progress": e.progress,
            "payload": e.payload or {},
            "created_at": _iso(e.created_at),
        }

    @staticmethod
    def _file(f: FileObject) -> dict[str, Any]:
        return {
            "id": f.id,
            "project_id": f.project_id,
            "owner_id": f.owner_id,
            "kind": f.kind,
            "bucket": f.bucket,
            "object_key": f.object_key,
            "filename": f.filename,
            "content_type": f.content_type,
            "size": f.size,
            "sha256": f.sha256,
            "created_at": _iso(f.created_at),
        }

    # ---- users ----------------------------------------------------------
    async def create_user(self, email: str, password: str, display_name: str) -> dict[str, Any]:
        email = email.strip().lower()
        if len(email) > 160 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
            raise ValueError("邮箱格式不正确")
        if len(password) < 8:
            raise ValueError("密码至少需要 8 位")
        user = User(
            email=email,
            password_hash=hash_password(password),
            tenant_id=f"tenant_{secrets.token_hex(6)}",
            display_name=display_name.strip() or email,
        )
        async with get_session() as session:
            session.add(user)
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError("邮箱已注册") from exc
            await session.commit()
            return self._user(user)

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        async with get_session() as session:
            user = await session.get(User, int(user_id))
            return self._user(user) if user else None

    async def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        async with get_session() as session:
            result = await session.execute(select(User).where(User.email == email.strip().lower()))
            user = result.scalar_one_or_none()
            if not user or not verify_password(password, user.password_hash):
                return None
            return self._user(user)

    # ---- projects -------------------------------------------------------
    async def create_project(self, owner_id: int, tenant_id: str, title: str, prompt: str, config: dict[str, Any]) -> dict[str, Any]:
        project = Project(
            id=secrets.token_urlsafe(12),
            tenant_id=tenant_id,
            owner_id=owner_id,
            title=title.strip() or "未命名演示文稿",
            prompt=prompt.strip(),
            config=config or {},
            status="DRAFT",
        )
        async with get_session() as session:
            session.add(project)
            await session.commit()
            return self._project(project)

    async def list_projects(self, owner_id: int) -> list[dict[str, Any]]:
        async with get_session() as session:
            result = await session.execute(
                select(Project).where(Project.owner_id == owner_id).order_by(Project.updated_at.desc())
            )
            return [self._project(p) for p in result.scalars()]

    async def get_project(self, project_id: str, owner_id: int) -> dict[str, Any] | None:
        async with get_session() as session:
            project = await session.get(Project, project_id)
            if not project or project.owner_id != owner_id:
                return None
            return self._project(project)

    async def update_project(self, project_id: str, owner_id: int, **changes: Any) -> dict[str, Any] | None:
        allowed = {"title", "prompt", "config", "status", "outline", "document"}
        updates = {k: v for k, v in changes.items() if k in allowed and v is not None}
        async with get_session() as session:
            project = await session.get(Project, project_id)
            if not project or project.owner_id != owner_id:
                return None
            for key, value in updates.items():
                setattr(project, key, value)
            if updates:
                project.updated_at = utcnow()
            await session.commit()
            return self._project(project)

    # ---- jobs -----------------------------------------------------------
    async def create_job(self, project_id: str, owner_id: int, job_type: str) -> dict[str, Any]:
        job = Job(id=secrets.token_urlsafe(12), project_id=project_id, owner_id=owner_id, type=job_type, status="QUEUED")
        async with get_session() as session:
            session.add(job)
            await session.flush()
            session.add(JobEvent(job_id=job.id, event_type="job.queued", stage="decision", progress=0, payload={}))
            await session.commit()
            return self._job(job)

    async def get_job(self, job_id: str, owner_id: int) -> dict[str, Any] | None:
        async with get_session() as session:
            job = await session.get(Job, job_id)
            if not job or job.owner_id != owner_id:
                return None
            return self._job(job)

    async def get_job_by_id(self, job_id: str) -> dict[str, Any] | None:
        """按 ID 取任务（不校验归属）。仅供 worker/队列层内部使用。"""
        async with get_session() as session:
            job = await session.get(Job, job_id)
            return self._job(job) if job else None

    async def get_active_job(self, project_id: str, owner_id: int, job_type: str) -> dict[str, Any] | None:
        async with get_session() as session:
            result = await session.execute(
                select(Job)
                .where(
                    Job.project_id == project_id,
                    Job.owner_id == owner_id,
                    Job.type == job_type,
                    Job.status.in_(("QUEUED", "RUNNING")),
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            job = result.scalar_one_or_none()
            return self._job(job) if job else None

    async def update_job(self, job_id: str, **changes: Any) -> None:
        allowed = {"status", "progress", "result", "error", "retry_count", "heartbeat_at", "cost_usd", "tokens_input", "tokens_output"}
        updates = {k: v for k, v in changes.items() if k in allowed and v is not None}
        if not updates:
            return
        updates["updated_at"] = utcnow()
        async with get_session() as session:
            await session.execute(update(Job).where(Job.id == job_id).values(**updates))
            await session.commit()

    async def add_event(self, job_id: str, event_type: str, stage: str, progress: int, payload: dict[str, Any]) -> int:
        event = JobEvent(
            job_id=job_id,
            event_type=event_type,
            stage=stage,
            progress=max(0, min(100, int(progress))),
            payload=payload or {},
        )
        async with get_session() as session:
            session.add(event)
            await session.commit()
            return event.id

    async def events_after(self, job_id: str, event_id: int = 0) -> list[dict[str, Any]]:
        async with get_session() as session:
            result = await session.execute(
                select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > event_id).order_by(JobEvent.id)
            )
            return [self._event(e) for e in result.scalars()]

    # ---- job lifecycle helpers (scheduler / startup) --------------------
    async def mark_interrupted_jobs_failed(self) -> int:
        """单进程模式启动时调用：内存任务无法存活，把 QUEUED/RUNNING 显式置为 FAILED。"""
        async with get_session() as session:
            result = await session.execute(
                update(Job)
                .where(Job.status.in_(("QUEUED", "RUNNING")))
                .values(status="FAILED", error="服务重启导致任务中断，请重新生成", updated_at=utcnow())
            )
            await session.commit()
            return result.rowcount or 0

    async def fail_stale_running_jobs(self, stale_seconds: int = 120) -> int:
        """把心跳超时的 RUNNING 任务置为 FAILED（多实例下 Worker 崩溃恢复）。"""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
        async with get_session() as session:
            result = await session.execute(
                update(Job)
                .where(Job.status == "RUNNING", or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < cutoff))
                .values(status="FAILED", error="任务执行中断（Worker 心跳丢失）", updated_at=utcnow())
            )
            await session.commit()
            return result.rowcount or 0

    async def fail_stale_queued_jobs(self, stale_seconds: int = 600) -> int:
        """把长时间未被消费的 QUEUED 任务置为 FAILED。"""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)
        async with get_session() as session:
            result = await session.execute(
                update(Job)
                .where(Job.status == "QUEUED", Job.created_at < cutoff)
                .values(status="FAILED", error="任务排队超时，请重新生成", updated_at=utcnow())
            )
            await session.commit()
            return result.rowcount or 0

    async def purge_old_jobs(self, days: int = 14) -> int:
        """清理超过保留期的已完成任务及其事件（保留活跃任务）。"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        async with get_session() as session:
            old_job_ids = (
                await session.execute(
                    select(Job.id).where(Job.status.in_(("COMPLETED", "FAILED", "CANCELED")), Job.updated_at < cutoff)
                )
            ).scalars().all()
            if not old_job_ids:
                return 0
            await session.execute(update(JobEvent).where(JobEvent.job_id.in_(old_job_ids)).values(payload={}))
            return len(old_job_ids)

    # ---- files ----------------------------------------------------------
    async def create_file(
        self,
        owner_id: int,
        kind: str,
        bucket: str,
        object_key: str,
        filename: str,
        content_type: str = "application/octet-stream",
        size: int = 0,
        sha256: str = "",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        f = FileObject(
            id=secrets.token_urlsafe(12),
            project_id=project_id,
            owner_id=owner_id,
            kind=kind,
            bucket=bucket,
            object_key=object_key,
            filename=filename or "file",
            content_type=content_type,
            size=size,
            sha256=sha256,
        )
        async with get_session() as session:
            session.add(f)
            await session.commit()
            return self._file(f)

    async def get_file(self, file_id: str, owner_id: int) -> dict[str, Any] | None:
        async with get_session() as session:
            f = await session.get(FileObject, file_id)
            if not f or f.owner_id != owner_id:
                return None
            return self._file(f)

    async def list_files(self, owner_id: int, project_id: str | None = None) -> list[dict[str, Any]]:
        async with get_session() as session:
            stmt = select(FileObject).where(FileObject.owner_id == owner_id)
            if project_id:
                stmt = stmt.where(FileObject.project_id == project_id)
            stmt = stmt.order_by(FileObject.created_at.desc())
            result = await session.execute(stmt)
            return [self._file(f) for f in result.scalars()]

    async def delete_file(self, file_id: str, owner_id: int) -> dict[str, Any] | None:
        async with get_session() as session:
            f = await session.get(FileObject, file_id)
            if not f or f.owner_id != owner_id:
                return None
            snapshot = self._file(f)
            await session.delete(f)
            await session.commit()
            return snapshot
