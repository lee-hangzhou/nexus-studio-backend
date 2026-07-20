from __future__ import annotations

from app.chat.llm.registry import ModelSpec
from app.exceptions.base import AppError
from app.exceptions.codes import ErrorCode


def is_image_mime(mime_type: str) -> bool:
    return (mime_type or "").lower().startswith("image/")


def assert_vision_turn_allowed(*, spec: ModelSpec, image_attachment_ids: list[int]) -> None:
    if not image_attachment_ids:
        return
    if not spec.supports_vision:
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            "当前模型不支持识图，无法发送图片附件",
        )
