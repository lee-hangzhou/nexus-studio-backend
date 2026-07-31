"""Persist turn outcomes and emit title; never emit ERROR/DONE/CANCELLED."""

from __future__ import annotations

from app.agent.chat.conversation_title import (
    generate_conversation_title_via_llm,
    is_first_user_message,
    is_placeholder_title,
)
from app.agent.chat.stream.frames import StreamFrameType, create_stream_frame
from app.agent.chat.turn.gate_emit import emit_user_gates, has_pending_user_gate
from app.agent.chat.turn.gate_suspend import persist_gate_suspend_tool_results
from app.agent.chat.turn.persistence import finalize_assistant, finalize_published_deliverables
from app.agent.chat.turn.session import ChatTurnSession
from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.agent.runtime.turn.termination import termination_reason_for
from app.agent.runtime.turn_engine.events import (
    TurnCompleted,
    TurnEvent,
    TurnEventKind,
    TurnFailed,
    TurnInterrupted,
)
from app.agent.runtime.turn_engine.subscribers import TurnEmit
from app.contracts.metadata import AssistantMessageMetadata, ToolRecoveryMetadata
from app.server.chat.domain.stream_enums import TerminationReason
from app.server.infra.config import settings
from app.server.infra.logger import logger


class ChatPersistenceSubscriber:
    """Persistence + gate suspend + title task on turn lifecycle events."""

    barrier_events = frozenset(
        {
            TurnEventKind.TURN_COMPLETED,
            TurnEventKind.TURN_FAILED,
            TurnEventKind.TURN_INTERRUPTED,
        }
    )
    broadcast_events = frozenset()

    def __init__(self, session: ChatTurnSession) -> None:
        self._session = session

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        if isinstance(event, TurnInterrupted):
            session = self._session
            session.gate_interrupted = True
            session.terminated_by = TerminationReason.INTERRUPTED
            session.usage_collector.note_termination(
                terminated_by=TerminationReason.INTERRUPTED,
                message=None,
            )
            if session.agent is not None and session.runnable_config is not None:
                await persist_gate_suspend_tool_results(
                    agent=session.agent,
                    config=session.runnable_config,
                    observation=session.observation,
                )
            return

        if isinstance(event, TurnFailed):
            await self._handle_failed(event)
            return

        if isinstance(event, TurnCompleted):
            await self._handle_completed(event, emit=emit)
            return

    async def _handle_failed(self, event: TurnFailed) -> None:
        session = self._session
        session.terminated_by = TerminationReason.ERROR
        gateway_error = event.error_class in {
            "gateway_empty_stream",
            "gateway_upstream_timeout",
            "gateway_upstream_failed",
            "gateway_protocol_error",
            TurnTerminatedBy.GATEWAY_UPSTREAM_TIMEOUT.value,
            TurnTerminatedBy.GATEWAY_EMPTY_STREAM.value,
            TurnTerminatedBy.GATEWAY_UPSTREAM_FAILED.value,
            TurnTerminatedBy.GATEWAY_PROTOCOL_ERROR.value,
        }
        fail_message = (
            "模型服务暂时无响应，请重试" if gateway_error else (event.error or "turn failed")
        )
        await session.persistence.persist_turn_error(
            turn_id=session.turn_id,
            step_index=event.step_index or 0,
            content=fail_message,
            error_code=event.error_class,
        )
        session.usage_collector.note_failure(message=fail_message)
        reason = (
            termination_reason_for(event.error_class)
            if event.error_class
            else TerminationReason.ERROR
        )
        session.terminated_by = reason
        session.usage_collector.note_termination(terminated_by=reason, message=fail_message)

    async def _handle_completed(self, event: TurnCompleted, *, emit: TurnEmit) -> None:
        session = self._session
        if session.browser_blocked:
            return
        if session.recovery_hook.recovery_cancelled:
            session.terminated_by = TerminationReason.ERROR
            return

        deliverable_meta = AssistantMessageMetadata(
            turn_context=session.turn_context_meta,
            tool_steps=session.recorder.tool_steps,
            tool_audit=session.tool_audit if settings.CHAT_PERSIST_TOOL_AUDIT else [],
            recovery=ToolRecoveryMetadata(
                recovered=session.usage_collector.tool_recovery_count > 0,
                recovery_type=(
                    "tool_call" if session.usage_collector.tool_recovery_count > 0 else None
                ),
            ),
        )
        if not session.recorder.final_persisted:
            msg_id = await finalize_published_deliverables(
                persistence=session.persistence,
                ctx=session.ctx,
                turn_id=session.turn_id,
                metadata=deliverable_meta,
                user_id=session.user_id,
                conversation_id=session.conversation_id,
            )
            if msg_id is not None:
                session.recorder.final_persisted = True
        if (
            not session.recorder.final_persisted
            and session.recorder.last_model_message is not None
            and not session.recorder.last_model_message.tool_calls
        ):
            text = str(session.recorder.last_model_message.content or "").strip()
            if text:
                await finalize_assistant(
                    persistence=session.persistence,
                    ctx=session.ctx,
                    turn_id=session.turn_id,
                    content=text,
                    ai_message=session.recorder.last_model_message,
                    metadata=deliverable_meta,
                    user_id=session.user_id,
                    conversation_id=session.conversation_id,
                )
                session.recorder.final_persisted = True

        if (
            not session.recorder.final_persisted
            and session.agent is not None
            and session.runnable_config is not None
            and await has_pending_user_gate(session.agent, session.runnable_config)
        ):
            session.gate_interrupted = await emit_user_gates(
                session.agent,
                session.runnable_config,
                emit,
                turn_id=session.turn_id,
                conversation_id=session.conversation_id,
                model_key=session.model_key,
                workspace=session.workspace,
            )
            if session.gate_interrupted:
                await persist_gate_suspend_tool_results(
                    agent=session.agent,
                    config=session.runnable_config,
                    observation=session.observation,
                )
                session.terminated_by = TerminationReason.INTERRUPTED
                session.recovery_hook.force_interrupted = True
                session.usage_collector.note_termination(
                    terminated_by=TerminationReason.INTERRUPTED,
                    message=None,
                )
                return

        if not session.recorder.final_persisted:
            answer = (event.answer_text or "").strip()
            if not answer and not session.recorder.tool_steps:
                logger.warning(
                    "chat.stream_turn.empty_response",
                    conversation_id=session.conversation_id,
                    turn_id=session.turn_id,
                )

        if (
            not session.gate_interrupted
            and not session.recovery_hook.recovery_exhausted
            and not session.recovery_hook.recovery_cancelled
        ):
            session.terminated_by = TerminationReason.COMPLETED
            session.usage_collector.note_termination(
                terminated_by=TerminationReason.COMPLETED,
                message=None,
            )

        schedule_title = (
            session.terminated_by == TerminationReason.COMPLETED
            and session.recorder.final_persisted
            and not session.recovery_hook.recovery_used
            and await is_first_user_message(session.conversation_id)
            and is_placeholder_title(session.conversation.title)
        )
        logger.info(
            "chat.stream_turn.completed",
            conversation_id=session.conversation_id,
            turn_id=session.turn_id,
            final_persisted=session.recorder.final_persisted,
            message_ids=session.persistence.message_ids,
            terminated_by=session.terminated_by,
        )
        # 必须在 DONE 之前推送，否则流已结束前端收不到标题
        if schedule_title:
            try:
                title_result = await generate_conversation_title_via_llm(
                    user_id=session.user_id,
                    conversation_id=session.conversation_id,
                    user_content=session.content,
                    turn_asset_ids=session.turn_asset_ids,
                    model_key=session.model_key,
                )
            except Exception:
                logger.exception(
                    "chat.conversation_title.emit_failed",
                    conversation_id=session.conversation_id,
                )
            else:
                if (
                    title_result.applied
                    and title_result.title
                    and title_result.updated_at
                ):
                    await emit(
                        create_stream_frame(
                            type=StreamFrameType.CONVERSATION_TITLE,
                            conversation_id=session.conversation_id,
                            title=title_result.title,
                            updated_at=title_result.updated_at,
                        )
                    )
