"""Canvas AgentMountSpec — assemble turn via mount callables."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.canvas.agent.factory import build_canvas_agent
from app.agent.canvas.memory.store import canvas_runnable_config
from app.agent.canvas.turn.checkpoint import repair_canvas_checkpoint_if_needed
from app.agent.canvas.turn.empty_hook import CanvasEmptyAnswerHook
from app.agent.canvas.turn.guards import CanvasTurnGuards
from app.agent.canvas.turn.subscribers import (
    CanvasGenerationHubSubscriber,
    CanvasPersistenceSubscriber,
    CanvasResumeSubscriber,
)
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.mounts.spec import AgentMountSpec
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.server.infra.config import settings

CANVAS_AGENT_MODEL_KEY = "gpt-5.5"


@dataclass
class CanvasMountContext:
    user_id: int
    conversation_id: int  # project_id at the mount boundary
    turn_id: str
    cancel_event: asyncio.Event
    checkpointer: BaseCheckpointSaver
    content: str = ""
    client_turn_id: str | None = None
    mode: str = "auto"
    enable_tools: bool = True
    is_resume: bool = False
    agent: CompiledStateGraph | None = None
    runnable_config: RunnableConfig | None = None
    _turn_id_holder: dict[str, str] = field(default_factory=dict)

    @property
    def project_id(self) -> int:
        return int(self.conversation_id)


def _thread_id(ctx: CanvasMountContext) -> str:
    return f"{settings.CANVAS_CHECKPOINT_THREAD_PREFIX}-{ctx.project_id}"


async def _prepare_turn(ctx: CanvasMountContext) -> CanvasMountContext:
    ctx.checkpointer = get_chat_checkpointer()
    ctx._turn_id_holder = {"turn_id": ctx.turn_id}
    return ctx


async def _build_agent(ctx: CanvasMountContext) -> CompiledStateGraph:
    if ctx.agent is not None:
        return ctx.agent
    spec = get_model_spec(CANVAS_AGENT_MODEL_KEY)
    llm = GatewayChatModel(
        model_key=CANVAS_AGENT_MODEL_KEY,
        spec=spec,
        cancel_event=ctx.cancel_event,
    )
    loop_guard = TurnToolLoopGuard(surface="canvas")
    agent, _ = await build_canvas_agent(
        llm,
        project_id=ctx.project_id,
        user_id=ctx.user_id,
        checkpointer=ctx.checkpointer,
        enable_tools=ctx.enable_tools,
        mode=ctx.mode,
        turn_id_holder=ctx._turn_id_holder,
        loop_guard=loop_guard,
    )
    ctx.agent = agent
    return agent


def _build_guards(ctx: CanvasMountContext) -> CanvasTurnGuards:
    return CanvasTurnGuards(
        max_model_steps=settings.CANVAS_MAX_ITERATIONS,
        max_tool_calls=settings.CANVAS_MAX_TOOL_CALLS,
        wall_clock_sec=settings.CANVAS_TURN_WALL_CLOCK_SEC,
        tool_repeat_guard=settings.CANVAS_TOOL_REPEAT_GUARD,
    )


def _build_subscribers(ctx: CanvasMountContext) -> list:
    if ctx.is_resume:
        return [CanvasResumeSubscriber()]
    return [
        CanvasGenerationHubSubscriber(project_id=ctx.project_id),
        CanvasPersistenceSubscriber(
            project_id=ctx.project_id,
            user_id=ctx.user_id,
            content=ctx.content,
            client_turn_id=ctx.client_turn_id,
            enable_tools=ctx.enable_tools,
        ),
    ]


def _build_runnable_config(ctx: CanvasMountContext) -> RunnableConfig:
    if ctx.runnable_config is not None:
        return ctx.runnable_config
    config = canvas_runnable_config(
        thread_id=_thread_id(ctx),
        user_id=ctx.user_id,
        project_id=ctx.project_id,
        mode=ctx.mode,
    )
    ctx.runnable_config = config
    return config


def _terminal_policy(_ctx: CanvasMountContext) -> SseTerminalPolicy:
    return SseTerminalPolicy(emit_done_on_completed=True, emit_done_after_failure=False)


def _recovery(ctx: CanvasMountContext):
    if ctx.is_resume:
        return None
    return CanvasEmptyAnswerHook()


def _input_messages(ctx: CanvasMountContext):
    if ctx.is_resume:
        return None
    return [HumanMessage(content=ctx.content)]


def _heartbeat(_ctx: CanvasMountContext) -> int:
    return int(settings.CANVAS_HEARTBEAT_INTERVAL_SEC)


def _start_repair(ctx: CanvasMountContext):
    async def repair(agent, runnable_config, **kwargs: Any) -> None:
        await repair_canvas_checkpoint_if_needed(
            agent,
            runnable_config,
            project_id=ctx.project_id,
            turn_id=ctx.turn_id,
            reason=kwargs.get("reason") if kwargs.get("reason") != "turn_start" else None,
        )

    return repair


def _cleanup_repair(ctx: CanvasMountContext):
    return _start_repair(ctx)


CANVAS_MOUNT = AgentMountSpec(
    name="canvas",
    prepare_turn=_prepare_turn,
    resolve_thread_id=_thread_id,
    build_guards=_build_guards,
    build_subscribers=_build_subscribers,
    build_runnable_config=_build_runnable_config,
    build_agent=_build_agent,
    build_terminal_policy=_terminal_policy,
    resolve_heartbeat_interval_sec=_heartbeat,
    build_recovery_hook=_recovery,
    resolve_input_messages=_input_messages,
    resolve_on_turn_start_repair=_start_repair,
    resolve_on_turn_cleanup_repair=_cleanup_repair,
    resolve_mode=lambda ctx: ctx.mode,
    resolve_client_turn_id=lambda ctx: ctx.client_turn_id,
)
