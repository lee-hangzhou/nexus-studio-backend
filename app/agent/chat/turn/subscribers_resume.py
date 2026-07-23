"""Chat gate resume / abort subscribers (side effects only; SSE terminals via SseTurnSubscriber)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.gate import turn_auth as turn_auth_store
from app.agent.chat.gate.pending import clear_gate_pending, get_gate_pending
from app.agent.chat.tools.browser_names import REQUEST_USER_GATE
from app.agent.chat.tools.lc_tools import ChatToolContext
from app.agent.chat.tools.result import BROWSER_ERROR, LOGIN_FAILED, ToolResult, ToolResultProtocolError
from app.agent.chat.turn.event_recorder import TurnAgentEventRecorder
from app.agent.chat.turn.gate_emit import emit_user_gates
from app.agent.chat.turn.gate_suspend import persist_gate_suspend_tool_results
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.lifecycle import clear_turn_active
from app.agent.chat.turn.observation import TurnObservationContext
from app.agent.chat.turn.observation_store import clear_turn_observation_snapshot
from app.agent.chat.turn.persistence import TurnPersistence
from app.agent.chat.turn.usage_log import TerminatedBy
from app.agent.runtime.agent.events import (
    ModelStepFinishedEvent,
    ToolFinishedEvent,
    ToolStartedEvent,
)
from app.agent.runtime.turn.enums import TurnTerminatedBy
from app.agent.runtime.turn_engine.events import (
    ModelStepFinished,
    ToolFinished,
    ToolStarted,
    TurnCompleted,
    TurnEvent,
    TurnEventKind,
    TurnFailed,
    TurnInterrupted,
)
from app.agent.runtime.turn_engine.run_state import RunState
from app.agent.runtime.turn_engine.subscribers import TurnEmit
from app.server.chat.persistence.conversations import ChatConversations

_GATE_RESUME_FATAL_ERRORS = frozenset({BROWSER_ERROR, LOGIN_FAILED})


@dataclass
class ChatResumeControlHook:
    """Signals post-complete interrupt / fail so SSE owns terminals."""

    force_interrupted: bool = False
    force_failed: tuple[str, str] | None = None

    async def after_model_step(self, event, *, state: RunState) -> TurnTerminatedBy | None:
        del event, state
        return None

    async def before_complete(self, event, *, state: RunState) -> TurnTerminatedBy | None:
        del event, state
        return None

    async def on_stream_break(
        self,
        terminated_by: TurnTerminatedBy,
        *,
        state: RunState,
        agent: CompiledStateGraph,
        runnable_config: RunnableConfig,
    ) -> TurnTerminatedBy | None:
        del terminated_by, state, agent, runnable_config
        return None


@dataclass
class ChatResumeSubscriber:
    """Gate resume: record events, fatal gate persistence. DONE owned by SSE."""

    persistence: TurnPersistence
    recorder: TurnAgentEventRecorder
    observation: TurnObservationContext
    guards: TurnGuards
    ctx: ChatToolContext
    turn_id: str
    conversation_id: int
    model_key: str
    workspace: Path
    cancel_event: asyncio.Event
    conversation: ChatConversations | None = None
    agent: CompiledStateGraph | None = None
    runnable_config: RunnableConfig | None = None
    control_hook: ChatResumeControlHook | None = None
    terminated_by: TerminatedBy = "completed"
    gate_interrupted: bool = False
    gate_resume_failed: dict[str, str] | None = None
    last_step_index: int = 0
    clear_active_on_success: bool = True

    barrier_events = frozenset(
        {
            TurnEventKind.TURN_COMPLETED,
            TurnEventKind.TURN_FAILED,
            TurnEventKind.TURN_INTERRUPTED,
        }
    )
    broadcast_events = frozenset(
        {
            TurnEventKind.TOOL_STARTED,
            TurnEventKind.MODEL_STEP_FINISHED,
            TurnEventKind.TOOL_FINISHED,
        }
    )

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        if isinstance(event, (ToolStarted, ModelStepFinished, ToolFinished)):
            self.last_step_index = event.step_index
            if isinstance(event, ToolStarted):
                await self.recorder.record(
                    ToolStartedEvent(
                        turn_id=event.turn_id,
                        step_index=event.step_index,
                        call_id=event.call_id,
                        tool_name=event.tool_name,
                        tool_args=event.tool_args,
                    ),
                    ctx=self.ctx,
                    guards=None,
                )
            elif isinstance(event, ModelStepFinished) and event.ai_message is not None:
                await self.recorder.record(
                    ModelStepFinishedEvent(
                        turn_id=event.turn_id,
                        step_index=event.step_index,
                        ai_message=event.ai_message,
                        invalid_tool_calls=list(event.invalid_tool_calls),
                    ),
                    ctx=self.ctx,
                    guards=None,
                )
            elif isinstance(event, ToolFinished):
                await self.recorder.record(
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
                    ctx=self.ctx,
                    guards=None,
                )
                if (
                    event.tool_name == REQUEST_USER_GATE
                    and event.error_class in _GATE_RESUME_FATAL_ERRORS
                ):
                    try:
                        message = ToolResult.display_from_message(event.tool_result or "")
                    except ToolResultProtocolError:
                        message = (event.tool_result or "").strip()
                    self.gate_resume_failed = {
                        "error_class": event.error_class or "",
                        "message": message,
                    }
            return

        if isinstance(event, TurnInterrupted):
            self.gate_interrupted = True
            self.terminated_by = "interrupted"
            return

        if isinstance(event, TurnFailed):
            self.terminated_by = "error"
            return

        if isinstance(event, TurnCompleted):
            if self.gate_resume_failed is not None:
                self.terminated_by = "error"
                await self.persistence.persist_turn_error(
                    turn_id=self.turn_id,
                    step_index=self.last_step_index,
                    content=self.gate_resume_failed["message"],
                    error_code=self.gate_resume_failed["error_class"],
                )
                self.observation.usage_collector.note_termination(
                    terminated_by="error",
                    message=self.gate_resume_failed["message"],
                )
                await clear_gate_pending(self.conversation_id)
                await turn_auth_store.clear_turn_auth(self.conversation_id, self.turn_id)
                if self.control_hook is not None:
                    self.control_hook.force_failed = (
                        self.gate_resume_failed["message"],
                        self.gate_resume_failed["error_class"],
                    )
                return
            if self.agent is not None and self.runnable_config is not None:
                self.gate_interrupted = await emit_user_gates(
                    self.agent,
                    self.runnable_config,
                    emit,
                    turn_id=self.turn_id,
                    conversation_id=self.conversation_id,
                    model_key=self.model_key,
                    workspace=self.workspace,
                )
                if self.gate_interrupted:
                    await persist_gate_suspend_tool_results(
                        agent=self.agent,
                        config=self.runnable_config,
                        observation=self.observation,
                    )
                    self.terminated_by = "interrupted"
                    if self.control_hook is not None:
                        self.control_hook.force_interrupted = True
                    self.observation.usage_collector.note_termination(
                        terminated_by="interrupted",
                        message=None,
                    )
                    return
            pending = await get_gate_pending(self.conversation_id)
            cleared_gate_id = str(pending.get("gate_id") or "") if pending else ""
            await clear_gate_pending(self.conversation_id)
            if cleared_gate_id:
                from app.agent.chat.gate import assets as gate_assets
                from app.agent.chat.gate import meta as gate_meta_store

                gate_assets.delete_gate_assets(self.workspace, cleared_gate_id)
                await gate_meta_store.clear_gate_meta(cleared_gate_id)
            await turn_auth_store.clear_turn_auth(self.conversation_id, self.turn_id)
            if self.clear_active_on_success and self.conversation is not None:
                await clear_turn_active(self.conversation)
                await clear_turn_observation_snapshot(self.conversation_id, self.turn_id)
            self.terminated_by = "completed"
            self.observation.usage_collector.note_termination(
                terminated_by="completed",
                message=None,
            )


@dataclass
class ChatAbortGateSubscriber:
    """Watch gate-cancel tool finish during abort resolve."""

    cancel_event: asyncio.Event
    gate_cancel_seen: bool = False
    barrier_events: frozenset[TurnEventKind] = field(default_factory=frozenset)
    broadcast_events: frozenset[TurnEventKind] = field(
        default_factory=lambda: frozenset({TurnEventKind.TOOL_FINISHED})
    )

    async def handle(self, event: TurnEvent, *, emit: TurnEmit) -> None:
        del emit
        from app.agent.chat.tools.result import GATE_CANCELLED

        if isinstance(event, ToolFinished):
            if event.error_class == GATE_CANCELLED or event.tool_name == "request_user_gate":
                self.gate_cancel_seen = True
                self.cancel_event.set()
