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
    CanvasPersistenceSubscriber,
    CanvasResumeSubscriber,
)
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.mounts.spec import AgentMountSpec
from app.agent.runtime.ports import get_canvas_port, get_user_skill_port
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.contracts.turn_content import (
    CompiledTurnInput,
    TurnMediaType,
    TurnReferenceIndex,
    TurnUserInput,
    compile_turn_input,
    extract_skill_paths,
    parse_turn_content_blocks,
)
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.ports.product import SelectedSkillDTO
from app.server.skills.domain.enums import SkillSurface


@dataclass
class CanvasMountContext:
    user_id: int
    project_id: int
    episode_id: int
    session_id: int
    turn_id: str
    cancel_event: asyncio.Event
    checkpointer: BaseCheckpointSaver
    model_key: str = ""
    content_text: str = ""
    user_input: TurnUserInput | None = None
    compiled_input: CompiledTurnInput | None = None
    selected_skills: tuple[SelectedSkillDTO, ...] = ()
    client_turn_id: str | None = None
    mode: str = "auto"
    enable_tools: bool = True
    is_resume: bool = False
    agent: CompiledStateGraph | None = None
    runnable_config: RunnableConfig | None = None
    _turn_id_holder: dict[str, str] = field(default_factory=dict)

    @property
    def resolved_model_key(self) -> str:
        key = (self.model_key or "").strip()
        return key or settings.CHAT_DEFAULT_MODEL


def _thread_id(ctx: CanvasMountContext) -> str:
    return f"{settings.CANVAS_CHECKPOINT_THREAD_PREFIX}:{ctx.episode_id}:{ctx.session_id}"


def _runtime_scope_id(ctx: CanvasMountContext) -> str:
    return f"canvas:{ctx.episode_id}:{ctx.session_id}"


async def _prepare_turn(ctx: CanvasMountContext) -> CanvasMountContext:
    ctx.checkpointer = get_chat_checkpointer()
    ctx._turn_id_holder = {"turn_id": ctx.turn_id}
    if not ctx.is_resume and ctx.user_input is not None:
        ctx.compiled_input = compile_turn_input(ctx.user_input)
        ctx.content_text = ctx.compiled_input.human_message
    if ctx.is_resume and not ctx.selected_skills and ctx.client_turn_id:
        snapshot = await get_canvas_port().get_user_turn_input(
            ctx.session_id,
            ctx.client_turn_id,
        )
        content_raw = snapshot.get("content") if isinstance(snapshot, dict) else None
        if isinstance(content_raw, list):
            try:
                blocks = parse_turn_content_blocks(content_raw)
            except Exception as exc:
                raise AppError(
                    ErrorCode.INVALID_PARAMS,
                    "invalid turn skill input snapshot",
                ) from exc
            paths = extract_skill_paths(blocks)
            if paths:
                ctx.selected_skills = await get_user_skill_port().resolve_selected(
                    surface=SkillSurface.CANVAS,
                    user_id=ctx.user_id,
                    project_id=ctx.project_id,
                    paths=paths,
                )
    return ctx


async def _build_agent(ctx: CanvasMountContext) -> CompiledStateGraph:
    if ctx.agent is not None:
        return ctx.agent
    model_key = ctx.resolved_model_key
    spec = get_model_spec(model_key)
    llm = GatewayChatModel(
        model_key=model_key,
        spec=spec,
        cancel_event=ctx.cancel_event,
    )
    loop_guard = TurnToolLoopGuard(surface=SkillSurface.CANVAS)
    if ctx.is_resume:
        reference_index = TurnReferenceIndex()
        tool_asset_ids: frozenset[int] = frozenset()
        asset_media_types: dict[int, TurnMediaType] = {}
    else:
        if ctx.compiled_input is None:
            raise AppError(ErrorCode.INTERNAL_ERROR, "compiled turn input missing before agent build")
        reference_index = ctx.compiled_input.reference_index
        tool_asset_ids = frozenset(ctx.compiled_input.tool_asset_ids)
        asset_media_types = {
            a.asset_id: a.media_type for a in ctx.compiled_input.tool_asset_index
        }
    agent, _ = await build_canvas_agent(
        llm,
        project_id=ctx.project_id,
        episode_id=ctx.episode_id,
        user_id=ctx.user_id,
        checkpointer=ctx.checkpointer,
        enable_tools=ctx.enable_tools,
        mode=ctx.mode,
        turn_id_holder=ctx._turn_id_holder,
        loop_guard=loop_guard,
        user_message=ctx.content_text,
        is_resume=ctx.is_resume,
        selected_skills=ctx.selected_skills,
        reference_index=reference_index,
        tool_asset_ids=tool_asset_ids,
        asset_media_types=asset_media_types,
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
        return [
            CanvasResumeSubscriber(
                episode_id=ctx.episode_id,
                session_id=ctx.session_id,
                user_id=ctx.user_id,
            )
        ]
    return [
        CanvasPersistenceSubscriber(
            project_id=ctx.project_id,
            episode_id=ctx.episode_id,
            session_id=ctx.session_id,
            user_id=ctx.user_id,
            content=ctx.content_text,
            client_turn_id=ctx.client_turn_id,
            enable_tools=ctx.enable_tools,
            model_key=ctx.resolved_model_key,
            user_input=ctx.user_input,
        ),
    ]


def _build_runnable_config(ctx: CanvasMountContext) -> RunnableConfig:
    if ctx.runnable_config is not None:
        return ctx.runnable_config
    config = canvas_runnable_config(
        thread_id=_thread_id(ctx),
        user_id=ctx.user_id,
        project_id=ctx.project_id,
        episode_id=ctx.episode_id,
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
    return [HumanMessage(content=ctx.content_text)]


def _heartbeat(_ctx: CanvasMountContext) -> int:
    return int(settings.CANVAS_HEARTBEAT_INTERVAL_SEC)


def _start_repair(ctx: CanvasMountContext):
    async def repair(agent, runnable_config, **kwargs: Any) -> None:
        await repair_canvas_checkpoint_if_needed(
            agent,
            runnable_config,
            project_id=ctx.project_id,
            episode_id=ctx.episode_id,
            turn_id=ctx.turn_id,
            reason=kwargs.get("reason") if kwargs.get("reason") != "turn_start" else None,
        )

    return repair


def _cleanup_repair(ctx: CanvasMountContext):
    return _start_repair(ctx)


def _preview(_ctx: CanvasMountContext):
    return lambda name, result, ok: sanitize_tool_step_preview(name, result, ok=ok)


CANVAS_MOUNT = AgentMountSpec(
    name=SkillSurface.CANVAS,
    prepare_turn=_prepare_turn,
    resolve_thread_id=_thread_id,
    build_guards=_build_guards,
    build_subscribers=_build_subscribers,
    build_runnable_config=_build_runnable_config,
    build_agent=_build_agent,
    build_terminal_policy=_terminal_policy,
    resolve_heartbeat_interval_sec=_heartbeat,
    build_recovery_hook=_recovery,
    build_preview_tool_result=_preview,
    resolve_input_messages=_input_messages,
    resolve_on_turn_start_repair=_start_repair,
    resolve_on_turn_cleanup_repair=_cleanup_repair,
    resolve_mode=lambda ctx: ctx.mode,
    resolve_client_turn_id=lambda ctx: ctx.client_turn_id,
    resolve_runtime_scope_id=_runtime_scope_id,
)
