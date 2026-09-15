#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/8/12
# @Desc  : 使用FastAPI实现API，接收JSON或RabbitMQ消息，下载七牛云文件，读取内容并生成embedding向量

import os
import json
import requests
import uvicorn
import logging
import asyncio
import uuid
import ipaddress
import re
import secrets
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, File, UploadFile, Form, Request
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.responses import JSONResponse
from typing import List, Optional
import embedding_utils
from embedding_utils import cache_decorator
from urllib.parse import unquote, urlparse
from core.magic_pdf_converter import MagicPDFConverter
from core.markitdown_converter import MarkItDownConverter
from core.chunkers.semantic_chunker import SemanticChunker
from core.chunkers.fast_chunker import FastChunker

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _cleanup_temp_downloads() -> None:
    """清理 temp_download 中上次运行残留的临时文件。"""
    try:
        entries = os.listdir(TEMP_DIR)
    except FileNotFoundError:
        return
    for entry in entries:
        path = os.path.join(TEMP_DIR, entry)
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            logger.info("清理残留临时文件: %s", entry)
        except OSError:
            logger.warning("清理残留临时文件失败: %s", entry)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _cleanup_temp_downloads()
    yield
    _cleanup_temp_downloads()


app = FastAPI(lifespan=lifespan)

# 运行时限制。所有值都可以通过环境变量调整，但不会接受非正数。
def _positive_int_env(name: str, default: int, maximum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if value < 1:
        value = default
    if maximum is not None:
        value = min(value, maximum)
    return value


MAX_UPLOAD_BYTES = _positive_int_env("PERSONALDB_MAX_UPLOAD_BYTES", 20 * 1024 * 1024, 200 * 1024 * 1024)
MAX_REMOTE_BYTES = _positive_int_env("PERSONALDB_MAX_REMOTE_BYTES", MAX_UPLOAD_BYTES, 200 * 1024 * 1024)
MAX_TEXT_CHARS = _positive_int_env("PERSONALDB_MAX_TEXT_CHARS", 200_000, 2_000_000)
MAX_QUERY_CHARS = _positive_int_env("PERSONALDB_MAX_QUERY_CHARS", 10_000, 100_000)
MAX_EXTRACTED_TEXT_CHARS = _positive_int_env("PERSONALDB_MAX_EXTRACTED_TEXT_CHARS", 2_000_000, 10_000_000)
MAX_DOCUMENT_CHUNKS = _positive_int_env("PERSONALDB_MAX_DOCUMENT_CHUNKS", 500, 2_000)
MAX_REMOTE_DOWNLOAD_SECONDS = _positive_int_env("PERSONALDB_MAX_REMOTE_DOWNLOAD_SECONDS", 120, 600)
MAX_FILENAME_CHARS = 255
MAX_URL_CHARS = 2_048
MAX_UPLOAD_REQUEST_BYTES = MAX_UPLOAD_BYTES + 1 * 1024 * 1024
CHROMA_DB_DIR = os.path.abspath(
    os.environ.get("CHROMA_DB_DIR", str(Path(__file__).resolve().parent / "cache" / "chromadb"))
)


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """在 multipart/JSON 解析前限制请求体，覆盖缺少 Content-Length 的分块请求。"""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        limits = {
            "/upload/": MAX_UPLOAD_REQUEST_BYTES,
            "/upload": MAX_UPLOAD_REQUEST_BYTES,
            "/vectorize/text": MAX_TEXT_CHARS * 4 + 64 * 1024,
            "/search": MAX_QUERY_CHARS * 4 + 64 * 1024,
        }
        limit = limits.get(scope.get("path"))
        if limit is None:
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        raw_length = headers.get(b"content-length")
        if raw_length:
            try:
                if int(raw_length) > limit or int(raw_length) < 0:
                    raise _RequestBodyTooLarge
            except ValueError:
                response = JSONResponse({"detail": "Content-Length 无效"}, status_code=400)
                await response(scope, receive, send)
                return
            except _RequestBodyTooLarge:
                response = JSONResponse({"detail": "请求体超过大小限制"}, status_code=413)
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            response = JSONResponse({"detail": "请求体超过大小限制"}, status_code=413)
            await response(scope, receive, send)


app.add_middleware(RequestBodyLimitMiddleware)


@app.get("/health/live", include_in_schema=False)
async def health_live():
    return {"status": "UP"}

# 将临时文件放在模块目录下，避免服务从不同工作目录启动时写入未知位置。
TEMP_DIR = os.path.abspath(os.environ.get("PERSONALDB_TEMP_DIR", str(Path(__file__).resolve().parent / "temp_download")))
os.makedirs(TEMP_DIR, exist_ok=True)


def _env_bool(*names: str, default: bool = False) -> bool:
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


# 生产 compose 应设置 REQUIRE_INTERNAL_AUTH=true 和 PERSONALDB_INTERNAL_TOKEN。
# 未设置时保留本地开发兼容性；服务本身仍应只加入内部 Docker 网络。
REQUIRE_INTERNAL_AUTH = _env_bool("PERSONALDB_REQUIRE_INTERNAL_AUTH", "REQUIRE_INTERNAL_AUTH")
PERSONALDB_INTERNAL_TOKEN = os.environ.get("PERSONALDB_INTERNAL_TOKEN", "").strip()


def _require_internal_auth(request: Request) -> None:
    """校验仅供主 API/Agent 调用的共享令牌。"""
    if not REQUIRE_INTERNAL_AUTH:
        return
    if len(PERSONALDB_INTERNAL_TOKEN) < 32 or PERSONALDB_INTERNAL_TOKEN.lower().startswith(("replace", "your_")):
        logger.error("内部认证已启用，但 PERSONALDB_INTERNAL_TOKEN 未配置或强度不足")
        raise HTTPException(status_code=503, detail="知识库内部认证未配置")
    provided = request.headers.get("X-Internal-Token", "")
    if not provided or not secrets.compare_digest(provided, PERSONALDB_INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="需要内部服务认证")


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_FILE_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{0,31}$")
DEFAULT_ALLOWED_FILE_TYPES = frozenset({
    "csv", "doc", "docx", "epub", "html", "htm", "json", "md", "markdown",
    "pdf", "ppt", "pptx", "rtf", "txt", "xls", "xlsx", "xml",
})


def _validate_user_id(value: int | str) -> int | str:
    if isinstance(value, bool) or value is None:
        raise HTTPException(status_code=422, detail="userId 无效")
    text = str(value).strip()
    if not text or len(text) > 128 or not _SAFE_ID_RE.fullmatch(text):
        raise HTTPException(status_code=422, detail="userId 无效")
    # Platform account IDs are integers. Canonicalize form values such as
    # "1" so Chroma metadata and /files/{user_id} comparisons use one type.
    if text.isdecimal():
        parsed = int(text)
        if parsed > 2**63 - 1:
            raise HTTPException(status_code=422, detail="userId 超出范围")
        return parsed
    return text


def _coerce_bounded_int(value: object, field_name: str, *, default: int = 0, maximum: int = 2**63 - 1) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{field_name} 无效") from None
    if parsed < 0 or parsed > maximum:
        raise HTTPException(status_code=422, detail=f"{field_name} 超出范围")
    return parsed


def _sanitize_filename(filename: object, fallback: str = "uploaded_file") -> str:
    """去掉路径和控制字符，防止文件名影响临时目录边界。"""
    value = str(filename or "").replace("\\", "/")
    value = unquote(value).split("/")[-1]
    value = "".join(ch for ch in value if ord(ch) >= 32 and ch not in {"\x7f", ":"}).strip()
    if value in {"", ".", ".."}:
        value = fallback
    return value[:MAX_FILENAME_CHARS]


def _normalize_file_type(value: object, fallback: str = "unknown", *, require_allowed: bool = True) -> str:
    value = str(value or "").strip().lower()
    if not value:
        value = fallback
    if len(value) > 32 or not _SAFE_FILE_TYPE_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="fileType 无效")
    configured_types = {
        item.strip().lower().lstrip(".")
        for item in os.environ.get("PERSONALDB_ALLOWED_FILE_TYPES", "").split(",")
        if item.strip()
    }
    allowed_types = configured_types or DEFAULT_ALLOWED_FILE_TYPES
    if require_allowed and value not in allowed_types:
        raise HTTPException(status_code=415, detail=f"不支持的文件类型: {value}")
    return value


def _validate_filename_type(filename: str, supplied_type: str | None) -> str:
    extension = Path(filename).suffix.lower().lstrip(".")
    extension_type = _normalize_file_type(extension) if extension else None
    if supplied_type and extension_type and supplied_type != extension_type:
        raise HTTPException(status_code=422, detail="fileType 与文件扩展名不一致")
    if supplied_type:
        return supplied_type
    if extension_type:
        return extension_type
    raise HTTPException(status_code=415, detail="无法识别文件类型，请提供带扩展名的文件")


def _safe_url_for_log(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "?"
    return f"{parsed.scheme}://{host}{parsed.path[:160]}"


def _is_private_or_reserved(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # is_global 同时排除环回、内网、链路本地、保留、多播和共享地址段。
    return not ip.is_global


def _validate_remote_url(raw_url: object):
    """校验并解析可下载 URL，阻断本机/内网和重定向 SSRF。"""
    url = str(raw_url or "").strip()
    if not url or len(url) > MAX_URL_CHARS:
        raise ValueError("url 不能为空且长度不能超过 2048 个字符")
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or parsed.scheme != scheme:
        raise ValueError("url 必须使用 http 或 https")
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("url 主机地址无效")
    if parsed.fragment:
        raise ValueError("url 不应包含 fragment")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        raise ValueError("url 端口无效") from None
    if not 1 <= port <= 65535:
        raise ValueError("url 端口无效")

    hostname = parsed.hostname.rstrip(".").lower()
    # 可选的主机白名单适合生产环境进一步收窄下载来源。
    allowlist = {
        item.strip().lower().rstrip(".")
        for item in os.environ.get("PERSONALDB_URL_ALLOWLIST", "").split(",")
        if item.strip()
    }
    if allowlist and hostname not in allowlist:
        raise ValueError("url 主机不在允许列表中")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        addresses = [str(literal_ip)]
    else:
        # 在请求前解析所有地址；任一地址是内网/保留地址就拒绝，避免 DNS 指向内部服务。
        try:
            infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("无法解析 url 主机") from exc
        addresses = sorted({str(info[4][0]) for info in infos if info and info[4]})
        if not addresses:
            raise ValueError("无法解析 url 主机")

    if not _env_bool("PERSONALDB_ALLOW_PRIVATE_URLS", default=False) and any(
        _is_private_or_reserved(address) for address in addresses
    ):
        raise ValueError("出于安全原因不允许访问内网地址")
    return parsed, url


def _validate_response_peer(response: requests.Response) -> None:
    """校验 requests 实际连接的对端地址，缩小 DNS 重绑定的时间窗。"""
    raw = response.raw
    connection = getattr(raw, "_connection", None) or getattr(raw, "connection", None)
    sock = getattr(connection, "sock", None)
    if sock is None:
        # urllib3 在部分版本/协议下把 socket 挂在底层 buffered reader 上。
        fp = getattr(getattr(raw, "_fp", None), "fp", None)
        sock = getattr(getattr(fp, "raw", None), "_sock", None)
    try:
        peer_address = str(sock.getpeername()[0]) if sock is not None else ""
    except (AttributeError, OSError, TypeError):
        peer_address = ""
    if not peer_address:
        raise ValueError("无法验证远程连接地址")
    if not _env_bool("PERSONALDB_ALLOW_PRIVATE_URLS", default=False) and _is_private_or_reserved(peer_address):
        raise ValueError("出于安全原因不允许访问内网地址")


def _write_stream_to_file(response: requests.Response, destination: str, max_bytes: int) -> int:
    """流式写入响应并强制总大小上限，避免 response.content 带来的内存耗尽。"""
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size < 0 or declared_size > max_bytes:
            raise ValueError("远程文件超过大小限制")

    total = 0
    deadline = time.monotonic() + MAX_REMOTE_DOWNLOAD_SECONDS
    with open(destination, "wb") as output:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if time.monotonic() > deadline:
                raise ValueError("远程文件下载超时")
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("远程文件超过大小限制")
            output.write(chunk)
    if total == 0:
        raise ValueError("远程文件内容为空")
    return total

# RabbitMQ消息处理类

class SearchQuery(BaseModel):
    userId: int | str
    query: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)
    keyword: Optional[str] = Field(default="", max_length=1_000)
    topk: int = Field(default=3, ge=1, le=20)

@app.post("/search")
def search_personal_knowledge_base(query: SearchQuery, request: Request):
    """
    搜索个人知识库
    """
    try:
        _require_internal_auth(request)
        _validate_user_id(query.userId)
        logger.info("收到搜索请求: userId=%s query_chars=%s topk=%s", query.userId, len(query.query), query.topk)
        embedder = embedding_utils.EmbeddingModel()
        chroma = embedding_utils.ChromaDB(embedder, db_dir=CHROMA_DB_DIR)
        collection_name = f"user_{query.userId}"

        result = chroma.query2collection(
            collection=collection_name,
            query_documents=[query.query],
            keyword=query.keyword,
            topk=query.topk
        )
        logger.info("知识库搜索成功: userId=%s", query.userId)
        return result
    except Exception as e:
        logger.error(f"搜索失败: {str(e)}", exc_info=True)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail="搜索失败") from e

@cache_decorator
def _get_markdown_content(file_path: str, file_name: str) -> str:
    """
    根据文件类型选择合适的转换器，将文件内容转换为Markdown格式。
    PDF文件使用MagicPDFConverter（MinerU），其他文件使用MarkitdownConverter。
    """
    # 获取文件扩展名, 是否可以使用MinerU，如果不用显卡速度太慢
    USE_MINERU = os.environ.get("USE_MINERU", "false")
    if USE_MINERU.lower() == "true":
        CAN_USE_MINERU = True
    else:
        CAN_USE_MINERU = False
    file_extension = os.path.splitext(file_name)[1].lower() if file_name else ""

    # 根据文件类型选择转换器
    if CAN_USE_MINERU and file_extension == '.pdf':
        # 使用 MinerU (MagicPDFConverter) 处理PDF
        logger.info(f"使用PDF转换器(MinerU)处理文件: {file_path}")
        converter = MagicPDFConverter(output_dir="./output_pdf")
        content, _ = converter.convert_pdf_file(file_path)
        return True, content
    else:
        # 使用 markitdown 处理其他文件
        logger.info(f"使用Markitdown转换器处理文件: {file_path}")
        converter = MarkItDownConverter(use_magic_pdf=False)  #use_magic_pdf设定是否使用MinerU
        content, _ = converter.convert_file(file_path)
        return True, content


def process_and_vectorize_local_file(file_name: str, temp_file_path: str, id: int, user_id: int|str, file_type: str, url: str, folder_id: int):
    """
    从本地文件路径处理文件、进行向量化并存储
    """
    file_name = _sanitize_filename(file_name, "uploaded_file")
    user_id = _validate_user_id(user_id)
    id = _coerce_bounded_int(id, "fileId", maximum=2**63 - 1)
    folder_id = _coerce_bounded_int(folder_id, "folderId", maximum=2**63 - 1)
    file_type = _normalize_file_type(file_type)
    # 步骤2: 使用适当的转换器读取文件内容
    logger.info(f"开始读取文件内容: {temp_file_path}")
    
    status, markdown_content = _get_markdown_content(temp_file_path, file_name)

    if not markdown_content or not markdown_content.strip():
        logger.error(f"文件内容为空或无效: {temp_file_path}")
        raise ValueError("文件内容为空或无效")
    if len(markdown_content) > MAX_EXTRACTED_TEXT_CHARS:
        raise ValueError("文件解析后的文本超过大小限制")
    logger.info(f"文件内容读取成功，准备进行分块。")

    # 对Markdown格式进行Trunk(分块)
    documents = _chunk_text(markdown_content)
    if not documents:
        raise ValueError("分块后内容为空")
    if len(documents) > MAX_DOCUMENT_CHUNKS:
        raise ValueError("文件分块数量超过限制")
    logger.info(f"内容分块成功，共 {len(documents)} 块。")

    # 使用 embedding provider 自身的配置校验，不能把所有 provider 都绑定到阿里云密钥。
    logger.info("初始化embedding模型")
    embedder = embedding_utils.EmbeddingModel()
    chroma = embedding_utils.ChromaDB(embedder, db_dir=CHROMA_DB_DIR)
    logger.info(f"开始插入文件 {id} 的向量")
    embedding_result = chroma.insert_file_vectors(
        file_name=file_name,
        user_id=user_id,
        file_id=id,
        file_type=file_type,
        url=url or "",
        folder_id=folder_id or 0,
        documents=documents
    )
    logger.info("向量插入成功")

    result = {
        "id": id,
        "file_name": file_name,
        "userId": user_id,
        "fileType": file_type,
        "url": url,
        "folderId": folder_id,
        "embedding_result": embedding_result,
        "markdown_content": markdown_content
    }
    logger.info(f"处理OK。。。")
    return result


def process_file_sync(file_name:str, id: int, user_id: int|str, file_type: str, url: str, folder_id: int):
    """
    处理文件下载、读取和生成embedding的同步版本
    """
    parsed_url, normalized_url = _validate_remote_url(url)
    file_name = _sanitize_filename(file_name or os.path.basename(unquote(parsed_url.path)), "downloaded_file")
    logger.info(f"解析后的远程 URL: {_safe_url_for_log(normalized_url)}")
    temp_file_path = None
    try:
        # 步骤1: 下载文件
        temp_file_path = os.path.join(TEMP_DIR, f"{uuid.uuid4().hex}_{file_name}")
        logger.info(f"开始下载文件: {_safe_url_for_log(normalized_url)}")
        # 不跟随重定向，避免校验后的公网主机被重定向到内网。
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(
                normalized_url,
                timeout=(10, 60),
                allow_redirects=False,
                stream=True,
            )
            _validate_response_peer(response)
            response.raise_for_status()
            if 300 <= response.status_code < 400 or response.headers.get("Location"):
                raise ValueError("远程文件不允许重定向")
            _write_stream_to_file(response, temp_file_path, MAX_REMOTE_BYTES)
        logger.info(f"文件下载成功: {temp_file_path}")

        return process_and_vectorize_local_file(file_name, temp_file_path, id, user_id, file_type, normalized_url, folder_id)

    except requests.exceptions.Timeout as e:
        logger.warning("下载文件超时: %s", _safe_url_for_log(normalized_url))
        raise ValueError("下载文件超时") from e
    except requests.exceptions.RequestException as e:
        logger.warning("下载文件失败: %s", _safe_url_for_log(normalized_url))
        raise ValueError("下载文件失败") from e
    except ValueError as e:
        logger.error(f"处理失败: {str(e)}", exc_info=True)
        raise
    except Exception as e:
        logger.exception("远程文件处理失败")
        raise ValueError("远程文件处理失败") from e
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"临时文件已删除: {temp_file_path}")
            except OSError:
                logger.warning("临时文件清理失败: %s", temp_file_path)


@app.post("/upload/")
async def upload_and_vectorize_endpoint(request: Request):
    """
    支持三种内容类型：
    - multipart/form-data（带或不带文件）
    - application/x-www-form-urlencoded
    - application/json

    字段：
    - userId: int
    - fileId: int
    - folderId: int (可选，默认0)
    - fileType: str (可选)
    - url: str (可选，与 file 互斥)
    - file: UploadFile (可选，与 url 互斥)
    """
    temp_file_path = None
    try:
        _require_internal_auth(request)
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                declared_request_size = int(content_length)
            except ValueError:
                raise HTTPException(status_code=400, detail="Content-Length 无效") from None
            if declared_request_size < 0 or declared_request_size > MAX_UPLOAD_REQUEST_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"请求体不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                )

        # 统一解析 body
        content_type = request.headers.get("content-type", "")
        data = {}
        upload_file: UploadFile | None = None

        if "application/json" in content_type:
            try:
                data = await request.json()
            except (ValueError, TypeError):
                raise HTTPException(status_code=400, detail="JSON 请求体无效") from None
            if not isinstance(data, dict):
                raise HTTPException(status_code=422, detail="请求体必须是对象")
        elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
            # 对 multipart/form-data 与 x-www-form-urlencoded 都适用
            form = await request.form(max_files=1, max_fields=12, max_part_size=64 * 1024)
            data = dict(form)
            possible_file = form.get("file")
            if isinstance(possible_file, (UploadFile, StarletteUploadFile)):
                upload_file = possible_file
        else:
            raise HTTPException(status_code=415, detail="仅支持 JSON 或表单请求")

        # 参数解析与校验
        userId = data.get("userId")
        fileId = data.get("fileId")

        if userId is None:
            raise HTTPException(status_code=422, detail="缺少或非法参数: userId")
        if fileId is None:
            raise HTTPException(status_code=422, detail="缺少或非法参数: fileId")

        userId = _validate_user_id(userId)
        fileId = _coerce_bounded_int(fileId, "fileId", maximum=2**63 - 1)
        folderId = _coerce_bounded_int(data.get("folderId", 0), "folderId", maximum=2**63 - 1)
        fileType = _normalize_file_type(data.get("fileType")) if data.get("fileType") else None
        url = str(data.get("url") or "").strip()
        if len(url) > MAX_URL_CHARS:
            raise HTTPException(status_code=422, detail="url 过长")

        # 互斥校验
        has_url = bool(url and str(url).strip())
        has_file = upload_file is not None
        if not has_url and not has_file:
            raise HTTPException(status_code=400, detail="必须提供 'url' 或 'file'")
        if has_url and has_file:
            raise HTTPException(status_code=400, detail="只能提供 'url' 或 'file' 中的一个")

        # 分支：文件上传
        if has_file:
            # 推断 fileType
            original_file_name = _sanitize_filename(upload_file.filename, "uploaded_file")
            fileType = _validate_filename_type(original_file_name, fileType)

            temp_file_name = f"{uuid.uuid4().hex}_{original_file_name}"
            temp_file_path = os.path.join(TEMP_DIR, temp_file_name)
            # 分块保存上传内容，避免一次性把整个文件读入内存。
            total_bytes = 0
            with open(temp_file_path, "xb") as buffer:
                while True:
                    content_bytes = await upload_file.read(1024 * 1024)
                    if not content_bytes:
                        break
                    total_bytes += len(content_bytes)
                    if total_bytes > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"文件大小不能超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                        )
                    buffer.write(content_bytes)
            if total_bytes == 0:
                raise HTTPException(status_code=400, detail="文件内容为空")
            logger.info(f"文件上传成功: {temp_file_path}")

            return await asyncio.to_thread(
                process_and_vectorize_local_file,
                file_name=original_file_name,
                temp_file_path=temp_file_path,
                id=fileId,
                user_id=userId,
                file_type=fileType,
                url="",  # 直接上传无 URL
                folder_id=folderId,
            )

        # 分支：URL 下载处理
        else:
            parsed_url = urlparse(url)
            file_name = _sanitize_filename(
                os.path.basename(unquote(parsed_url.path)),
                f"downloaded_file_{userId}",
            )
            fileType = _validate_filename_type(file_name, fileType)
            return await asyncio.to_thread(
                process_file_sync,
                file_name=file_name,
                id=fileId,
                user_id=userId,
                file_type=fileType,
                url=url,
                folder_id=folderId,
            )

    except HTTPException:
        raise
    except ValueError as e:
        logger.error("上传和向量化参数/处理失败: %s", str(e), exc_info=True)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"上传和向量化失败: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="上传和向量化失败") from e
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                logger.info(f"临时文件已删除: {temp_file_path}")
            except OSError:
                logger.warning("临时文件清理失败: %s", temp_file_path)


class TextVectorizeBody(BaseModel):
    """
    纯文本向量化请求体。
    仅必需字段：content, fileId, fileName
    其余参数均为可选，默认空/0。
    """
    content: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS)
    fileId: int = Field(..., ge=0, le=2**63 - 1)
    fileName: str = Field(..., min_length=1, max_length=MAX_FILENAME_CHARS)
    userId: Optional[int | str] = 0
    fileType: Optional[str] = Field(default=None, max_length=32)
    url: Optional[str] = Field(default="", max_length=MAX_URL_CHARS)
    folderId: Optional[int] = Field(default=0, ge=0, le=2**63 - 1)


def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 200) -> List[str]:
    """
    使用 SemanticChunker 进行分块。
    """
    text = (text or "").strip()
    if not text:
        return []
    chunker = FastChunker(max_tokens=max_chars)
    chunks = chunker.chunk_text(text)
    return [chunk.content for chunk in chunks]


def process_text_content(
    file_name: str,
    text: str,
    id: int,
    user_id: int = 0,
    file_type: Optional[str] = None,
    folder_id: int = 0,
    url: str = ""
):
    """
    直接对纯文本进行向量化并落库（Chroma）。
    其余参数默认空/0，以满足“无需额外参数”的需求。
    """
    logger.info("开始处理纯文本向量化")
    if not text or not text.strip():
        raise ValueError("content 不能为空")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("content 超过大小限制")
    file_name = _sanitize_filename(file_name, "text.txt")
    user_id = _validate_user_id(user_id)
    file_type = _normalize_file_type(file_type, fallback="txt")
    id = _coerce_bounded_int(id, "fileId", maximum=2**63 - 1)
    folder_id = _coerce_bounded_int(folder_id, "folderId", maximum=2**63 - 1)
    if url and len(str(url)) > MAX_URL_CHARS:
        raise ValueError("url 过长")

    documents = _chunk_text(text)
    if not documents:
        raise ValueError("content 无有效文本")
    if len(documents) > MAX_DOCUMENT_CHUNKS:
        raise ValueError("content 分块数量超过限制")

    logger.info("初始化 embedding 模型与 Chroma")
    embedder = embedding_utils.EmbeddingModel()
    chroma = embedding_utils.ChromaDB(embedder, db_dir=CHROMA_DB_DIR)

    logger.info(f"插入文本向量：fileId={id}, userId={user_id}")
    embedding_result = chroma.insert_file_vectors(
        file_name=file_name,
        user_id=user_id or 0,
        file_id=id,
        file_type=file_type or "unknown",
        url=url or "",
        folder_id=folder_id or 0,
        documents=documents
    )

    result = {
        "id": id,
        "file_name": file_name,
        "userId": user_id or 0,
        "fileType": file_type or "unknown",
        "url": url or "",
        "folderId": folder_id or 0,
        "embedding_result": embedding_result
    }
    logger.info("纯文本向量化完成")
    return result


# ===== 纯文本向量化接口 =====
@app.post("/vectorize/text")
def vectorize_text_endpoint(body: TextVectorizeBody, request: Request):
    """
    纯文本向量化：
    - 必填：content, fileId, fileName
    - 可选：userId(默认0), fileType(None), url(""), folderId(0)
    """
    try:
        _require_internal_auth(request)
        _validate_user_id(body.userId or 0)
        logger.info(
            f"收到文本向量化请求: fileId={body.fileId}, fileName={body.fileName}, userId={body.userId}"
        )
        return process_text_content(
            file_name=body.fileName,
            text=body.content,
            id=body.fileId,
            user_id=body.userId or 0,
            file_type=body.fileType,
            folder_id=body.folderId or 0,
            url=body.url or ""
        )
    except Exception as e:
        logger.error(f"文本向量化失败: {str(e)}", exc_info=True)
        if isinstance(e, HTTPException):
            raise
        if isinstance(e, ValueError):
            raise HTTPException(status_code=400, detail=str(e)) from e
        raise HTTPException(status_code=500, detail="文本向量化失败") from e

@app.get("/files/{user_id}")
def list_user_files(user_id: int, request: Request):
    """
    列出指定用户的所有文件信息
    """
    try:
        _require_internal_auth(request)
        _validate_user_id(user_id)
        logger.info(f"收到列出用户 {user_id} 文件的请求")
        embedder = embedding_utils.EmbeddingModel()
        chroma = embedding_utils.ChromaDB(embedder, db_dir=CHROMA_DB_DIR)

        files = chroma.list_files_by_user(user_id=user_id)

        if not files:
            logger.info(f"用户 {user_id} 没有任何文件。")
            return []

        logger.info(f"成功为用户 {user_id} 找到 {len(files)} 个文件。")
        return files
    except Exception as e:
        logger.error(f"列出用户 {user_id} 的文件失败: {str(e)}", exc_info=True)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail="列出文件失败") from e


if __name__ == "__main__":
    """
    主函数入口：启动FastAPI服务
    """
    print("启动Personal DB FastAPI服务...")
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PERSONALDB_PORT", "9100")))
