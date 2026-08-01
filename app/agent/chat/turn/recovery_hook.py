"""Chat empty / tool-exhaust recovery as TurnEngine RecoveryHook.

Owns persistence + answer-token publishing only. Terminal SSE (ERROR/DONE/CANCELLED)
is owned exclusively by SseTurnSubscriber via engine fail / TurnEnded.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.turn.empty_recovery import recover_empty_answer
from app.agent.chat.turn.event_recorder import TurnAgentEventRecorder
from app.agent.chat.turn.gate_emit import has_pending_user_gate
from app.agent.chat.turn.upgrade_invite_emit import has_pending_upgrade_invite
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.persistence import TurnPersistence, finalize_assistant
from app.agent.chat.turn.trace import log_stage
from app.agent.chat.turn.usage_log import TurnUsageCollector
from app.agent.chat.tools.lc_tools import ChatToolContext
from app.agent.runtime.stream.text import chunk_text
from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.agent.runtime.turn_engine.constants import TERMINATION_ERROR_MESSAGES
from app.agent.runtime.turn_engine.run_state import RunState
from app.contracts.metadata import (
    AssistantMessageMetadata,
    ToolRecoveryMetadata,
    TurnContextMetadata,
)
from app.server.infra.config import settings

PublishAnswerToken = Callable[[str], Awaitable[None]]


@dataclass
class ChatRecoveryHook:
    llm: Any
    system_prompt: str
    config: RunnableConfig
    guards: TurnGuards
    cancel_event: Any
    model_key: str
    turn_id: str
    persistence: TurnPersistence
    recorder: TurnAgentEventRecorder
    usage_collector: TurnUsageCollector
    ctx: ChatToolContext
    turn_context_meta: TurnContextMetadata
    tool_audit: list
    user_id: int
    conversation_id: int
    agent: CompiledStateGraph | None = None
    publish_answer_token: PublishAnswerToken | None = None
    recovery_used: bool = False
    recovery_exhausted: bool = False
    recovery_cancelled: bool = False
    force_interrupted: bool = False
    last_step_index: int = 0
    _answer_parts: list[str] = field(default_factory=list)

    def bind_publish(self, publish_answer_token: PublishAnswerToken) -> None:
        self.publish_answer_token = publish_answer_token

    async def after_model_step(self, event, *, state: RunState) -> TurnTerminatedBy | None:
        self.last_step_index = event.step_index
        return None

    async def before_complete(self, event, *, state: RunState) -> TurnTerminatedBy | None:
        if self.recorder.final_persisted:
            return None
        answer = "".join(state.answer_parts).strip()
        if answer:
            return None
        last = self.recorder.last_model_message
        if last is not None and not last.tool_calls and str(last.content or "").strip():
            return None
        # Invariant: UserGate interrupt may fire before tool.end, so tool_steps can be empty.
        # Must return INTERRUPTED before empty_recovery / GATEWAY_UPSTREAM_FAILED.
        # Gate SSE is emitted on TurnCompleted → Chat persistence (force_interrupted).
        if self.agent is not None and await has_pending_user_gate(self.agent, self.config):
            return TurnTerminatedBy.INTERRUPTED
        if self.agent is not None and await has_pending_upgrade_invite(
            self.agent, self.config
        ):
            return TurnTerminatedBy.INTERRUPTED
        if self.recorder.last_invalid_tool_calls and not (
            last is not None and last.tool_calls
        ):
            # 用户确认兜底: middleware 同轮重试后若仍无助手正文, 走 empty recovery, 不标 GATEWAY_UPSTREAM_FAILED
            messages = list(event.messages)
            ok = await self._attempt_recovery(
                messages,
                reason="invalid_tool_arguments",
                state=state,
            )
            if ok:
                return None
            if self.recovery_cancelled:
                return TurnTerminatedBy.CANCELLED
            if self.recovery_exhausted:
                return TurnTerminatedBy.AGENT_RECOVERY_EXHAUSTED
            return None
        if self.recorder.tool_steps:
            return TurnTerminatedBy.GATEWAY_UPSTREAM_FAILED
        messages = list(event.messages)
        ok = await self._attempt_recovery(messages, reason="empty_response", state=state)
        if ok:
            return None
        if self.recovery_cancelled:
            return TurnTerminatedBy.CANCELLED
        if self.recovery_exhausted:
            return TurnTerminatedBy.AGENT_RECOVERY_EXHAUSTED
        return None

    async def on_stream_break(
        self,
        terminated_by: TurnTerminatedBy,
        *,
        state: RunState,
        agent: CompiledStateGraph,
        runnable_config: RunnableConfig,
    ) -> TurnTerminatedBy | None:
        if terminated_by not in {
            TurnTerminatedBy.GUARD_ERROR,
            TurnTerminatedBy.MAX_TOOLS,
        }:
            return None
        if self.cancel_event.is_set():
            return None
        snap = await agent.aget_state(runnable_config)
        messages = list(snap.values.get("messages") or [])
        ok = await self._attempt_recovery(
            messages,
            reason="tool_recovery_exhausted",
            state=state,
        )
        if ok:
            return TurnTerminatedBy.COMPLETED
        if self.recovery_cancelled:
            return TurnTerminatedBy.CANCELLED
        if self.recovery_exhausted:
            return TurnTerminatedBy.AGENT_RECOVERY_EXHAUSTED
        return terminated_by

    async def _attempt_recovery(
        self,
        messages: list[Any],
        *,
        reason: str,
        state: RunState,
    ) -> bool:
        assert self.publish_answer_token is not None
        self.recovery_used = True
        remaining_steps = max(0, self.guards.max_model_steps - self.guards.model_steps_used)
        max_attempts = min(settings.CHAT_EMPTY_RECOVERY_ATTEMPTS, remaining_steps)
        if self.cancel_event.is_set() or not self.guards.check_wall_clock():
            max_attempts = 0
        recovery_timeout = max(
            1,
            min(
                settings.CHAT_EMPTY_RECOVERY_TIMEOUT_SEC,
                int(self.guards.remaining_wall_clock_seconds()),
            ),
        )
        log_stage(
            "empty_recovery.started",
            model=self.model_key,
            reason=reason,
            max_attempts=max_attempts,
            model_steps=self.guards.model_steps_used,
        )
        result = await recover_empty_answer(
            self.llm,
            messages,
            config=self.config,
            system_prompt=self.system_prompt,
            max_attempts=max_attempts,
            timeout_sec=recovery_timeout,
            cancel_event=self.cancel_event,
        )
        for offset in range(result.attempts):
            self.guards.on_model_step_finished()
            self.usage_collector.note_model_step(
                step_index=self.last_step_index + offset + 1,
                tool_calls=[],
                content_len=len(result.text or "") if offset == result.attempts - 1 else 0,
            )
        self.usage_collector.note_empty_recovery(
            attempts=result.attempts,
            succeeded=bool(result.text),
            failure_reason=result.failure_reason,
        )
        if result.failure_reason == "cancelled" or self.cancel_event.is_set():
            await self.persistence.persist_cancelled_close(
                turn_id=self.turn_id,
                step_index=self.last_step_index,
            )
            self.recorder.final_persisted = True
            self.recovery_cancelled = True
            self.usage_collector.note_termination(
                terminated_by="error",
                message="user cancelled during recovery",
            )
            return False
        if result.text:
            log_stage(
                "empty_recovery.succeeded",
                model=self.model_key,
                reason=reason,
                attempts=result.attempts,
                model_steps=self.guards.model_steps_used,
                duration_ms=result.duration_ms,
                answer_chars=len(result.text),
            )
            for piece in chunk_text(result.text):
                await self.publish_answer_token(piece)
            await finalize_assistant(
                persistence=self.persistence,
                ctx=self.ctx,
                turn_id=self.turn_id,
                content=result.text,
                ai_message=AIMessage(content=result.text),
                metadata=AssistantMessageMetadata(
                    turn_context=self.turn_context_meta,
                    tool_steps=self.recorder.tool_steps,
                    tool_audit=self.tool_audit if settings.CHAT_PERSIST_TOOL_AUDIT else [],
                    recovery=ToolRecoveryMetadata(
                        recovered=True,
                        recovery_type="empty_response",
                        recovery_reason=reason,
                        recovery_attempts=result.attempts,
                    ),
                ),
                user_id=self.user_id,
                conversation_id=self.conversation_id,
            )
            self.recorder.final_persisted = True
            self.usage_collector.note_termination(terminated_by="completed", message=None)
            return True

        log_stage(
            "empty_recovery.failed",
            model=self.model_key,
            reason=reason,
            attempts=result.attempts,
            model_steps=self.guards.model_steps_used,
            failure_reason=result.failure_reason,
            duration_ms=result.duration_ms,
        )
        friendly_message = TERMINATION_ERROR_MESSAGES[TurnTerminatedBy.AGENT_RECOVERY_EXHAUSTED]
        await finalize_assistant(
            persistence=self.persistence,
            ctx=self.ctx,
            turn_id=self.turn_id,
            content=friendly_message,
            ai_message=AIMessage(content=friendly_message),
            metadata=AssistantMessageMetadata(
                turn_context=self.turn_context_meta,
                tool_steps=self.recorder.tool_steps,
                tool_audit=self.tool_audit if settings.CHAT_PERSIST_TOOL_AUDIT else [],
                recovery=ToolRecoveryMetadata(
                    recovered=True,
                    recovery_type="empty_response",
                    recovery_reason=reason,
                    recovery_attempts=result.attempts,
                    recovery_failure_reason=result.failure_reason,
                    recovery_exhausted=True,
                ),
            ),
            user_id=self.user_id,
            conversation_id=self.conversation_id,
        )
        self.recorder.final_persisted = True
        self.recovery_exhausted = True
        self.usage_collector.note_termination(
            terminated_by="error",
            message="agent recovery exhausted",
        )
        return False
