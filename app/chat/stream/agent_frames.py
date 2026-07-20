"""Map agent events to SSE stream frames."""

from __future__ import annotations

from app.chat.agent.events import AgentEventType
from app.chat.stream.frames import StreamFrame, StreamFrameType, create_stream_frame
from app.chat.tools.ui_preview import sanitize_tool_step_preview
from app.core.config import settings
from app.domain.chat.enums import StreamErrorCode, normalize_stream_error_code


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
                channel=event.channel or "answer",
                text=event.text,
            )
        )
    elif event.type == AgentEventType.TOOL_STARTED:
        frames.append(
            create_stream_frame(
                type=StreamFrameType.TOOL_START,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                call_id=event.call_id or "",
                name=event.tool_name or "",
                args=event.tool_args or {},
            )
        )
    elif event.type == AgentEventType.TOOL_FINISHED:
        recoverable = bool(event.synthetic)
        frames.append(
            create_stream_frame(
                type=StreamFrameType.TOOL_END,
                protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                call_id=event.call_id or "",
                name=event.tool_name or "",
                ok=not event.tool_error,
                preview=(
                    "参数异常，正在自动修复"
                    if recoverable
                    else sanitize_tool_step_preview(
                        event.tool_name or "",
                        (event.tool_result or "")[:2000],
                        ok=not event.tool_error,
                    )
                ),
                data={
                    "synthetic": recoverable,
                    "recoverable": recoverable,
                    "recovery_attempt": event.recovery_attempt,
                    "error_code": event.error_class,
                }
                if recoverable
                else {},
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
                message=event.error or "turn failed",
            )
        )
    return frames
