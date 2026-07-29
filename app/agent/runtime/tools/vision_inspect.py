"""视觉理解：turn / node 媒体 inspect 共享实现"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.chat.llm.model_catalog import model_catalog
from app.agent.chat.tools.result import ToolResult
from app.contracts.turn_content import TurnMediaType
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.infra.gateway import gateway_client
from app.server.infra.gateway_errors import GatewayChatError


class MediaInspectTask(StrEnum):
    """视觉理解任务类型"""

    DESCRIBE = "describe"
    REVERSE_PROMPT = "reverse_prompt"
    CUSTOM = "custom"


class _VisionTextPart(BaseModel):
    """网关 chat 文本 part"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str = Field(min_length=1)


class _VisionMediaUrl(BaseModel):
    """网关 chat 媒体 URL 壳"""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)


class _VisionImagePart(BaseModel):
    """网关 chat 图像 part"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["image_url"] = "image_url"
    image_url: _VisionMediaUrl


class _VisionVideoPart(BaseModel):
    """网关 chat 视频 part"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["video_url"] = "video_url"
    video_url: _VisionMediaUrl


_VisionContentPart = Annotated[
    _VisionTextPart | _VisionImagePart | _VisionVideoPart,
    Field(discriminator="type"),
]


class _VisionChatMessage(BaseModel):
    """网关 chat user message"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user"] = "user"
    content: list[_VisionContentPart] = Field(min_length=1)


class _VisionChatCompletionRequest(BaseModel):
    """非流式视觉 chat 请求体"""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    messages: list[_VisionChatMessage] = Field(min_length=1)
    stream: Literal[False] = False


class _GatewayChatMessage(BaseModel):
    """网关 chat 响应 message"""

    model_config = ConfigDict(extra="ignore")

    content: str


class _GatewayChatChoice(BaseModel):
    """网关 chat 响应 choice"""

    model_config = ConfigDict(extra="ignore")

    message: _GatewayChatMessage


class _GatewayChatCompletion(BaseModel):
    """网关 chat completion 响应"""

    model_config = ConfigDict(extra="ignore")

    choices: list[_GatewayChatChoice] = Field(min_length=1)


_TASK_PROMPTS = {
    MediaInspectTask.DESCRIBE: "Describe the visual content of the attached media in detail.",
    MediaInspectTask.REVERSE_PROMPT: (
        "Reverse-engineer a detailed image generation prompt that would recreate this image. "
        "Output only the prompt text."
    ),
}

VISUAL_MEDIA_TYPES = frozenset({TurnMediaType.IMAGE, TurnMediaType.VIDEO})

_VISION_UNAVAILABLE_CODES = frozenset(
    {
        int(ErrorCode.SERVICE_UNAVAILABLE),
        int(ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED),
        int(ErrorCode.GATEWAY_SUBMIT_ERROR),
    }
)


def media_type_from_mime(mime_type: str) -> TurnMediaType | None:
    """从非空 mime 推断图像/视频；无法识别返回 None"""
    if not mime_type:
        raise ValueError("mime_type is required")
    lowered = mime_type.lower()
    if lowered.startswith("image/"):
        return TurnMediaType.IMAGE
    if lowered.startswith("video/"):
        return TurnMediaType.VIDEO
    return None


def require_caption_model_id() -> str:
    """读取已配置的 caption 模型 id；空配置抛 ValueError"""
    model_id = settings.GATEWAY_CAPTION_MODEL.strip()
    if not model_id:
        raise ValueError("GATEWAY_CAPTION_MODEL is not configured")
    return model_id


def vision_fail_from_app_error(exc: AppError) -> str:
    """把已知网关 AppError 映射为 vision tool 失败；未知码向上抛"""
    code = int(exc.code)
    if code == int(ErrorCode.GATEWAY_PROTOCOL_ERROR):
        return ToolResult.fail("vision_protocol_error", detail=exc.message).to_tool_message()
    if code in _VISION_UNAVAILABLE_CODES:
        return ToolResult.fail("vision_unavailable", detail=exc.message).to_tool_message()
    if code == int(ErrorCode.GATEWAY_UPSTREAM_ERROR):
        return ToolResult.fail("vision_upstream_failed", detail=exc.message).to_tool_message()
    raise exc


def _vision_media_part(media_type: TurnMediaType, url: str) -> _VisionImagePart | _VisionVideoPart:
    """按媒体类型构造网关 content part"""
    if media_type == TurnMediaType.VIDEO:
        return _VisionVideoPart(video_url=_VisionMediaUrl(url=url))
    return _VisionImagePart(image_url=_VisionMediaUrl(url=url))


async def inspect_assets_with_vision(
    *,
    task: MediaInspectTask,
    instruction: str | None,
    asset_ids: list[int],
    media_types: list[TurnMediaType],
    preview_urls: list[str],
    result_extra: dict[str, object],
) -> str:
    """对已解析的视觉 URL 列表调用网关；成功时 result_extra 并入 output JSON"""
    if not asset_ids:
        return ToolResult.fail(
            "file_not_found",
            detail="no visual assets to inspect",
        ).to_tool_message()
    if not (len(asset_ids) == len(media_types) == len(preview_urls)):
        return ToolResult.fail(
            "vision_protocol_error",
            detail="asset lists length mismatch",
        ).to_tool_message()

    try:
        model_id = require_caption_model_id()
    except ValueError as exc:
        return ToolResult.fail("vision_misconfigured", detail=str(exc)).to_tool_message()

    for asset_id, media_type in zip(asset_ids, media_types, strict=True):
        if media_type not in VISUAL_MEDIA_TYPES:
            return ToolResult.fail(
                "file_not_allowed",
                detail=f"asset {asset_id} is not visual media",
            ).to_tool_message()
        if task == MediaInspectTask.REVERSE_PROMPT and media_type == TurnMediaType.VIDEO:
            return ToolResult.fail(
                "vision_unsupported_media",
                detail="reverse_prompt requires image assets",
            ).to_tool_message()

    needs_image = any(t == TurnMediaType.IMAGE for t in media_types)
    needs_video = any(t == TurnMediaType.VIDEO for t in media_types)
    try:
        if needs_image and not model_catalog.require_supports_vision(model_id):
            return ToolResult.fail(
                "vision_unsupported_media",
                detail=f"model {model_id} does not support vision input",
            ).to_tool_message()
        if needs_video and not model_catalog.require_supports_video_input(model_id):
            return ToolResult.fail(
                "vision_unsupported_media",
                detail=f"model {model_id} does not support video input",
            ).to_tool_message()
    except AppError as exc:
        if int(exc.code) == int(ErrorCode.INVALID_PARAMS):
            return ToolResult.fail(
                "vision_misconfigured",
                detail=exc.message,
            ).to_tool_message()
        raise

    if task == MediaInspectTask.CUSTOM:
        if instruction is None:
            raise TypeError("custom task requires instruction from tool schema")
        prompt = instruction.strip()
    else:
        prompt = _TASK_PROMPTS[task]
    content: list[_VisionContentPart] = [_VisionTextPart(text=prompt)]
    for url, media_type in zip(preview_urls, media_types, strict=True):
        content.append(_vision_media_part(media_type, url))

    request = _VisionChatCompletionRequest(
        model=model_id,
        messages=[_VisionChatMessage(content=content)],
    )
    try:
        payload = await gateway_client.openai_chat_completion(
            request.model_dump(mode="json"),
        )
    except AppError as exc:
        return vision_fail_from_app_error(exc)
    except GatewayChatError as exc:
        error_type = "vision_unavailable" if exc.retryable else "vision_protocol_error"
        return ToolResult.fail(error_type, detail=exc.detail).to_tool_message()

    try:
        parsed = _GatewayChatCompletion.model_validate(payload)
    except ValidationError as exc:
        return ToolResult.fail(
            "vision_protocol_error",
            detail=f"invalid vision response shape: {exc.error_count()} errors",
        ).to_tool_message()

    analysis = parsed.choices[0].message.content
    if not analysis.strip():
        return ToolResult.fail(
            "vision_empty_response",
            detail="empty vision response",
        ).to_tool_message()

    body = {"task": task.value, "analysis": analysis, **result_extra}
    return ToolResult.ok(json.dumps(body, ensure_ascii=False)).to_tool_message()
