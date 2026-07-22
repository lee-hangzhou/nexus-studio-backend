from __future__ import annotations

from app.agent.chat.llm.registry import ModelSpec
from app.server.chat.services.vision.mime import is_image_mime
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode

__all__ = ["assert_vision_turn_allowed", "is_image_mime"]


def assert_vision_turn_allowed(*, spec: ModelSpec, image_attachment_ids: list[int]) -> None:
    if not image_attachment_ids:
        return
    if not spec.supports_vision:
        raise AppError(
            ErrorCode.INVALID_PARAMS,
            "当前模型不支持识图，无法发送图片附件",
        )
