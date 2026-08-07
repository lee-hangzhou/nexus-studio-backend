from __future__ import annotations

from typing import Literal

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode

MATERIAL_MAX_BYTES = 200 * 1024 * 1024

MIME_PREFIX_IMAGE = "image/"
MIME_PREFIX_VIDEO = "video/"
MIME_PREFIX_AUDIO = "audio/"

SOURCE_MANUAL_UPLOAD = "manual_upload"
SOURCE_GENERATE_MATERIAL = "generate_material"
SOURCE_AGENT_UPLOAD = "agent_upload"

DirectUploadSourceType = Literal["manual_upload", "generate_material", "agent_upload"]

KEY_ROOT_MANUAL = "assets"
KEY_ROOT_MATERIAL = "materials"
KEY_ROOT_AGENT = "agent"

ASSET_TYPE_IMAGE = "image"
ASSET_TYPE_VIDEO = "video"
ASSET_TYPE_AUDIO = "audio"


def asset_type_from_media_mime(mime_type: str) -> str:
    """仅接受 image/video/audio MIME，返回资产类型"""
    if mime_type.startswith(MIME_PREFIX_IMAGE):
        return ASSET_TYPE_IMAGE
    if mime_type.startswith(MIME_PREFIX_VIDEO):
        return ASSET_TYPE_VIDEO
    if mime_type.startswith(MIME_PREFIX_AUDIO):
        return ASSET_TYPE_AUDIO
    raise AppError(ErrorCode.INVALID_PARAMS, "仅支持图片、视频或音频")


def validate_direct_upload_bytes(*, size: int) -> None:
    """校验直传对象大小在限额内"""
    if size <= 0:
        raise AppError(ErrorCode.INVALID_PARAMS, "素材文件为空")
    if size > MATERIAL_MAX_BYTES:
        raise AppError(ErrorCode.INVALID_PARAMS, "素材文件过大")


def validate_direct_upload_media(*, mime_type: str, size: int) -> str:
    """校验直传 MIME 与大小，返回 asset_type"""
    validate_direct_upload_bytes(size=size)
    return asset_type_from_media_mime(mime_type)


def expected_key_prefix(
    *,
    source_type: DirectUploadSourceType,
    user_id: int,
    project_id: int | None,
) -> str:
    """按 source_type 生成 storage_key 必选前缀"""
    if source_type == SOURCE_MANUAL_UPLOAD:
        return f"{KEY_ROOT_MANUAL}/{user_id}/"
    if source_type == SOURCE_GENERATE_MATERIAL:
        return f"{KEY_ROOT_MATERIAL}/{user_id}/"
    if project_id is None:
        raise AppError(ErrorCode.INVALID_PARAMS, "agent_upload 必须提供 project_id")
    return f"{KEY_ROOT_AGENT}/{user_id}/{project_id}/"


def assert_storage_key_owned(
    *,
    storage_key: str,
    source_type: DirectUploadSourceType,
    user_id: int,
    project_id: int | None,
) -> None:
    """校验 storage_key 归属当前用户与来源"""
    prefix = expected_key_prefix(source_type=source_type, user_id=user_id, project_id=project_id)
    if not storage_key.startswith(prefix):
        raise AppError(ErrorCode.INVALID_PARAMS, "storage_key 与当前用户或来源不匹配")
    remainder = storage_key[len(prefix) :]
    if not remainder or remainder.startswith("/") or ".." in remainder.split("/"):
        raise AppError(ErrorCode.INVALID_PARAMS, "storage_key 非法")
