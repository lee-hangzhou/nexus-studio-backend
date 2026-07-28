"""Shared mutable state for chat turn subscribers (side-effect adapters)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.tools.lc_tools import ChatToolContext
from app.agent.chat.turn.event_recorder import TurnAgentEventRecorder
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.observation import TurnObservationContext
from app.agent.chat.turn.persistence import TurnPersistence
from app.agent.chat.turn.recovery_hook import ChatRecoveryHook
from app.agent.chat.turn.usage_log import TerminatedBy, TurnUsageCollector
from app.contracts.metadata import TurnContextMetadata
from app.server.chat.persistence.conversations import ChatConversations


@dataclass
class ChatTurnSession:
    """One turn's shared state — subscribers adapt; they do not own SSE terminals."""

    persistence: TurnPersistence
    recorder: TurnAgentEventRecorder
    usage_collector: TurnUsageCollector
    observation: TurnObservationContext
    guards: TurnGuards
    ctx: ChatToolContext
    turn_context_meta: TurnContextMetadata
    tool_audit: list
    turn_id: str
    conversation_id: int
    user_id: int
    model_key: str
    content: str
    turn_asset_ids: tuple[int, ...]
    conversation: ChatConversations
    workspace: Path
    cancel_event: asyncio.Event
    recovery_hook: ChatRecoveryHook
    agent: CompiledStateGraph | None = None
    runnable_config: RunnableConfig | None = None
    terminated_by: TerminatedBy = "error"
    gate_interrupted: bool = False
    browser_blocked: bool = False
