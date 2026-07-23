"""Browser-blocked side effects (frame + persist). Terminal DONE owned by SSE."""

from __future__ import annotations

from app.agent.chat.stream.frames import StreamFrameType, create_stream_frame
from app.agent.chat.turn.persistence import finalize_assistant
from app.agent.chat.turn.session import ChatTurnSession
from app.agent.chat.turn.trace import log_stage
from app.agent.runtime.turn_engine.events import ToolFinished, TurnEvent, TurnEventKind
from app.agent.runtime.turn_engine.subscribers import TurnEmit
from app.contracts.metadata import AssistantMessageMetadata
from app.server.chat.domain.stream_enums import TerminationReason
from app.server.infra.config import settings


class ChatBrowserBlockedSubscriber:
    """Emit BROWSER_BLOCKED and persist partial answer; no DONE/ERROR."""

    barrier_events = frozenset()
    broadcast_events = frozenset({TurnEventKind.TOOL_FINISHED})

    def __init__(self, session: ChatTurnSession) -> None:
        self._session = session

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        if not isinstance(event, ToolFinished) or event.error_class != "browser_blocked":
            return
        session = self._session
        blocked_text = ""
        if session.recorder.last_model_message is not None:
            blocked_text = str(session.recorder.last_model_message.content or "").strip()
        await emit(
            create_stream_frame(
                type=StreamFrameType.BROWSER_BLOCKED,
                turn_id=session.turn_id,
                message=blocked_text,
                screenshot_url=None,
                conversation_id=session.conversation_id,
            )
        )
        if blocked_text and not session.recorder.final_persisted:
            await finalize_assistant(
                persistence=session.persistence,
                ctx=session.ctx,
                turn_id=session.turn_id,
                content=blocked_text,
                ai_message=session.recorder.last_model_message,
                metadata=AssistantMessageMetadata(
                    turn_context=session.turn_context_meta,
                    tool_steps=session.recorder.tool_steps,
                    tool_audit=session.tool_audit if settings.CHAT_PERSIST_TOOL_AUDIT else [],
                ),
                user_id=session.user_id,
                conversation_id=session.conversation_id,
            )
            session.recorder.final_persisted = True
        session.terminated_by = TerminationReason.COMPLETED
        session.browser_blocked = True
        session.usage_collector.note_termination(
            terminated_by=TerminationReason.COMPLETED,
            message=None,
        )
        log_stage(
            "browser_blocked.exit",
            tool_name=event.tool_name,
            has_screenshot=False,
            model_text_len=len(blocked_text),
            step_index=event.step_index,
        )
