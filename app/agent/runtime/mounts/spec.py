"""Agent mount specification — runner only awaits callables, never branches on mount name."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.runtime.mounts.context import MountTurnContext
from app.agent.runtime.turn.guards import TurnGuards
from app.agent.runtime.turn_engine.checkpoint import CheckpointRepairFn
from app.agent.runtime.turn_engine.handlers import RecoveryHook
from app.agent.runtime.turn_engine.sse_subscriber import PendingEnrichFn, ToolPreviewFn
from app.agent.runtime.turn_engine.subscribers import TurnSubscriber
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy

PrepareTurnFn = Callable[[Any], Awaitable[Any]]
BuildAgentFn = Callable[[Any], Awaitable[CompiledStateGraph]]
BuildSubscribersFn = Callable[[Any], Sequence[TurnSubscriber]]
BuildGuardsFn = Callable[[Any], TurnGuards]
ResolveThreadIdFn = Callable[[Any], str]
BuildRunnableConfigFn = Callable[[Any], RunnableConfig]
BuildTerminalPolicyFn = Callable[[Any], SseTerminalPolicy]
BuildRecoveryHookFn = Callable[[Any], RecoveryHook | None]
BuildPreviewFn = Callable[[Any], ToolPreviewFn | None]
BuildEnrichPendingFn = Callable[[Any], PendingEnrichFn | None]
ResolveInputMessagesFn = Callable[[Any], list[BaseMessage] | None]
ResolveHeartbeatFn = Callable[[Any], int]
ResolveRepairFn = Callable[[Any], CheckpointRepairFn | None]
ResolveModeFn = Callable[[Any], str | None]
ResolveClientTurnIdFn = Callable[[Any], str | None]
ResolveRuntimeScopeIdFn = Callable[[Any], int | str]


@dataclass(frozen=True)
class AgentMountSpec:
    """Surface differences for one agent turn. Runtime never switches on ``name``."""

    name: str
    resolve_thread_id: ResolveThreadIdFn
    build_guards: BuildGuardsFn
    build_subscribers: BuildSubscribersFn
    build_runnable_config: BuildRunnableConfigFn
    build_agent: BuildAgentFn
    build_terminal_policy: BuildTerminalPolicyFn
    resolve_heartbeat_interval_sec: ResolveHeartbeatFn
    prepare_turn: PrepareTurnFn | None = None
    build_recovery_hook: BuildRecoveryHookFn | None = None
    build_preview_tool_result: BuildPreviewFn | None = None
    build_enrich_pending: BuildEnrichPendingFn | None = None
    resolve_input_messages: ResolveInputMessagesFn | None = None
    resolve_on_turn_start_repair: ResolveRepairFn | None = None
    resolve_on_turn_cleanup_repair: ResolveRepairFn | None = None
    resolve_mode: ResolveModeFn | None = None
    resolve_client_turn_id: ResolveClientTurnIdFn | None = None
    resolve_runtime_scope_id: ResolveRuntimeScopeIdFn | None = None
