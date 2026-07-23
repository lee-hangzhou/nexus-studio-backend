from __future__ import annotations

from collections.abc import Callable

from app.agent.runtime.stream.frames import StreamFrameType, create_stream_frame
from app.agent.runtime.tools.result import summarize_tool_result
from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.agent.runtime.turn_engine.constants import TERMINATION_ERROR_MESSAGES
from app.agent.runtime.turn_engine.events import (
    ModelToken,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnEvent,
    TurnEventKind,
    TurnFailed,
    TurnInterrupted,
)
from app.agent.runtime.turn_engine.interrupt_model import parse_pending_tool_actions
from app.agent.runtime.turn_engine.subscribers import TurnEmit
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.server.chat.domain.stream_enums import StreamErrorCode, TokenChannel, normalize_stream_error_code
from app.server.infra.config import settings

ToolPreviewFn = Callable[[str, str, bool], str]


def _default_preview(tool_name: str, result: str, ok: bool) -> str:
    return summarize_tool_result(tool_name, result, ok=ok)


class SseTurnSubscriber:
    """Sole owner of SSE protocol frames for a turn (token/tool/error/done/pending)."""

    barrier_events = frozenset({TurnEventKind.TURN_INTERRUPTED, TurnEventKind.TURN_FAILED})
    broadcast_events = frozenset({
        TurnEventKind.MODEL_TOKEN,
        TurnEventKind.TOOL_STARTED,
        TurnEventKind.TOOL_FINISHED,
        TurnEventKind.TURN_ENDED,
    })

    def __init__(
        self,
        *,
        policy: SseTerminalPolicy | None = None,
        preview_tool_result: ToolPreviewFn | None = None,
    ) -> None:
        self._policy = policy or SseTerminalPolicy()
        self._preview = preview_tool_result or _default_preview
        self._failed_emitted = False
        self._done_emitted = False

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        if isinstance(event, ModelToken):
            channel = event.channel
            if isinstance(channel, str):
                channel = TokenChannel(channel)
            await emit(
                create_stream_frame(
                    type=StreamFrameType.TOKEN,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    channel=channel,
                    text=event.text,
                )
            )
            return

        if isinstance(event, ToolStarted):
            await emit(
                create_stream_frame(
                    type=StreamFrameType.TOOL_START,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    call_id=event.call_id,
                    name=event.tool_name,
                    args=event.tool_args,
                )
            )
            return

        if isinstance(event, ToolFinished):
            preview = self._preview(
                event.tool_name,
                event.tool_result,
                not event.tool_error,
            )
            await emit(
                create_stream_frame(
                    type=StreamFrameType.TOOL_END,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    call_id=event.call_id,
                    name=event.tool_name,
                    ok=not event.tool_error,
                    preview=preview,
                    data={"error_code": event.error_class} if event.tool_error else {},
                )
            )
            return

        if isinstance(event, TurnInterrupted):
            actions = parse_pending_tool_actions(
                event.interrupts,
                pending_tool_calls=event.pending_tool_calls,
            )
            for action in actions:
                if not action.call_id or not action.name:
                    continue
                await emit(
                    create_stream_frame(
                        type=StreamFrameType.TOOL_PENDING,
                        turn_id=event.turn_id,
                        call_id=action.call_id,
                        name=action.name,
                        summary=action.summary or None,
                    )
                )
            return

        if isinstance(event, TurnFailed):
            self._failed_emitted = True
            code = normalize_stream_error_code(
                event.error_class,
                fallback=StreamErrorCode.TURN_FAILED,
            )
            message = event.error
            if event.error_class:
                try:
                    terminated = TurnTerminatedBy(event.error_class)
                    message = TERMINATION_ERROR_MESSAGES.get(terminated, event.error)
                except ValueError:
                    pass
            await emit(
                create_stream_frame(
                    type=StreamFrameType.ERROR,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    code=code,
                    message=message,
                    turn_id=event.turn_id,
                )
            )
            return

        if isinstance(event, TurnEnded):
            await self._handle_ended(event, emit=emit)

    async def _handle_ended(self, event: TurnEnded, *, emit: TurnEmit) -> None:
        if event.terminated_by == TurnTerminatedBy.INTERRUPTED:
            return
        if event.terminated_by == TurnTerminatedBy.CANCELLED:
            if self._policy.emit_done_after_failure:
                await self._emit_done(event.turn_id, emit=emit)
            return

        if event.terminated_by == TurnTerminatedBy.COMPLETED:
            if self._policy.emit_done_on_completed:
                await self._emit_done(event.turn_id, emit=emit)
            return

        if not (event.failed_emitted or self._failed_emitted):
            message = TERMINATION_ERROR_MESSAGES.get(
                event.terminated_by,
                str(event.terminated_by),
            )
            await emit(
                create_stream_frame(
                    type=StreamFrameType.ERROR,
                    protocol_version=settings.CHAT_SSE_PROTOCOL_VERSION,
                    code=normalize_stream_error_code(
                        event.terminated_by.value,
                        fallback=StreamErrorCode.TURN_FAILED,
                    ),
                    message=message,
                    turn_id=event.turn_id,
                )
            )
            self._failed_emitted = True

        if self._policy.emit_done_after_failure:
            await self._emit_done(event.turn_id, emit=emit)

    async def _emit_done(self, turn_id: str, *, emit: TurnEmit) -> None:
        if self._done_emitted:
            return
        self._done_emitted = True
        payload: dict = {
            "type": StreamFrameType.DONE,
            "turn_id": turn_id,
        }
        if self._policy.message_ids is not None:
            payload["message_ids"] = list(self._policy.message_ids())
        await emit(create_stream_frame(**payload))
