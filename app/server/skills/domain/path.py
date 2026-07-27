from __future__ import annotations

from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.skills.domain.limits import MAX_PATH_DEPTH, MAX_SEGMENT_LEN


def normalize_skill_path(path: str) -> str:
    """规范化技能路径并校验 segment 规则"""
    raw = path.strip()
    if not raw:
        raise AppError(ErrorCode.INVALID_PARAMS, "skill path required")
    if raw.startswith("/"):
        raise AppError(ErrorCode.INVALID_PARAMS, "skill path must not start with /")
    if raw.endswith("/"):
        raise AppError(ErrorCode.INVALID_PARAMS, "skill path must not end with /")
    segments = raw.split("/")
    if any(not segment for segment in segments):
        raise AppError(ErrorCode.INVALID_PARAMS, "skill path must not contain empty segments")
    if ".." in segments or "." in segments:
        raise AppError(ErrorCode.INVALID_PARAMS, "skill path must not contain . or ..")
    if len(segments) > MAX_PATH_DEPTH:
        raise AppError(ErrorCode.INVALID_PARAMS, "skill path depth exceeds limit")
    for segment in segments:
        if len(segment) > MAX_SEGMENT_LEN:
            raise AppError(ErrorCode.INVALID_PARAMS, "skill path segment too long")
    return raw


def parent_path(path: str) -> str | None:
    """返回父路径，顶层节点返回 None"""
    normalized = normalize_skill_path(path)
    if "/" not in normalized:
        return None
    return normalized.rsplit("/", 1)[0]


def is_prefix_path(prefix: str, path: str) -> bool:
    """判断 prefix 是否为 path 的前缀路径"""
    normalized_prefix = normalize_skill_path(prefix)
    normalized_path = normalize_skill_path(path)
    if normalized_path == normalized_prefix:
        return True
    return normalized_path.startswith(f"{normalized_prefix}/")
