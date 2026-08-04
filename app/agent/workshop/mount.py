from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import FrozenSet

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.agent.factory import build_chat_agent
from app.agent.chat.expert_turn import (
    build_expert_identity_block,
    intersect_chat_tools_with_profile,
    profile_tool_names_for_workshop_expert,
)
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.chat.memory.store import chat_runnable_config
from app.agent.chat.prompt.composer import PromptComposer
from app.agent.chat.prompt.types import TurnPromptContext
from app.agent.chat.tools.build_turn_tools import build_chat_turn_tools
from app.agent.chat.tools.lc_tools import ChatToolContext
from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview
from app.agent.workshop.host_tools import build_host_orchestration_tools
from app.agent.workshop.mechanism import (
    WorkshopMechanismSurface,
    assemble_workshop_mechanism_skills_block,
    format_invite_directory_prompt,
)
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.persistence import TurnPersistence
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.mounts.spec import AgentMountSpec
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.agent.workshop.skills.loader import load_workshop_skill_bodies
from app.agent.workshop.turn.persistence_subscriber import (
    WorkshopPersistenceSubscriber,
    persist_workshop_user_message,
    workshop_workspace,
)
from app.server.infra.config import settings
from app.server.skills.domain.enums import SkillSurface
from app.server.workshop.domain.ecommerce.effective_profile import (
    expert_kind_to_role,
    resolve_effective_capabilities,
    workshop_expert_idle_thread_id,
    workshop_host_idle_thread_id,
    workshop_thread_id,
)
from app.server.workshop.domain.enums import (
    WorkshopExpertKind,
    WorkshopRole,
    WorkshopToolCapability,
)
from app.server.workshop.domain.ecommerce.profiles import is_ecom_preset
from app.server.workshop.domain.tool_mapping import capability_tool_names, profile_for_preset


@dataclass(slots=True)
class WorkshopMountContext:
    """Workshop 回合挂载上下文（供 chat-style mount 调用）"""

    project_id: str
    expert_id: str
    task_id: str | None
    preset_key: str | None
    expert_kind: WorkshopExpertKind
    granted_external: FrozenSet[WorkshopToolCapability] = field(default_factory=frozenset)
    is_host: bool = False
    tool_names: tuple[str, ...] = ()


@dataclass
class WorkshopTurnMountContext(WorkshopMountContext):
    """完整 Workshop AgentMount 上下文"""

    user_id: int = 0
    conversation_id: int = 0
    turn_id: str = ""
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    model_key: str = ""
    enable_tools: bool = True
    content: str = ""
    host_context_block: str = ""
    room_timeline_block: str = ""
    speaker_role: str | None = None
    expert_name: str | None = None
    avatar_url: str | None = None
    system_prompt: str = ""
    agent: CompiledStateGraph | None = None
    runnable_config: RunnableConfig | None = None
    input_messages: list | None = None
    sse_attribution: dict[str, str | None] = field(default_factory=dict)
    persistence: TurnPersistence | None = None
    tool_ctx: ChatToolContext | None = None
    selected_skills_text: str = ""
    persist_user_message: bool = True
    persist_chat_messages: bool = True


def prepare_turn_tool_snapshot(ctx: WorkshopMountContext) -> tuple[str, ...]:
    """按 EffectiveProfile 计算本回合可见工具名；Advisor 无 MCP/店铺写"""
    if ctx.is_host:
        host_caps = resolve_effective_capabilities(
            WorkshopRole.HOST,
            frozenset(
                {
                    WorkshopToolCapability.READ_PROJECT_FILES,
                    WorkshopToolCapability.INVITE_EXPERT,
                    WorkshopToolCapability.RAISE_AUTH_POPUP,
                    WorkshopToolCapability.PROPOSE_INVITE,
                    WorkshopToolCapability.DRAFT_WORKFLOW,
                    WorkshopToolCapability.CONFIRM_SAVE_WORKFLOW,
                    WorkshopToolCapability.CREATE_SCHEDULE,
                    WorkshopToolCapability.MANUAL_RUN_WORKFLOW,
                }
            ),
            ctx.granted_external,
        )
        return capability_tool_names(host_caps)

    profile = profile_for_preset(ctx.preset_key)
    if profile is not None:
        role = expert_kind_to_role(profile.kind)
        effective = resolve_effective_capabilities(
            role,
            profile.capability_allowlist,
            ctx.granted_external,
        )
        return capability_tool_names(effective)

    role = expert_kind_to_role(ctx.expert_kind)
    from app.server.workshop.domain.role_policy import capabilities_for

    effective = resolve_effective_capabilities(
        role,
        capabilities_for(role),
        ctx.granted_external,
    )
    return capability_tool_names(effective)


def resolve_thread_id(ctx: WorkshopMountContext) -> str:
    """解析 checkpointer thread_id：Host / 专家闲聊 / 专家×任务彼此隔离"""
    if ctx.is_host:
        return workshop_host_idle_thread_id(ctx.project_id)
    if ctx.task_id is None:
        return workshop_expert_idle_thread_id(ctx.project_id, ctx.expert_id)
    return workshop_thread_id(ctx.project_id, ctx.expert_id, ctx.task_id)


def _build_workshop_system_prompt(ctx: WorkshopTurnMountContext) -> str:
    """组装 Workshop host 或 expert 回合 system prompt"""
    prompt_ctx = TurnPromptContext(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        model_key=ctx.model_key,
        enable_tools=ctx.enable_tools,
        tool_names=list(ctx.tool_names),
    )
    parts = [
        PromptComposer.build_capability_brief(prompt_ctx),
        PromptComposer.build_context_clock(),
    ]
    if ctx.is_host:
        mechanism = assemble_workshop_mechanism_skills_block(
            surface=WorkshopMechanismSurface.HOST,
            invite_directory_block=format_invite_directory_prompt(),
        )
        parts.insert(0, mechanism)
        if ctx.host_context_block.strip():
            parts.insert(1, ctx.host_context_block.strip())
    elif ctx.preset_key:
        parts.insert(0, build_expert_identity_block(ctx.preset_key))
        parts.insert(
            1,
            assemble_workshop_mechanism_skills_block(
                surface=WorkshopMechanismSurface.EXPERT,
            ),
        )
        if is_ecom_preset(ctx.preset_key):
            profile = profile_for_preset(ctx.preset_key)
            if profile is not None and profile.skill_refs:
                skill_bodies = load_workshop_skill_bodies(profile.skill_refs)
                if skill_bodies:
                    parts.insert(2, skill_bodies)
    if ctx.room_timeline_block.strip():
        parts.append(ctx.room_timeline_block.strip())
    if ctx.selected_skills_text.strip():
        parts.append(ctx.selected_skills_text.strip())
    return "\n\n".join(part for part in parts if part.strip())


async def _prepare_workshop_turn(ctx: WorkshopTurnMountContext) -> WorkshopTurnMountContext:
    """准备 Workshop AgentMount 的 turn 上下文"""
    workspace = workshop_workspace()
    ctx.tool_names = prepare_turn_tool_snapshot(ctx)
    ctx.system_prompt = _build_workshop_system_prompt(ctx)
    ctx.sse_attribution = {
        "speaker_role": ctx.speaker_role or ("host" if ctx.is_host else "expert"),
        "expert_id": None if ctx.is_host else ctx.expert_id,
        "expert_name": ctx.expert_name or ("项目助手" if ctx.is_host else None),
        "avatar": ctx.avatar_url or ("/avatars/experts/host.png" if ctx.is_host else None),
        "task_id": ctx.task_id,
    }
    persistence = TurnPersistence(user_id=ctx.user_id, conversation_id=ctx.conversation_id)
    ctx.persistence = persistence
    if ctx.persist_chat_messages and ctx.persist_user_message and ctx.content.strip():
        await persist_workshop_user_message(
            user_id=ctx.user_id,
            conversation_id=ctx.conversation_id,
            content=ctx.content,
            turn_id=ctx.turn_id,
        )
    loop_guard = TurnToolLoopGuard(surface=SkillSurface.CHAT)
    tool_ctx = ChatToolContext(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        workspace=workspace,
        audit=[],
        guards=None,
        cancel_event=ctx.cancel_event,
        loop_guard=loop_guard,
        source_user_text=ctx.content,
        model_key=ctx.model_key,
    )
    ctx.tool_ctx = tool_ctx
    all_tools = build_chat_turn_tools(
        tool_ctx,
        enable_tools=ctx.enable_tools,
        user_id=ctx.user_id,
        tool_asset_ids=frozenset(),
        asset_media_types={},
    )
    if ctx.is_host:
        # Host 不挂载 Chat 执行工具；仅编排工具
        tools = (
            build_host_orchestration_tools(tool_ctx, project_id=ctx.project_id)
            if ctx.enable_tools
            else []
        )
        ctx.tool_names = tuple(tool.name for tool in tools)
    elif ctx.preset_key:
        allowed = profile_tool_names_for_workshop_expert(
            ctx.preset_key,
            granted_external=ctx.granted_external,
        )
        tools = intersect_chat_tools_with_profile(all_tools, allowed)
    else:
        allowed = frozenset(ctx.tool_names)
        tools = intersect_chat_tools_with_profile(all_tools, allowed)
    spec = get_model_spec(ctx.model_key)
    llm = GatewayChatModel(
        model_key=ctx.model_key,
        spec=spec,
        cancel_event=ctx.cancel_event,
    )
    ctx.agent = build_chat_agent(
        llm,
        tools,
        get_chat_checkpointer(),
        system_prompt=ctx.system_prompt,
        store=get_memory_store(),
    )
    thread_id = resolve_thread_id(ctx)
    ctx.runnable_config = chat_runnable_config(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        workspace=str(workspace),
        turn_id=ctx.turn_id,
    )
    ctx.runnable_config["configurable"]["thread_id"] = thread_id
    ctx.input_messages = [HumanMessage(content=ctx.content)] if ctx.content else []
    return ctx


async def _build_workshop_agent(ctx: WorkshopTurnMountContext) -> CompiledStateGraph:
    """返回已组装的 Workshop agent"""
    assert ctx.agent is not None
    return ctx.agent


def _workshop_guards(_ctx: WorkshopTurnMountContext) -> TurnGuards:
    """工坊回合守卫参数"""
    return TurnGuards(
        max_model_steps=settings.CHAT_MAX_ITERATIONS,
        max_tool_calls=settings.CHAT_MAX_TOOL_CALLS,
        wall_clock_sec=settings.CHAT_TURN_WALL_CLOCK_SEC,
        tool_repeat_guard=settings.CHAT_TOOL_REPEAT_GUARD,
    )


def _workshop_subscribers(ctx: WorkshopTurnMountContext):
    """复用 TurnPersistence 写入助手消息与发言归属；非聊天平面关闭落库"""
    if not ctx.persist_chat_messages:
        return []
    assert ctx.persistence is not None
    assert ctx.tool_ctx is not None
    return [
        WorkshopPersistenceSubscriber(
            persistence=ctx.persistence,
            tool_ctx=ctx.tool_ctx,
            user_id=ctx.user_id,
            conversation_id=ctx.conversation_id,
            turn_id=ctx.turn_id,
            enable_tools=ctx.enable_tools,
            attribution=dict(ctx.sse_attribution),
        )
    ]


def _workshop_runnable_config(ctx: WorkshopTurnMountContext) -> RunnableConfig:
    """返回 LangGraph runnable config"""
    assert ctx.runnable_config is not None
    return ctx.runnable_config


def _workshop_terminal_policy(ctx: WorkshopTurnMountContext) -> SseTerminalPolicy:
    """DONE 帧携带已落库 message_ids"""
    persistence = ctx.persistence
    return SseTerminalPolicy(
        emit_done_on_completed=True,
        emit_done_after_failure=True,
        message_ids=(lambda: persistence.message_ids) if persistence is not None else None,
    )


def _workshop_preview(_ctx: WorkshopTurnMountContext):
    """工具结果预览清洗"""
    return lambda name, result, ok: sanitize_tool_step_preview(name, result, ok=ok)


def _workshop_input_messages(ctx: WorkshopTurnMountContext):
    """本回合输入消息"""
    return ctx.input_messages


def _workshop_heartbeat(_ctx: WorkshopTurnMountContext) -> int:
    """心跳间隔秒"""
    return int(settings.CHAT_HEARTBEAT_INTERVAL_SEC)


def _workshop_sse_attribution(ctx: WorkshopTurnMountContext) -> dict[str, str | None]:
    """SSE 发言归属字段"""
    return dict(ctx.sse_attribution)


def _workshop_runtime_scope(ctx: WorkshopTurnMountContext) -> str:
    """运行时作用域 id"""
    return f"workshop:{ctx.project_id}"


async def prepare_turn(ctx: WorkshopMountContext) -> WorkshopMountContext:
    """轻量 prepare_turn 兼容：仅写入 tool_names 快照"""
    ctx.tool_names = prepare_turn_tool_snapshot(ctx)
    return ctx


WORKSHOP_MOUNT = AgentMountSpec(
    name="workshop",
    prepare_turn=_prepare_workshop_turn,
    resolve_thread_id=resolve_thread_id,
    build_guards=_workshop_guards,
    build_subscribers=_workshop_subscribers,
    build_runnable_config=_workshop_runnable_config,
    build_agent=_build_workshop_agent,
    build_terminal_policy=_workshop_terminal_policy,
    resolve_heartbeat_interval_sec=_workshop_heartbeat,
    build_preview_tool_result=_workshop_preview,
    resolve_input_messages=_workshop_input_messages,
    resolve_runtime_scope_id=_workshop_runtime_scope,
    resolve_sse_attribution=_workshop_sse_attribution,
)
