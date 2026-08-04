"""apply_composer_prompt 成功后发出 composer_prompt_applied SSE 帧。"""

from __future__ import annotations

from app.agent.chat.tools.result import ToolResult, ToolResultProtocolError
from app.agent.runtime.stream.frames import StreamFrameType, create_stream_frame
from app.agent.runtime.turn_engine.events import ToolFinished, TurnEvent, TurnEventKind
from app.agent.runtime.turn_engine.subscribers import TurnEmit
from app.contracts.composer_prompt import ComposerPromptPayload
from app.server.chat.domain.stream_enums import StreamErrorCode
from app.server.infra.config import settings
from app.server.infra.logger import logger

APPLY_COMPOSER_PROMPT_TOOL = "apply_composer_prompt"


class PromptAssistantComposerPromptSubscriber:
    """仅 prompt_assistant：把 apply_composer_prompt 工具结果转成类型化写回帧。"""

    barrier_events: frozenset[TurnEventKind] = frozenset()
    broadcast_events: frozenset[TurnEventKind] = frozenset({TurnEventKind.TOOL_FINISHED})

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        """TOOL_FINISHED 且工具成功时解析载荷并发帧；失败发 ERROR，不伪装成功。"""
        if not isinstance(event, ToolFinished):
            return
        if event.tool_name != APPLY_COMPOSER_PROMPT_TOOL or event.tool_error:
            return
        try:
            tool_result = ToolResult.parse_tool_message(event.tool_result)
            if not tool_result.success:
                raise ToolResultProtocolError("apply_composer_prompt finished without success")
            payload = ComposerPromptPayload.model_validate_json(tool_result.output)
        except Exception as exc:
            logger.exception(
                "prompt_assistant.composer_prompt_applied.parse_failed",
                turn_id=event.turn_id,
                call_id=event.call_id,
                exc_info=exc,
            )
            await emit(
                create_stream_frame(
                    type=StreamFrameType.ERROR,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    code=StreamErrorCode.INTERNAL.value,
                    message="composer prompt apply payload invalid",
                    turn_id=event.turn_id,
                )
            )
            return
        await emit(
            create_stream_frame(
                type=StreamFrameType.COMPOSER_PROMPT_APPLIED,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                turn_id=event.turn_id,
                prompt=payload.prompt,
                content=[segment.model_dump(mode="json") for segment in payload.content],
                ref_asset_ids=list(payload.ref_asset_ids),
            )
        )
