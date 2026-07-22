from __future__ import annotations

import asyncio
import contextlib
import threading
from pathlib import Path

import tos
from fastapi import UploadFile

from app.server.infra.config import settings
from app.server.infra.logger import logger

_CHUNK_SIZE = 8 * 1024 * 1024  # 8 MB


class TosObjectStorage:
    """火山引擎 TOS 对象存储适配器（无重试，跨云失败直接向上抛）"""

    def __init__(self) -> None:
        self._client_instance: tos.TosClientV2 | None = None
        self._lock = threading.Lock()

    def _client(self) -> tos.TosClientV2:
        if self._client_instance is None:
            with self._lock:
                if self._client_instance is None:
                    self._client_instance = tos.TosClientV2(
                        settings.TOS_ACCESS_KEY,
                        settings.TOS_SECRET_KEY,
                        settings.TOS_ENDPOINT,
                        settings.TOS_REGION,
                        socket_timeout=settings.OBJECT_STORAGE_TIMEOUTS.socket_seconds,
                        connection_time=settings.OBJECT_STORAGE_TIMEOUTS.connect_seconds,
                    )
        return self._client_instance

    @property
    def _is_configured(self) -> bool:
        return all([
            settings.TOS_ENDPOINT,
            settings.TOS_ACCESS_KEY,
            settings.TOS_SECRET_KEY,
            settings.TOS_BUCKET,
        ])

    async def put_upload_file(
        self,
        storage_key: str,
        file: UploadFile,
        content_type: str | None = None,
    ) -> None:
        if not self._is_configured:
            raise RuntimeError("TOS 配置缺失，无法上传资产")

        effective_content_type = content_type or file.content_type or "application/octet-stream"
        client = self._client()
        bucket = settings.TOS_BUCKET

        def _upload() -> None:
            file.file.seek(0)
            client.put_object(
                bucket,
                storage_key,
                content=file.file,
                content_type=effective_content_type,
            )

        await asyncio.wait_for(
            asyncio.to_thread(_upload),
            timeout=settings.OBJECT_STORAGE_TIMEOUTS.operation_seconds,
        )
        logger.info("asset.tos.uploaded", storage_key=storage_key)

    async def put_file_path(
        self,
        storage_key: str,
        path: Path,
        content_type: str | None = None,
    ) -> None:
        if not self._is_configured:
            raise RuntimeError("TOS 配置缺失，无法上传文件")

        effective_content_type = content_type or "application/octet-stream"
        client = self._client()
        bucket = settings.TOS_BUCKET
        size = path.stat().st_size

        def _upload() -> None:
            with open(path, "rb") as f:
                client.put_object(
                    bucket,
                    storage_key,
                    content=f,
                    content_type=effective_content_type,
                    content_length=size,
                )

        await asyncio.wait_for(
            asyncio.to_thread(_upload),
            timeout=settings.OBJECT_STORAGE_TIMEOUTS.operation_seconds,
        )
        logger.info("asset.tos.uploaded", storage_key=storage_key, size=size)

    async def get_object_bytes(self, storage_key: str) -> bytes:
        """下载对象为 bytes（图片 embedding 场景用），内部分块读取避免单次大 IO。"""
        if not self._is_configured:
            raise RuntimeError("TOS 配置缺失，无法下载资产")

        client = self._client()
        bucket = settings.TOS_BUCKET

        def _download() -> bytes:
            output = client.get_object(bucket, storage_key)
            chunks: list[bytes] = []
            while chunk := output.content.read(_CHUNK_SIZE):
                chunks.append(chunk)
            return b"".join(chunks)

        content = await asyncio.wait_for(
            asyncio.to_thread(_download),
            timeout=settings.OBJECT_STORAGE_TIMEOUTS.operation_seconds,
        )
        logger.info("asset.tos.downloaded", storage_key=storage_key, size=len(content))
        return content

    async def download_to_path(self, storage_key: str, dest: Path) -> None:
        """流式下载对象直接写入磁盘，全程不在内存中缓存（视频/大附件落盘场景）。
        若下载失败或超时，删除已写入的残缺文件，再重新抛出异常。
        """
        if not self._is_configured:
            raise RuntimeError("TOS 配置缺失，无法下载资产")

        client = self._client()
        bucket = settings.TOS_BUCKET

        def _download_file() -> None:
            output = client.get_object(bucket, storage_key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                while chunk := output.content.read(_CHUNK_SIZE):
                    f.write(chunk)

        try:
            await asyncio.wait_for(
                asyncio.to_thread(_download_file),
                timeout=settings.OBJECT_STORAGE_TIMEOUTS.operation_seconds,
            )
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                dest.unlink()
            raise

        logger.info("asset.tos.downloaded_to_path", storage_key=storage_key, dest=str(dest))

    def presigned_get_url(self, storage_key: str, *, expires: int | None = None) -> str:
        if not self._is_configured:
            raise RuntimeError("TOS 配置缺失，无法生成预览链接")

        expiry = expires if expires is not None else settings.TOS_PRESIGN_EXPIRY_SECONDS
        result = self._client().pre_signed_url(
            http_method=tos.HttpMethodType.Http_Method_Get,
            bucket=settings.TOS_BUCKET,
            key=storage_key,
            expires=expiry,
        )
        return result.signed_url


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    return name or "upload.bin"


object_storage = TosObjectStorage()
