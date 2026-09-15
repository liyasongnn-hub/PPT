import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import dotenv
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from content_client import A2AContentClientWrapper
from db import close_engine, create_all, init_engine, is_postgres
from job_queue import InProcessDriver, make_job_queue
from outline_client import A2AOutlineClientWrapper
from security import create_token, decode_token
from storage import EXPORT_BUCKET, FILE_SIGNED_URL_TTL, UPLOAD_BUCKET, make_storage
from store import PlatformStore

logger = logging.getLogger(__name__)
dotenv.load_dotenv()

# 加载统一环境配置
project_root = Path(__file__).parent.parent.parent
env_file = project_root / ".env"
if env_file.exists():
    dotenv.load_dotenv(env_file)
else:
    dotenv.load_dotenv()

OUTLINE_API = os.environ.get("OUTLINE_API", f"http://{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('OUTLINE_API_PORT', '10001')}")
CONTENT_API = os.environ.get("CONTENT_API", f"http://{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('CONTENT_API_PORT', '10011')}")
PERSONAL_DB = os.environ.get("PERSONAL_DB", f"http://{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('PERSONALDB_PORT', '9100')}")
PERSONALDB_INTERNAL_TOKEN = os.environ.get("PERSONALDB_INTERNAL_TOKEN", "").strip()
_INSECURE_JWT_SECRETS = frozenset({
    "change-this-local-secret",
    "local-change-me-before-production",
    "change-me",
    "changeme",
    "secret",
    "test-secret",
})


def load_jwt_secret() -> str:
    """读取并校验 JWT 密钥。不合格时拒绝启动，而不是静默退回默认值。"""
    secret = os.environ.get("JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError(
            "JWT_SECRET 未配置。生成一个随机密钥后重试：\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"'
        )
    if secret in _INSECURE_JWT_SECRETS or secret.lower().startswith(("replace", "your_")):
        raise RuntimeError("JWT_SECRET 仍是模板里的占位符，请替换为真实随机密钥。")
    if len(secret) < 32:
        raise RuntimeError(f"JWT_SECRET 长度仅 {len(secret)} 位，至少需要 32 位。")
    return secret


JWT_SECRET = load_jwt_secret()
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
MAX_EXPORT_BYTES = int(os.environ.get("MAX_EXPORT_BYTES", str(50 * 1024 * 1024)))
MAX_PROJECT_DOCUMENT_BYTES = int(os.environ.get("MAX_PROJECT_DOCUMENT_BYTES", str(15 * 1024 * 1024)))
MAX_PROXY_BYTES = int(os.environ.get("MAX_PROXY_BYTES", str(15 * 1024 * 1024)))
DATA_DIR = Path(os.environ.get("PLATFORM_DATA_DIR", str(project_root / "data"))).resolve()
store = PlatformStore()
queue = make_job_queue(store)
storage = make_storage(DATA_DIR)

# 反向代理后需要信任 X-Forwarded-For 才能拿到真实客户端 IP。默认关闭：
# 直连时信任该头部等于让攻击者随意伪造 IP 绕过限流。
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "false").lower() in ("1", "true", "yes")


def client_ip(request: Request) -> str:
    if TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            # 取最右一跳（由最近的代理写入），避免客户端伪造前缀绕过限流
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class SlidingWindowLimiter:
    """进程内滑动窗口限流器。

    单容器部署足够。一旦横向扩展到多实例，必须换成 Redis 等共享存储实现，
    否则每个实例各算一份配额，限流形同虚设。

    检查与记录分离：调用方可以先 retry_after() 判断，再决定是否 record()，
    这样"只统计失败尝试"这类策略才有表达空间。
    """

    def __init__(self, limit: int, window_seconds: int, max_keys: int = 20_000):
        self._limit = limit
        self._window = window_seconds
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        if len(self._hits) > self._max_keys:
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] <= self._window}

    def _bucket(self, key: str, now: float) -> deque[float]:
        bucket = self._hits.setdefault(key, deque())
        while bucket and now - bucket[0] > self._window:
            bucket.popleft()
        return bucket

    def retry_after(self, key: str) -> int:
        """只检查是否已超限，不记录。返回 0 表示放行，否则为需等待的秒数。"""
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            bucket = self._bucket(key, now)
            if len(bucket) >= self._limit:
                return max(1, int(self._window - (now - bucket[0])) + 1)
            return 0

    def record(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._bucket(key, now).append(now)


# 登录/注册限流：同一 IP 与同一邮箱各自独立计数，压制密码爆破。
AUTH_IP_LIMITER = SlidingWindowLimiter(limit=10, window_seconds=300)
AUTH_ACCOUNT_LIMITER = SlidingWindowLimiter(limit=5, window_seconds=300)
try:
    generation_requests_per_hour = max(1, int(os.environ.get("GENERATION_REQUESTS_PER_HOUR", "60")))
except ValueError:
    generation_requests_per_hour = 60
GENERATION_USER_LIMITER = SlidingWindowLimiter(
    limit=generation_requests_per_hour,
    window_seconds=3600,
)


def _auth_limit_targets(request: Request, email: str):
    return (
        (f"ip:{client_ip(request)}", AUTH_IP_LIMITER),
        (f"acct:{email.strip().lower()}", AUTH_ACCOUNT_LIMITER),
    )


def enforce_auth_rate_limit(request: Request, email: str) -> None:
    for key, limiter in _auth_limit_targets(request, email):
        retry_after = limiter.retry_after(key)
        if retry_after:
            raise HTTPException(
                status_code=429,
                detail=f"尝试过于频繁，请 {retry_after} 秒后再试",
                headers={"Retry-After": str(retry_after)},
            )


def record_auth_attempt(request: Request, email: str) -> None:
    for key, limiter in _auth_limit_targets(request, email):
        limiter.record(key)


def enforce_generation_rate_limit(user_id: int) -> None:
    key = f"user:{user_id}"
    retry_after = GENERATION_USER_LIMITER.retry_after(key)
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail=f"AI 生成请求过于频繁，请 {retry_after} 秒后再试",
            headers={"Retry-After": str(retry_after)},
        )
    GENERATION_USER_LIMITER.record(key)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_engine(os.environ.get("DATABASE_URL") or None, DATA_DIR)
    if not is_postgres():
        # SQLite（本地开发）：按模型元数据自动建表
        await create_all()
    await storage.ensure_buckets([EXPORT_BUCKET, UPLOAD_BUCKET])
    if isinstance(queue, InProcessDriver):
        # 单进程模式：内存任务无法跨重启存活，显式置为失败；Redis 模式由 scheduler 负责故障转移
        await store.mark_interrupted_jobs_failed()
    yield
    close = getattr(queue, "close", None)
    if close is not None:
        await close()
    await storage.close()
    await close_engine()


IS_PRODUCTION = os.environ.get("APP_ENV", "development").strip().lower() == "production"
app = FastAPI(
    title="PPT Agent Platform API",
    version="1.0.0",
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
    lifespan=lifespan,
)

# Allow CORS for the frontend development server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.environ.get("CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173").split(",") if origin.strip()],
    allow_credentials=True,
    # 收紧到实际用到的动词与请求头，不再使用通配符
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Trace-Id"],
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    request.state.trace_id = trace_id
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request failed trace_id=%s path=%s", trace_id, request.url.path)
        raise
    response.headers["X-Trace-Id"] = trace_id
    return response

class AipptRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20_000)
    language: str = Field(default="中文", max_length=32)
    model: str = Field(default="", max_length=120)
    stream: bool

async def stream_agent_response(prompt: str, language: str = "chinese"):
    """A generator that yields parts of the agent response."""
    outline_wrapper = A2AOutlineClientWrapper(session_id=uuid.uuid4().hex, agent_url=OUTLINE_API)
    async for chunk_data in outline_wrapper.generate(prompt, language=language):
        if chunk_data["type"] == "text":
            yield chunk_data["text"]


class AuthPayload(BaseModel):
    email: str = Field(..., min_length=5, max_length=160)
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=80)


class ProjectCreatePayload(BaseModel):
    title: str = Field(default="未命名演示文稿", max_length=160)
    prompt: str = Field(..., min_length=1, max_length=20_000)
    language: str = Field(default="中文", max_length=32)
    page_count: int = Field(default=10, ge=1, le=100)
    style: str = Field(default="商务简洁", max_length=80)
    aspect_ratio: str = Field(default="16:9", max_length=16)
    template_id: str = Field(default="template_1", max_length=80)


class ProjectPatchPayload(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    prompt: str | None = Field(default=None, max_length=20_000)
    outline: str | None = Field(default=None, max_length=100_000)
    status: str | None = Field(default=None, max_length=40)
    config: dict[str, Any] | None = None
    document: dict[str, Any] | None = None


def personaldb_headers() -> dict[str, str]:
    return {"X-Internal-Token": PERSONALDB_INTERNAL_TOKEN} if PERSONALDB_INTERNAL_TOKEN else {}


async def current_user(request: Request) -> dict:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="需要 Bearer 认证凭证")
    payload = decode_token(authorization.split(" ", 1)[1], JWT_SECRET)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="认证凭证无效或已过期")
    user = await store.get_user(int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


def auth_response(user: dict) -> dict:
    return {
        "user": user,
        "access_token": create_token(user, JWT_SECRET, "access", 3600),
        "refresh_token": create_token(user, JWT_SECRET, "refresh", 30 * 24 * 3600),
        "token_type": "bearer",
    }


@app.post("/api/v1/auth/register", tags=["auth"])
async def register(payload: AuthPayload, request: Request):
    enforce_auth_rate_limit(request, payload.email)
    # 注册成功与否都计数：被滥用的正是"不断尝试注册"这个行为
    record_auth_attempt(request, payload.email)
    try:
        user = await store.create_user(payload.email, payload.password, payload.display_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return auth_response(user)


@app.post("/api/v1/auth/login", tags=["auth"])
async def login(payload: AuthPayload, request: Request):
    enforce_auth_rate_limit(request, payload.email)
    user = await store.authenticate(payload.email, payload.password)
    if not user:
        # 只统计失败：否则正常用户换个设备登录几次就会被自己锁死
        record_auth_attempt(request, payload.email)
        raise HTTPException(status_code=401, detail="邮箱或密码错误")
    return auth_response(user)


class RefreshPayload(BaseModel):
    refresh_token: str = Field(..., min_length=20)


@app.post("/api/v1/auth/refresh", tags=["auth"])
async def refresh(payload: RefreshPayload):
    token_data = decode_token(payload.refresh_token, JWT_SECRET)
    if not token_data or token_data.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="刷新凭证无效或已过期")
    user = await store.get_user(int(token_data["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return auth_response(user)


@app.get("/api/v1/me", tags=["auth"])
async def me(request: Request):
    return await current_user(request)


@app.get("/api/v1/projects", tags=["projects"])
async def list_projects(request: Request):
    user = await current_user(request)
    return {"data": await store.list_projects(user["id"])}


@app.post("/api/v1/projects", tags=["projects"])
async def create_project(payload: ProjectCreatePayload, request: Request):
    user = await current_user(request)
    config = {
        "language": payload.language,
        "page_count": payload.page_count,
        "style": payload.style,
        "aspect_ratio": payload.aspect_ratio,
        "template_id": payload.template_id,
    }
    return await store.create_project(user["id"], user["tenant_id"], payload.title, payload.prompt, config)


@app.get("/api/v1/projects/{project_id}", tags=["projects"])
async def get_project(project_id: str, request: Request):
    user = await current_user(request)
    project = await store.get_project(project_id, user["id"])
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@app.patch("/api/v1/projects/{project_id}", tags=["projects"])
async def patch_project(project_id: str, payload: ProjectPatchPayload, request: Request):
    user = await current_user(request)
    changes = payload.model_dump(exclude_none=True)
    if "outline" in changes:
        changes["status"] = changes.get("status", "OUTLINE_REVIEW")
    if "document" in changes:
        document_json = json.dumps(changes["document"], ensure_ascii=False, separators=(",", ":"))
        if len(document_json.encode("utf-8")) > MAX_PROJECT_DOCUMENT_BYTES:
            raise HTTPException(status_code=413, detail="项目文档超过保存大小限制")
    project = await store.update_project(project_id, user["id"], **changes)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


class ContentGeneratePayload(BaseModel):
    generate_from_uploaded_file: bool = False
    generate_from_web_search: bool = True


@app.post("/api/v1/projects/{project_id}/outline:generate", tags=["jobs"])
async def generate_outline_job(project_id: str, request: Request):
    user = await current_user(request)
    project = await store.get_project(project_id, user["id"])
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    active_job = await store.get_active_job(project_id, user["id"], "OUTLINE")
    if active_job:
        return active_job
    enforce_generation_rate_limit(user["id"])
    job = await store.create_job(project_id, user["id"], "OUTLINE")
    await queue.enqueue(job["id"], "OUTLINE")
    return job


@app.post("/api/v1/projects/{project_id}/content:generate", tags=["jobs"])
async def generate_content_job(project_id: str, payload: ContentGeneratePayload, request: Request):
    user = await current_user(request)
    project = await store.get_project(project_id, user["id"])
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not (project.get("outline") or "").strip():
        raise HTTPException(status_code=400, detail="项目还没有内容大纲，请先完成第一步")
    active_job = await store.get_active_job(project_id, user["id"], "CONTENT")
    if active_job:
        return active_job
    enforce_generation_rate_limit(user["id"])
    job = await store.create_job(project_id, user["id"], "CONTENT")
    await store.update_job(job["id"], result={"params": payload.model_dump()})
    await store.update_project(project_id, user["id"], status="GENERATING")
    await queue.enqueue(job["id"], "CONTENT")
    return job


@app.get("/api/v1/jobs/{job_id}", tags=["jobs"])
async def get_job(job_id: str, request: Request):
    user = await current_user(request)
    job = await store.get_job(job_id, user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


@app.get("/api/v1/jobs/{job_id}/events", tags=["jobs"])
async def job_events(job_id: str, request: Request, last_event_id: int = Query(default=0, ge=0)):
    user = await current_user(request)
    if not await store.get_job(job_id, user["id"]):
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        cursor = last_event_id
        while True:
            events = await store.events_after(job_id, cursor)
            for event in events:
                cursor = event["id"]
                yield f"id: {cursor}\nevent: {event['event_type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            job = await store.get_job(job_id, user["id"])
            if job and job["status"] in {"COMPLETED", "FAILED", "CANCELED"} and not events:
                break
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.8)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/v1/jobs/{job_id}/cancel", tags=["jobs"])
async def cancel_job(job_id: str, request: Request):
    user = await current_user(request)
    job = await store.get_job(job_id, user["id"])
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job["status"] not in {"QUEUED", "RUNNING"}:
        return job
    await queue.cancel(job_id)
    return await store.get_job(job_id, user["id"])


@app.post("/api/v1/projects/{project_id}/exports", tags=["files"])
async def upload_export(project_id: str, request: Request, file: UploadFile = File(...)):
    user = await current_user(request)
    project = await store.get_project(project_id, user["id"])
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    data = await file.read(MAX_EXPORT_BYTES + 1)
    if len(data) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail=f"文件大小不能超过 {MAX_EXPORT_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(status_code=400, detail="文件内容为空")
    filename = (file.filename or "export.pptx").replace("\\", "/").rsplit("/", 1)[-1]
    content_type = file.content_type or "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    sha = hashlib.sha256(data).hexdigest()
    object_key = f"{user['id']}/{project_id}/{uuid.uuid4().hex}/{filename}"
    await storage.put_object(EXPORT_BUCKET, object_key, data, content_type)
    record = await store.create_file(
        owner_id=user["id"],
        kind="export",
        bucket=EXPORT_BUCKET,
        object_key=object_key,
        filename=filename,
        content_type=content_type,
        size=len(data),
        sha256=sha,
        project_id=project_id,
    )
    signed = await storage.presign_get(EXPORT_BUCKET, object_key, FILE_SIGNED_URL_TTL)
    return {**record, "download_url": signed or f"/api/v1/files/{record['id']}/download"}


@app.get("/api/v1/projects/{project_id}/files", tags=["files"])
async def list_project_files(project_id: str, request: Request):
    user = await current_user(request)
    if not await store.get_project(project_id, user["id"]):
        raise HTTPException(status_code=404, detail="项目不存在")
    return await store.list_files(user["id"], project_id)


@app.get("/api/v1/files/{file_id}/download", tags=["files"])
async def download_file(file_id: str, request: Request):
    user = await current_user(request)
    record = await store.get_file(file_id, user["id"])
    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")
    signed = await storage.presign_get(record["bucket"], record["object_key"], FILE_SIGNED_URL_TTL)
    if signed:
        return RedirectResponse(signed, status_code=307)
    data = await storage.get_object(record["bucket"], record["object_key"])
    disposition = f'attachment; filename="{record["filename"]}"'
    return Response(content=data, media_type=record["content_type"], headers={"Content-Disposition": disposition})


@app.delete("/api/v1/files/{file_id}", tags=["files"])
async def delete_file(file_id: str, request: Request):
    user = await current_user(request)
    record = await store.get_file(file_id, user["id"])
    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")
    await storage.delete_object(record["bucket"], record["object_key"])
    await store.delete_file(file_id, user["id"])
    return {"status": "deleted"}


@app.post("/api/v1/projects/{project_id}/quality:check", tags=["quality"])
async def quality_check(project_id: str, request: Request):
    user = await current_user(request)
    project = await store.get_project(project_id, user["id"])
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    outline = project.get("outline", "")
    issues = []
    if not outline.strip():
        issues.append({"code": "EMPTY_OUTLINE", "severity": "ERROR", "message": "尚未生成大纲"})
    if len(outline) > 0 and len(outline) < 120:
        issues.append({"code": "THIN_OUTLINE", "severity": "WARN", "message": "大纲内容较少，建议补充页面结构"})
    score = max(0, 100 - sum(35 if i["severity"] == "ERROR" else 10 for i in issues))
    return {"project_id": project_id, "score": score, "status": "PASS" if score >= 80 else "REVIEW", "issues": issues, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


@app.post("/tools/aippt_outline")
async def aippt_outline(payload: AipptRequest, request: Request):
    user = await current_user(request)
    enforce_generation_rate_limit(user["id"])
    if not payload.stream:
        raise HTTPException(status_code=422, detail="只支持流式返回大纲")
    logger.info("收到大纲生成请求 language=%s trace_id=%s", payload.language, request.state.trace_id)
    return StreamingResponse(stream_agent_response(payload.content, payload.language), media_type="text/plain")


class AIWritingRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10_000)
    command: str = Field(..., min_length=1, max_length=32)
    stream: bool = True


@app.post("/tools/ai_writing")
async def ai_writing(payload: AIWritingRequest, request: Request):
    user = await current_user(request)
    enforce_generation_rate_limit(user["id"])
    command_prompts = {
        "美化改写": "润色下方文字，使表达专业、自然且适合演示文稿",
        "扩写丰富": "在不改变事实的前提下扩写下方文字，补足逻辑与细节",
        "精简提炼": "精简下方文字，只保留最重要的信息",
    }
    instruction = command_prompts.get(payload.command)
    if not instruction:
        raise HTTPException(status_code=422, detail="不支持的 AI 改写命令")
    prompt = f"{instruction}。只返回改写后的正文，不要解释，不要使用 Markdown 标题。\n\n原文：\n{payload.content}"
    return StreamingResponse(stream_agent_response(prompt, "中文"), media_type="text/plain")


@app.post("/tools/aippt_outline_from_file")
async def aippt_outline_from_file(
    request: Request,
    user_id: int|str|None = Form(None),
    file: UploadFile = File(None),  # 允许缺省，这样我们可以决定走 file 或 url
    url: str | None = Form(None),
    folder_id: int|str = Form(0),
    file_type: str | None = Form(None),
    language: str = Form("chinese"),  # 添加language参数，默认为chinese
):
    """
    对齐 personaldb 的 /upload/：
    - 必填: userId, fileId
    - 可选: folderId (默认0), fileType
    - file 与 url 互斥，至少一个
    """
    user = await current_user(request)
    enforce_generation_rate_limit(user["id"])
    personaldb_api_url = PERSONAL_DB
    if not personaldb_api_url:
        raise HTTPException(status_code=500, detail="PERSONAL_DB 未配置")

    # 互斥校验（与 personaldb 完全一致）
    has_file = file is not None
    has_url = bool(url and url.strip())

    # 生成 fileId（字符串更稳；personaldb 会 int()）
    file_id = str(int(time.time() * 1000))

    # 推断 fileType（当上传文件时且未显式传入）
    if has_file and not file_type:
        if file.filename and "." in file.filename:
            file_type = file.filename.rsplit(".", 1)[-1]
        else:
            file_type = "unknown"

    # 组装 multipart/form-data
    # 注意：即使是 url 分支，也仍用 multipart，personaldb 也能解析 form
    data = {
        "userId": str(user["id"]),
        "fileId": file_id,
        "folderId": str(folder_id),
    }
    if file_type:
        data["fileType"] = file_type
    if has_url:
        data["url"] = url.strip()

    files_payload = None
    if has_file:
        # 读取一次到内存，httpx 需要 (filename, bytes/obj, content_type)
        file_bytes = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(file_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"文件大小不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        if not file_bytes:
            raise HTTPException(status_code=400, detail="文件内容为空")
        files_payload = {
            "file": (
                file.filename or "uploaded_file",
                file_bytes,
                file.content_type or "application/octet-stream",
            )
        }

    upload_url = f"{personaldb_api_url.rstrip('/')}/upload/"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                upload_url,
                data=data,
                files=files_payload,
                headers=personaldb_headers(),
                timeout=360.0,
            )
            # 不直接 raise，先打日志方便定位
            if resp.status_code >= 400:
                # 打印下游返回体，personaldb 对错误信息写得很清楚
                logger.warning("personaldb upload failed status=%s", resp.status_code)
                resp.raise_for_status()

            # personaldb 的处理函数最终会返回一个 JSON（你上游期望里要有 markdown_content）
            try:
                result = resp.json()
            except ValueError:
                raise HTTPException(status_code=502, detail=f"personaldb 返回的不是 JSON：{resp.text}")

            markdown_content = result.get("markdown_content")
            if markdown_content is None:
                raise HTTPException(status_code=500, detail="personaldb 响应缺少 'markdown_content'")
            logger.info("uploaded source converted language=%s chars=%s", language, len(markdown_content))

            return StreamingResponse(stream_agent_response(markdown_content, language), media_type="text/plain")

        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Request to personaldb timed out.")
        except httpx.HTTPStatusError as exc:
            # 透传 personaldb 的错误详情，便于你在日志里看到具体字段问题
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=500, detail=f"Error connecting to personaldb: {exc}")

class AipptContentRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=100_000)
    language: str = Field(default="zh", max_length=32)
    sessionId: str = Field(default="", max_length=128)
    generateFromUploadedFile: bool = False  # 是否从上传的文件中生成PPT内容
    generateFromWebSearch: bool = True  # 是否从网络搜索中生成PPT内容

async def stream_content_response(markdown_content: str, language, generateFromUploadedFile, generateFromWebSearch, user_id):
    match = re.search(r"(# .*)", markdown_content, flags=re.DOTALL)
    result = markdown_content[match.start():] if match else markdown_content
    logger.info("received outline for content generation chars=%s", len(result))

    content_wrapper = A2AContentClientWrapper(session_id=uuid.uuid4().hex, agent_url=CONTENT_API)

    search_engine = []
    if generateFromUploadedFile:
        search_engine.append("KnowledgeBaseSearch")
    if generateFromWebSearch:
        search_engine.append("DocumentSearch")

    metadata = {"user_id": user_id, "search_engine": search_engine, "language": language}
    logger.info("content generation options user_id=%s search_engines=%s", user_id, search_engine)

    last_flush = asyncio.get_event_loop().time()

    async for chunk_data in content_wrapper.generate(user_question=result, metadata=metadata):
        # 心跳：每15秒发一次注释，避免某些代理断连接
        now = asyncio.get_event_loop().time()
        if now - last_flush > 10:
            yield b": keep-alive\n\n"
            last_flush = now

        if chunk_data.get("type") == "text":
            # 注意：每条 SSE 事件以空行结束
            payload = str(chunk_data["text"])
            encoded_lines = "".join(f"data: {line}\n" for line in payload.splitlines() or [""])
            yield f"{encoded_lines}\n".encode("utf-8")

    # 可选：显式结束信号（前端可据此收尾）
    yield b"data: [DONE]\n\n"

@app.post("/tools/aippt")
async def aippt_content(payload: AipptContentRequest, request: Request):
    user = await current_user(request)
    enforce_generation_rate_limit(user["id"])
    markdown_content = payload.content
    user_id = str(user["id"])

    async def event_generator():
        async for chunk in stream_content_response(
            markdown_content,
            language=payload.language,
            generateFromUploadedFile=payload.generateFromUploadedFile,
            generateFromWebSearch=payload.generateFromWebSearch,
            user_id=user_id
        ):
            yield chunk

    # 关键：SSE 推荐这些头
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

@app.get("/data/{filename}")
async def get_data(filename: str):
    # Templates and cover images live beside this API module.
    template_root = (Path(__file__).parent / "template").resolve()
    candidate = (template_root / filename).resolve()
    if candidate.parent != template_root or candidate.name != filename:
        raise HTTPException(status_code=400, detail="非法文件名")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(candidate)

@app.get("/templates")
async def get_templates():
    templates = [
        { "name": "红色通用", "id": "template_1", "cover": "/api/data/template_1.jpg" },
        { "name": "蓝色通用", "id": "template_2", "cover": "/api/data/template_2.jpg" },
        { "name": "紫色通用", "id": "template_3", "cover": "/api/data/template_3.jpg" },
        { "name": "莫兰迪配色", "id": "template_4", "cover": "/api/data/template_4.jpg" },
        # { "name": "图表", "id": "template_6", "cover": "/api/data/template_6.jpg" },
    ]

    return {"data": templates}

class AipptByIDRequest(BaseModel):
    id: str
    language: str = "chinese"  # 添加language字段，默认为chinese

async def aippt_file_id_streamer(id: str, language: str = "chinese"):
    """根据用户的已有的文件数据中的文件id来生成ppt
    id: 文件的id，例如论文的pmid
    """
    yield json.dumps({"type": "status", "message": "正在解析文件..."}, ensure_ascii=False) + '\n'
    paper_markdown = ""
    if not paper_markdown:
        yield json.dumps({"type": "status", "message": "没有找到该文章"}, ensure_ascii=False) + '\n'
        return
    personaldb_api_url = os.getenv("PERSONAL_DB")
    if not personaldb_api_url:
        raise HTTPException(status_code=500, detail="PERSONAL_DB 未配置")
    # 论文名称
    file_name = f"{id}.md"
    data = {
        "userId": id,
        "fileId": id,
        "folderId": 123,
        "fileType": "txt"
    }
    files = {"file": (file_name, paper_markdown, "text/plain")}
    upload_url = f"{personaldb_api_url.rstrip('/')}/upload/"
    response = httpx.post(upload_url, data=data, files=files, headers=personaldb_headers(), timeout=40.0)
    result = response.json()
    if not result.get("id"):
        yield json.dumps({"type": "status", "message": "论文向量化失败，请联系管理员"}, ensure_ascii=False) + '\n'
    yield json.dumps({"type": "status", "message": "正在生成大纲..."}, ensure_ascii=False) + '\n'
    outline = ""
    async for outline_trunk in stream_agent_response(paper_markdown, language):
        outline += outline_trunk
    yield json.dumps({"type": "status", "message": "大纲生成完毕，即将生成PPT..."}, ensure_ascii=False) + '\n'

    match = re.search(r"(# .*)", outline, flags=re.DOTALL)

    if match:
        result = outline[match.start():]
    else:
        result = outline
    logger.info("file-id flow outline chars=%s", len(result))
    content_wrapper = A2AContentClientWrapper(session_id=uuid.uuid4().hex, agent_url=CONTENT_API)
    # 传入不同的参数，使用不同的搜索,可以同时使用多个搜索
    search_engine = ["KnowledgeBaseSearch"]
    # 方便测试，这个已经在知识库中插入了对应的数据
    metadata = {"user_id": id, "search_engine": search_engine, "language": language}
    logger.info("file-id flow metadata user_id=%s", id)
    async for chunk_data in content_wrapper.generate(user_question=result, metadata=metadata):
        if chunk_data["type"] == "text":
            slide = chunk_data["text"]
            yield slide + '\n'


@app.post("/tools/aippt_by_id")
async def aippt_by_id(payload: AipptByIDRequest, request: Request):
    await current_user(request)
    raise HTTPException(status_code=410, detail="按文件 ID 生成已停用，请先将文件加入知识库后从项目工作流生成")


@app.get("/files/{user_id}")
async def list_user_files(user_id: int, request: Request):
    """
    列出指定用户的所有文件信息
    """
    user = await current_user(request)
    if user_id != user["id"]:
        raise HTTPException(status_code=403, detail="不能读取其他用户的文件")
    personaldb_api_url = os.environ["PERSONAL_DB"]
    url = f"{personaldb_api_url}/files/{user_id}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=personaldb_headers())
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as exc:
            raise HTTPException(status_code=500, detail=f"Error connecting to personaldb: {exc}")
        except httpx.HTTPStatusError as exc:
            # 转发下游服务的错误
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)


@app.get("/proxy")
async def proxy(request: Request, url: str = Query(..., description="Target absolute URL")):
    """
    透明代理上游资源，转发部分请求头，透传关键响应头，并允许前端同源访问。
    适合图片/音视频等二进制内容。
    """
    parsed = httpx.URL(url)
    if parsed.scheme != "https" or not parsed.host:
        raise HTTPException(status_code=400, detail="代理仅允许 HTTPS URL")
    allowed_hosts = {
        host.strip().lower()
        for host in os.environ.get("PROXY_ALLOWED_HOSTS", "images.pexels.com,images.unsplash.com,source.unsplash.com").split(",")
        if host.strip()
    }
    hostname = parsed.host.lower().rstrip(".")
    if hostname not in allowed_hosts and not any(hostname.endswith("." + suffix) for suffix in allowed_hosts):
        raise HTTPException(status_code=403, detail="目标域名不在代理白名单")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise HTTPException(status_code=502, detail="目标域名解析失败") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise HTTPException(status_code=403, detail="禁止访问内网地址")

    HEADERS_TO_FORWARD = {"Range", "User-Agent"}  # 需要时可扩展
    HEADERS_TO_COPY = {
        "Content-Type",
        "Content-Length",
        "Content-Disposition",
        "Accept-Ranges",
        "ETag",
        "Last-Modified",
        "Cache-Control",
        "Expires",
    }
    forward_headers = {}
    for h in HEADERS_TO_FORWARD:
        v = request.headers.get(h)
        if v:
            forward_headers[h] = v

    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        try:
            async with client.stream("GET", url, headers=forward_headers) as upstream:
                if 300 <= upstream.status_code < 400:
                    raise HTTPException(status_code=403, detail="代理目标不允许重定向")
                if upstream.status_code >= 400:
                    raise HTTPException(status_code=upstream.status_code, detail="Upstream error")
                declared_length = upstream.headers.get("Content-Length")
                if declared_length and int(declared_length) > MAX_PROXY_BYTES:
                    raise HTTPException(status_code=413, detail="代理资源超过大小限制")
                chunks: list[bytes] = []
                total = 0
                async for chunk in upstream.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_PROXY_BYTES:
                        raise HTTPException(status_code=413, detail="代理资源超过大小限制")
                    chunks.append(chunk)
                body = b"".join(chunks)
                upstream_status = upstream.status_code
                upstream_headers = dict(upstream.headers)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Upstream fetch error: {e!s}")

    headers = {}
    for h in HEADERS_TO_COPY:
        if h in upstream_headers:
            headers[h] = upstream_headers[h]

    # 允许被前端同源读取
    headers["Access-Control-Allow-Origin"] = "*"
    # 给静态资源加简单缓存（按需调整）
    headers.setdefault("Cache-Control", "public, max-age=86400")

    return Response(
        content=body,
        status_code=upstream_status,
        headers=headers,
        media_type=upstream_headers.get("Content-Type"),
    )

@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "ppt-main-api", "version": "1.0.0"}


@app.get("/health/live", tags=["health"])
def health_live():
    return {"status": "UP", "service": "ppt-main-api"}


@app.get("/health/ready", tags=["health"])
async def health_ready():
    checks: dict[str, bool] = {}
    try:
        await store.get_user(1)  # 仅探测数据库可用性，不关心结果
        checks["database"] = True
    except Exception:
        checks["database"] = False
    for name, url in (("outline", OUTLINE_API), ("content", CONTENT_API), ("personal_db", PERSONAL_DB)):
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                endpoint = url.rstrip("/") + ("/.well-known/agent.json" if name != "personal_db" else "/health/live")
                response = await client.get(endpoint, headers=personaldb_headers() if name == "personal_db" else None)
                checks[name] = 200 <= response.status_code < 300
        except httpx.HTTPError:
            checks[name] = False
    ready = all(checks.values())
    return {"status": "READY" if ready else "DEGRADED", "checks": checks}


@app.get("/health/config", tags=["health"])
async def health_config(request: Request):
    await current_user(request)
    return {
        "status": "OK",
        "providers": {
            "llm": {"provider": os.getenv("MODEL_PROVIDER", "deepseek"), "model": os.getenv("LLM_MODEL", "deepseek-chat")},
            "embedding": {"provider": os.getenv("EMBEDDING_PROVIDER", "aliyun"), "model": os.getenv("EMBEDDING_MODEL", "text-embedding-v3")},
        },
        "storage": {"driver": "postgresql" if is_postgres() else "sqlite"},
    }


if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("MAIN_API_PORT", "6800"))
    uvicorn.run(app, host=host, port=port)
