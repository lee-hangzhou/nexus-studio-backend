"""Map agent events to SSE stream frames."""

from __future__ import annotations

from app.agent.chat.agent.events import AgentEventType
from app.agent.chat.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.agent.chat.tools.judgment_gate import is_upgrade_protocol_tool
from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview
from app.server.infra.config import settings
from app.server.chat.domain.stream_enums import StreamErrorCode, normalize_stream_error_code


def chunk_text(text: str, *, size: int = 160) -> list[str]:
    if not text:
        return []
    return [text[index : index + size] for index in range(0, len(text), size)]


def frames_from_agent_event(event, *, turn_id: str) -> list[StreamFrame]:
    frames: list[StreamFrame] = []
    if event.type == AgentEventType.MODEL_TOKEN and event.text:
        frames.append(
            create_stream_frame(
                type=StreamFrameType.TOKEN,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                channel=event.channel,
                text=event.text,
            )
        )
    elif event.type == AgentEventType.TOOL_STARTED:
        if is_upgrade_protocol_tool(event.tool_name):
            return frames
        frames.append(
            create_stream_frame(
                type=StreamFrameType.TOOL_START,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                call_id=event.call_id,
                name=event.tool_name,
                args=event.tool_args,
            )
        )
    elif event.type == AgentEventType.TOOL_FINISHED:
        if is_upgrade_protocol_tool(event.tool_name):
            return frames
        frames.append(
            create_stream_frame(
                type=StreamFrameType.TOOL_END,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                call_id=event.call_id,
                name=event.tool_name,
                ok=not event.tool_error,
                preview=(
                    sanitize_tool_step_preview(
                        event.tool_name,
                        event.tool_result,
                        ok=not event.tool_error,
                    )
                ),
                data={"error_code": event.error_class} if event.tool_error else {},
            )
        )
    elif event.type == AgentEventType.TURN_FAILED:
        error_code = normalize_stream_error_code(
            event.error_class,
            fallback=StreamErrorCode.TURN_FAILED,
        )
        frames.append(
            create_stream_frame(
                type=StreamFrameType.ERROR,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                code=error_code,
                message=event.error,
            )
        )
    return frames
