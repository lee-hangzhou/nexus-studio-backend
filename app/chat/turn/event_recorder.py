"""Shared persistence and usage recording for agent events."""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, ToolMessage

from app.chat.agent.events import AgentEvent, AgentEventType
from app.chat.agent.tool_recovery import INTERNAL_TOOL_NAME
from app.chat.tools.lc_tools import ChatToolContext
from app.chat.tools.ui_preview import sanitize_tool_step_preview
from app.chat.turn.guards import TurnGuards
from app.chat.turn.observation import TurnObservationContext
from app.chat.turn.persistence import finalize_assistant
from app.chat.turn.trace import log_stage
from app.contracts.metadata import (
    AssistantMessageMetadata,
    InvalidToolCallMetadata,
    ToolRecoveryMetadata,
    ToolRequestMetadata,
    ToolResultMetadata,
    ToolStepMetadata,
)
from app.core.config import settings
from app.core.logger import logger


@dataclass
class TurnAgentEventRecorder:
    observation: TurnObservationContext
    tool_steps: list[ToolStepMetadata] = field(default_factory=list)
    pending_recovery_tools: set[str] = field(default_factory=set)
    final_persisted: bool = False
    last_model_message: AIMessage | None = None
    last_step_index: int = 0

    async def record(
        self,
        event: AgentEvent,
        *,
        ctx: ChatToolContext | None = None,
        guards: TurnGuards | None = None,
    ) -> str | None:
        if event.type == AgentEventType.TOOL_STARTED:
            self.observation.usage_collector.note_tool_start(
                step_index=event.step_index,
                call_id=(event.call_id or "").strip(),
                name=event.tool_name or "",
                args=event.tool_args or {},
            )
            return None

        if event.type == AgentEventType.MODEL_STEP_FINISHED and event.ai_message is not None:
            if guards is not None:
                guards.on_model_step_finished()
            await self._record_model_step(event, ctx=ctx)
            return None

        if event.type == AgentEventType.TOOL_FINISHED and event.tool_result is not None:
            await self._record_tool_finished(event)
            if guards is None:
                return None
            return guards.on_tool_finished(event.tool_name or "", event.error_class)

        return None

    async def _record_model_step(
        self,
        event: AgentEvent,
        *,
        ctx: ChatToolContext | None,
    ) -> None:
        ai_message = event.ai_message
        assert ai_message is not None
        self.last_model_message = ai_message
        self.last_step_index = event.step_index
        self.observation.usage_collector.note_model_step(
            step_index=event.step_index,
            tool_calls=[str(call.get("name") or "") for call in (ai_message.tool_calls or [])],
            content_len=len(str(ai_message.content or "")),
        )
        recovery_calls = [
            call
            for call in (ai_message.tool_calls or [])
            if str(call.get("name") or "") == INTERNAL_TOOL_NAME
        ]
        for call in recovery_calls:
            args = call.get("args") or {}
            log_stage(
                "tool_recovery.detected",
                model=self.observation.model_key,
                tool_name=str(args.get("original_tool") or "unknown"),
                error_code=str(args.get("error_code") or "invalid_arguments"),
                fields=list(args.get("fields") or []),
                raw_length=int(args.get("raw_length") or 0),
                recovery_attempt=int(args.get("recovery_attempt") or 1),
                step_index=event.step_index,
            )
        invalid_tool_calls = [
            InvalidToolCallMetadata(
                call_id=item.call_id,
                name=item.name,
                parse_error=item.parse_error,
                raw_length=len(item.raw_arguments),
            )
            for item in event.invalid_tool_calls
        ]
        request_meta = ToolRequestMetadata(
            turn_context=self.observation.turn_context,
            invalid_tool_calls=invalid_tool_calls,
            stream=self.observation.stream_meta,
        )
        if ai_message.tool_calls:
            await self.observation.persistence.persist_tool_request(
                turn_id=self.observation.turn_id,
                step_index=event.step_index,
                ai_message=ai_message,
                metadata=request_meta,
            )
            return

        if event.invalid_tool_calls:
            return

        text = str(ai_message.content or "").strip()
        if not text or ctx is None:
            return

        if (
            self.observation.usage_collector.tool_recovery_count > 0
            and self.observation.usage_collector.recovery_outcome == "in_progress"
        ):
            self.observation.usage_collector.note_tool_recovery_corrected()

        await finalize_assistant(
            persistence=self.observation.persistence,
            ctx=ctx,
            turn_id=self.observation.turn_id,
            content=text,
            ai_message=ai_message,
            metadata=AssistantMessageMetadata(
                turn_context=self.observation.turn_context,
                invalid_tool_calls=invalid_tool_calls,
                tool_steps=self.tool_steps,
                tool_audit=self.observation.tool_audit
                if settings.CHAT_PERSIST_TOOL_AUDIT
                else [],
                recovery=ToolRecoveryMetadata(
                    recovered=self.observation.usage_collector.tool_recovery_count > 0,
                    recovery_type=(
                        "tool_call"
                        if self.observation.usage_collector.tool_recovery_count > 0
                        else None
                    ),
                ),
            ),
            user_id=self.observation.user_id,
            conversation_id=self.observation.conversation_id,
        )
        self.final_persisted = True

    async def _record_tool_finished(self, event: AgentEvent) -> None:
        call_id = (event.call_id or "").strip()
        tool_preview = (event.tool_result or "")[:2000]
        self.observation.usage_collector.note_tool_finish(
            step_index=event.step_index,
            call_id=call_id,
            name=event.tool_name or "",
            args=event.tool_args,
            ok=not event.tool_error,
            error_type=event.error_class,
            preview=tool_preview,
        )
        if event.synthetic:
            self.observation.usage_collector.note_tool_recovery()
            self.pending_recovery_tools.add(event.tool_name or "unknown")
            log_stage(
                "tool_recovery.feedback_emitted",
                model=self.observation.model_key,
                tool_name=event.tool_name or "unknown",
                error_code=event.error_class or "invalid_arguments",
                step_index=event.step_index,
            )
        elif not event.tool_error and (event.tool_name or "") in self.pending_recovery_tools:
            self.pending_recovery_tools.discard(event.tool_name or "")
            self.observation.usage_collector.note_tool_recovery_corrected()
            log_stage(
                "tool_recovery.corrected",
                model=self.observation.model_key,
                tool_name=event.tool_name or "",
                step_index=event.step_index,
            )
        if not call_id:
            logger.warning(
                "chat.turn.tool_missing_call_id",
                conversation_id=self.observation.conversation_id,
                turn_id=self.observation.turn_id,
                tool_name=event.tool_name,
                stream_phase=self.observation.phase,
            )
            return

        tool_message = ToolMessage(
            content=event.tool_result,
            tool_call_id=call_id,
            name=event.tool_name or "tool",
        )
        await self.observation.persistence.persist_tool_result(
            turn_id=self.observation.turn_id,
            tool_message=tool_message,
            meta=ToolResultMetadata(
                name=event.tool_name,
                call_id=call_id,
                error_class=event.error_class,
                synthetic=event.synthetic,
                recovery_reason=event.error_class if event.synthetic else None,
                recovery_attempt=event.recovery_attempt if event.synthetic else None,
                stream=self.observation.stream_meta,
            ),
        )
        self.tool_steps.append(
            ToolStepMetadata(
                call_id=call_id,
                name=event.tool_name,
                args=event.tool_args or {},
                ok=not event.tool_error,
                error_type=event.error_class,
                result_preview=sanitize_tool_step_preview(
                    event.tool_name or "",
                    (event.tool_result or "")[:2000],
                    ok=not event.tool_error,
                ),
                synthetic=event.synthetic,
            )
        )
