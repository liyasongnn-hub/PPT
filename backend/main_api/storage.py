"""对象存储适配层：生产用 MinIO，本地开发回退到本地文件系统。

- ``MINIO_ENDPOINT`` 未设置 → ``LocalStorageBackend``：对象落在
  ``{PLATFORM_DATA_DIR}/storage/{bucket}/{object_key}``，让 ``start.py`` 零依赖跑通。
- ``MINIO_ENDPOINT`` 已设置 → ``MinioStorageBackend``：私有桶，读写走
  ``asyncio.to_thread``（minio 客户端是同步的），下载用预签名 URL。

``presign_get`` 对本地后端返回 ``None``，调用方据此回退为直接流式返回，
避免为本地文件伪造一个不存在的签名地址。
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from datetime import timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

EXPORT_BUCKET = os.environ.get("MINIO_EXPORT_BUCKET", "exports")
UPLOAD_BUCKET = os.environ.get("MINIO_UPLOAD_BUCKET", "uploads")
FILE_SIGNED_URL_TTL = int(os.environ.get("FILE_SIGNED_URL_TTL", str(15 * 60)))


class StorageBackend:
    async def ensure_buckets(self, buckets: list[str]) -> None:
        raise NotImplementedError

    async def put_object(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    async def get_object(self, bucket: str, key: str) -> bytes:
        raise NotImplementedError

    async def delete_object(self, bucket: str, key: str) -> None:
        raise NotImplementedError

    async def presign_get(self, bucket: str, key: str, ttl_seconds: int) -> str | None:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class LocalStorageBackend(StorageBackend):
    def __init__(self, data_dir: Path) -> None:
        self.root = (data_dir / "storage").resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def ensure_buckets(self, buckets: list[str]) -> None:
        for bucket in buckets:
            (self.root / bucket).mkdir(parents=True, exist_ok=True)

    def _path(self, bucket: str, key: str) -> Path:
        base = (self.root / bucket).resolve()
        target = (base / key.replace("\\", "/").lstrip("/")).resolve()
        if not str(target).startswith(str(base)):
            raise ValueError("非法 object_key")
        return target

    async def put_object(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        target = self._path(bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)

    async def get_object(self, bucket: str, key: str) -> bytes:
        target = self._path(bucket, key)
        return await asyncio.to_thread(target.read_bytes)

    async def delete_object(self, bucket: str, key: str) -> None:
        target = self._path(bucket, key)

        def _rm() -> None:
            if target.exists():
                target.unlink()

        await asyncio.to_thread(_rm)

    async def presign_get(self, bucket: str, key: str, ttl_seconds: int) -> str | None:
        return None


class MinioStorageBackend(StorageBackend):
    def __init__(self, endpoint: str, access_key: str, secret_key: str, secure: bool) -> None:
        from minio import Minio  # 延迟导入，本地开发未安装 minio 也不影响

        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    async def ensure_buckets(self, buckets: list[str]) -> None:
        def _ensure() -> None:
            for bucket in buckets:
                if not self.client.bucket_exists(bucket):
                    self.client.make_bucket(bucket)

        await asyncio.to_thread(_ensure)

    async def put_object(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        def _put() -> None:
            self.client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)

        await asyncio.to_thread(_put)

    async def get_object(self, bucket: str, key: str) -> bytes:
        def _get() -> bytes:
            resp = self.client.get_object(bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        return await asyncio.to_thread(_get)

    async def delete_object(self, bucket: str, key: str) -> None:
        await asyncio.to_thread(self.client.remove_object, bucket, key)

    async def presign_get(self, bucket: str, key: str, ttl_seconds: int) -> str:
        def _presign() -> str:
            return self.client.presigned_get_object(bucket, key, expires=timedelta(seconds=ttl_seconds))

        return await asyncio.to_thread(_presign)


def make_storage(data_dir: Path) -> StorageBackend:
    endpoint = os.environ.get("MINIO_ENDPOINT", "").strip()
    if endpoint:
        access_key = os.environ.get("MINIO_ACCESS_KEY", "")
        secret_key = os.environ.get("MINIO_SECRET_KEY", "")
        secure = os.environ.get("MINIO_SECURE", "false").lower() in ("1", "true", "yes")
        logger.info("using MinioStorageBackend endpoint=%s", endpoint)
        return MinioStorageBackend(endpoint, access_key, secret_key, secure)
    logger.info("using LocalStorageBackend dir=%s", data_dir / "storage")
    return LocalStorageBackend(data_dir)
