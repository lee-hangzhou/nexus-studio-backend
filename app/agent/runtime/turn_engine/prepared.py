from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.runtime.turn.guards import TurnGuards
from app.agent.runtime.turn_engine.checkpoint import CheckpointRepairFn
from app.agent.runtime.turn_engine.handlers import RecoveryHook
from app.agent.runtime.turn_engine.sse_subscriber import PendingEnrichFn, ToolPreviewFn
from app.agent.runtime.turn_engine.subscribers import TurnSubscriber
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.agent.runtime.tools.result import summarize_tool_result
from app.server.skills.domain.enums import SkillSurface


@dataclass(frozen=True)
class PreparedTurn:
    """surface Mount/编排器准备完成后 TurnEngine 所需的全部输入"""

    agent: CompiledStateGraph
    turn_id: str
    conversation_id: int | str
    user_id: int | str
    runnable_config: RunnableConfig
    thread_id: str
    guards: TurnGuards
    cancel_event: asyncio.Event
    subscribers: Sequence[TurnSubscriber]
    heartbeat_interval_sec: int
    input_messages: list[BaseMessage] | None = None
    resume_command: Command | None = None
    client_turn_id: str | None = None
    mode: str | None = None
    is_resume: bool = False
    recovery_hook: RecoveryHook | None = None
    on_turn_start_repair: CheckpointRepairFn | None = None
    on_turn_cleanup_repair: CheckpointRepairFn | None = None
    terminal_policy: SseTerminalPolicy | None = None
    preview_tool_result: ToolPreviewFn | None = None
    heal_invalid_tool_calls: bool = True
    surface: str = SkillSurface.CHAT
    enrich_pending: PendingEnrichFn | None = None
    sse_attribution: dict[str, str | None] | None = None


def default_preview(tool_name: str, result: str, ok: bool) -> str:
    """默认工具结果预览截断"""
    return summarize_tool_result(tool_name, result, ok=ok)
