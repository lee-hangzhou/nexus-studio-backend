"""Record agent steps into TurnAgentEventRecorder."""

from __future__ import annotations

from app.agent.chat.turn.session import ChatTurnSession
from app.agent.runtime.agent.events import (
    ModelStepFinishedEvent,
    ToolFinishedEvent,
    ToolStartedEvent,
)
from app.agent.runtime.turn_engine.events import (
    ModelStepFinished,
    ToolFinished,
    ToolStarted,
    TurnEvent,
    TurnEventKind,
)
from app.agent.runtime.turn_engine.subscribers import TurnEmit


class ChatRecordingSubscriber:
    """Mirror tool/model step events into the turn recorder."""

    barrier_events = frozenset()
    broadcast_events = frozenset(
        {
            TurnEventKind.TOOL_FINISHED,
            TurnEventKind.TOOL_STARTED,
            TurnEventKind.MODEL_STEP_FINISHED,
        }
    )

    def __init__(self, session: ChatTurnSession) -> None:
        self._session = session

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        del emit
        session = self._session
        if isinstance(event, ToolStarted):
            await session.recorder.record(
                ToolStartedEvent(
                    turn_id=event.turn_id,
                    step_index=event.step_index,
                    call_id=event.call_id,
                    tool_name=event.tool_name,
                    tool_args=event.tool_args,
                )
            )
            return

        if isinstance(event, ModelStepFinished) and event.ai_message is not None:
            await session.recorder.record(
                ModelStepFinishedEvent(
                    turn_id=event.turn_id,
                    step_index=event.step_index,
                    ai_message=event.ai_message,
                    invalid_tool_calls=list(event.invalid_tool_calls),
                ),
                ctx=session.ctx,
                guards=None,
            )
            session.recovery_hook.last_step_index = event.step_index
            return

        if isinstance(event, ToolFinished):
            await session.recorder.record(
                ToolFinishedEvent(
                    turn_id=event.turn_id,
                    step_index=event.step_index,
                    call_id=event.call_id,
                    tool_name=event.tool_name,
                    tool_args={},
                    tool_result=event.tool_result,
                    tool_error=event.tool_error,
                    error_class=event.error_class,
                ),
                guards=None,
            )
