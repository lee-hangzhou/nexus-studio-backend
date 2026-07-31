from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.runtime.turn_engine.prepared import PreparedTurn, default_preview
from app.agent.runtime.turn.guards import TurnGuards
from app.agent.runtime.turn_engine.checkpoint import CheckpointRepairFn
from app.agent.runtime.turn_engine.engine import (
    TurnEngine,
    TurnEngineConfig,
    TurnEngineInput,
)
from app.agent.runtime.turn_engine.handlers import RecoveryHook
from app.agent.runtime.turn_engine.sse_subscriber import PendingEnrichFn, SseTurnSubscriber, ToolPreviewFn
from app.agent.runtime.turn_engine.subscribers import TurnSubscriber
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.server.skills.domain.enums import SkillSurface


async def stream_prepared_turn(prepared: PreparedTurn) -> AsyncIterator[str]:
    """Canonical turn entry: prepared assembly → TurnEngine + SSE subscriber."""
    async for chunk in stream_agent_turn(
        agent=prepared.agent,
        turn_id=prepared.turn_id,
        conversation_id=prepared.conversation_id,
        user_id=prepared.user_id,
        input_messages=prepared.input_messages,
        resume_command=prepared.resume_command,
        runnable_config=prepared.runnable_config,
        thread_id=prepared.thread_id,
        guards=prepared.guards,
        cancel_event=prepared.cancel_event,
        subscribers=prepared.subscribers,
        heartbeat_interval_sec=prepared.heartbeat_interval_sec,
        client_turn_id=prepared.client_turn_id,
        mode=prepared.mode,
        is_resume=prepared.is_resume,
        recovery_hook=prepared.recovery_hook,
        on_turn_start_repair=prepared.on_turn_start_repair,
        on_turn_cleanup_repair=prepared.on_turn_cleanup_repair,
        terminal_policy=prepared.terminal_policy,
        preview_tool_result=prepared.preview_tool_result or default_preview,
        heal_invalid_tool_calls=prepared.heal_invalid_tool_calls,
        surface=prepared.surface,
        enrich_pending=prepared.enrich_pending,
        sse_attribution=prepared.sse_attribution,
    ):
        yield chunk


async def stream_agent_turn(
    *,
    agent: CompiledStateGraph,
    turn_id: str,
    conversation_id: int | str,
    user_id: int | str,
    input_messages: list[BaseMessage] | None = None,
    resume_command: Command | None = None,
    runnable_config: RunnableConfig,
    thread_id: str,
    guards: TurnGuards,
    cancel_event: asyncio.Event,
    subscribers: Sequence[TurnSubscriber],
    heartbeat_interval_sec: int,
    client_turn_id: str | None = None,
    mode: str | None = None,
    is_resume: bool = False,
    recovery_hook: RecoveryHook | None = None,
    on_turn_start_repair: CheckpointRepairFn | None = None,
    on_turn_cleanup_repair: CheckpointRepairFn | None = None,
    terminal_policy: SseTerminalPolicy | None = None,
    preview_tool_result: ToolPreviewFn | None = None,
    heal_invalid_tool_calls: bool = True,
    surface: str = SkillSurface.CHAT,
    enrich_pending: PendingEnrichFn | None = None,
    sse_attribution: dict[str, str | None] | None = None,
) -> AsyncIterator[str]:
    """Low-level turn orchestration; prefer stream_prepared_turn / mount runner."""
    merged_subscribers: list[TurnSubscriber] = [
        SseTurnSubscriber(
            policy=terminal_policy or SseTerminalPolicy(),
            preview_tool_result=preview_tool_result or default_preview,
            surface=surface,
            enrich_pending=enrich_pending,
            sse_attribution=sse_attribution,
        ),
        *list(subscribers),
    ]
    engine = TurnEngine(merged_subscribers)
    config = TurnEngineConfig(
        thread_id=thread_id,
        runnable_config=runnable_config,
        guards=guards,
        heartbeat_interval_sec=heartbeat_interval_sec,
        recovery_hook=recovery_hook,
        on_turn_start_repair=on_turn_start_repair,
        on_turn_cleanup_repair=on_turn_cleanup_repair,
        heal_invalid_tool_calls=heal_invalid_tool_calls,
    )
    turn_input = TurnEngineInput(
        turn_id=turn_id,
        conversation_id=conversation_id,
        user_id=user_id,
        input_messages=input_messages,
        resume_command=resume_command,
        client_turn_id=client_turn_id,
        mode=mode,
        is_resume=is_resume,
    )
    async for chunk in engine.stream(
        agent=agent,
        turn_input=turn_input,
        config=config,
        cancel_event=cancel_event,
    ):
        yield chunk
