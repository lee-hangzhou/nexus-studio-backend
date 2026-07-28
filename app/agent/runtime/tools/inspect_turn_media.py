from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent.chat.tools.result import ToolResult
from app.agent.chat.llm.model_catalog import model_catalog
from app.agent.runtime.ports import get_assets_port
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


class InspectTurnMediaRefInput(BaseModel):
    """单条视觉媒体引用"""

    model_config = ConfigDict(extra="forbid")

    asset_id: int = Field(ge=1)


class InspectTurnMediaInput(BaseModel):
    """inspect_turn_media 工具入参"""

    model_config = ConfigDict(extra="forbid")

    refs: list[InspectTurnMediaRefInput] = Field(min_length=1, max_length=20)
    task: MediaInspectTask
    instruction: str | None = None

    @model_validator(mode="after")
    def _validate_custom(self) -> InspectTurnMediaInput:
        """custom 任务必须带 instruction"""
        if self.task == MediaInspectTask.CUSTOM:
            if self.instruction is None or not self.instruction.strip():
                raise ValueError("instruction is required when task is custom")
        return self


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
    """网关 OpenAI 兼容非流式 vision chat 请求"""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)
    messages: list[_VisionChatMessage] = Field(min_length=1)
    stream: Literal[False] = False


class _GatewayChatMessage(BaseModel):
    """网关 chat completion message 片段"""

    model_config = ConfigDict(extra="ignore")

    content: str


class _GatewayChatChoice(BaseModel):
    """网关 chat completion choice 片段"""

    model_config = ConfigDict(extra="ignore")

    message: _GatewayChatMessage


class _GatewayChatCompletion(BaseModel):
    """网关 chat completion 响应（fail-closed）"""

    model_config = ConfigDict(extra="ignore")

    choices: list[_GatewayChatChoice] = Field(min_length=1)


_TASK_PROMPTS = {
    MediaInspectTask.DESCRIBE: "Describe the visual content of the attached media in detail.",
    MediaInspectTask.REVERSE_PROMPT: (
        "Reverse-engineer a detailed image generation prompt that would recreate this image. "
        "Output only the prompt text."
    ),
}

_VISUAL_MEDIA_TYPES = frozenset({TurnMediaType.IMAGE, TurnMediaType.VIDEO})

_VISION_UNAVAILABLE_CODES = frozenset(
    {
        int(ErrorCode.SERVICE_UNAVAILABLE),
        int(ErrorCode.GATEWAY_QUOTA_OR_RATE_LIMITED),
        int(ErrorCode.GATEWAY_SUBMIT_ERROR),
    }
)


def _vision_fail_from_app_error(exc: AppError) -> str:
    """把网关 AppError 映射为可区分的 vision tool 失败码"""
    code = int(exc.code)
    if code == int(ErrorCode.GATEWAY_PROTOCOL_ERROR):
        return ToolResult.fail("vision_protocol_error", detail=exc.message).to_tool_message()
    if code in _VISION_UNAVAILABLE_CODES:
        return ToolResult.fail("vision_unavailable", detail=exc.message).to_tool_message()
    if code == int(ErrorCode.GATEWAY_UPSTREAM_ERROR):
        return ToolResult.fail("vision_upstream_failed", detail=exc.message).to_tool_message()
    return ToolResult.fail("vision_upstream_failed", detail=exc.message).to_tool_message()


def _vision_media_part(media_type: TurnMediaType, url: str) -> _VisionImagePart | _VisionVideoPart:
    """按媒体类型构造网关 vision part"""
    if media_type == TurnMediaType.VIDEO:
        return _VisionVideoPart(video_url=_VisionMediaUrl(url=url))
    return _VisionImagePart(image_url=_VisionMediaUrl(url=url))


def build_inspect_turn_media_tool(
    *,
    user_id: int,
    allowed_asset_ids: frozenset[int],
    asset_media_types: dict[int, TurnMediaType],
) -> StructuredTool:
    """构建本 turn 视觉理解工具"""

    async def inspect_turn_media(
        refs: list[InspectTurnMediaRefInput],
        task: MediaInspectTask,
        instruction: str | None = None,
    ) -> str:
        """对 allowlist 内图像/视频调用网关视觉理解"""
        if not allowed_asset_ids:
            return ToolResult.fail(
                "file_not_allowed",
                detail="no visual media references in this turn",
            ).to_tool_message()

        model_id = (settings.GATEWAY_CAPTION_MODEL or "").strip()
        if not model_id:
            return ToolResult.fail(
                "vision_misconfigured",
                detail="GATEWAY_CAPTION_MODEL is not configured",
            ).to_tool_message()

        ordered_urls: list[str] = []
        ordered_ids: list[int] = []
        ordered_types: list[TurnMediaType] = []
        for ref in refs:
            if ref.asset_id not in allowed_asset_ids:
                return ToolResult.fail(
                    "file_not_allowed",
                    detail=f"asset {ref.asset_id} is not in turn references",
                ).to_tool_message()
            if ref.asset_id not in asset_media_types:
                return ToolResult.fail(
                    "file_not_allowed",
                    detail=f"asset {ref.asset_id} media_type missing from turn index",
                ).to_tool_message()
            media_type = asset_media_types[ref.asset_id]
            if media_type not in _VISUAL_MEDIA_TYPES:
                return ToolResult.fail(
                    "file_not_allowed",
                    detail=f"asset {ref.asset_id} is not visual media",
                ).to_tool_message()
            if task == MediaInspectTask.REVERSE_PROMPT and media_type == TurnMediaType.VIDEO:
                return ToolResult.fail(
                    "vision_unsupported_media",
                    detail="reverse_prompt requires image assets",
                ).to_tool_message()
            asset = await get_assets_port().get_asset(user_id=user_id, asset_id=ref.asset_id)
            if asset is None or not asset.preview_url.strip():
                return ToolResult.fail(
                    "file_not_found",
                    detail=f"asset {ref.asset_id} not found",
                ).to_tool_message()
            ordered_urls.append(asset.preview_url)
            ordered_ids.append(ref.asset_id)
            ordered_types.append(media_type)

        needs_image = any(t == TurnMediaType.IMAGE for t in ordered_types)
        needs_video = any(t == TurnMediaType.VIDEO for t in ordered_types)
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

        prompt = (
            (instruction or "").strip()
            if task == MediaInspectTask.CUSTOM
            else _TASK_PROMPTS[task]
        )
        content: list[_VisionContentPart] = [_VisionTextPart(text=prompt)]
        for url, media_type in zip(ordered_urls, ordered_types, strict=True):
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
            return _vision_fail_from_app_error(exc)
        except GatewayChatError as exc:
            error_type = (
                "vision_unavailable"
                if exc.retryable
                else "vision_protocol_error"
            )
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

        return ToolResult.ok(
            json.dumps(
                {
                    "task": task.value,
                    "refs": [{"asset_id": aid} for aid in ordered_ids],
                    "analysis": analysis,
                },
                ensure_ascii=False,
            )
        ).to_tool_message()

    return StructuredTool.from_function(
        coroutine=inspect_turn_media,
        name="inspect_turn_media",
        description=(
            "Analyze image or video assets attached as this turn's references using the vision model. "
            "Pass refs=[{asset_id}, ...] from Turn References. "
            "task=describe|reverse_prompt|custom; custom requires instruction."
        ),
        args_schema=InspectTurnMediaInput,
    )
