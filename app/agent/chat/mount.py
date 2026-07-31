from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.agent.factory import build_chat_agent
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.chat.memory.store import chat_runnable_config
from app.agent.chat.memory.turn_input import build_attachment_context_block
from app.agent.chat.prompt.composer import PromptComposer
from app.agent.chat.prompt.types import AttachmentBrief, TurnPromptContext
from app.agent.chat.expert_turn import (
    build_expert_identity_block,
    intersect_chat_tools_with_profile,
    profile_tool_names_for_chat,
)
from app.agent.chat.tools.build_turn_tools import build_chat_turn_tools
from app.agent.chat.tools.lc_tools import ChatToolContext
from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview
from app.agent.chat.turn.checkpoint import (
    capture_turn_checkpoint_messages,
    repair_chat_checkpoint_if_needed,
)
from app.agent.chat.turn.context import build_turn_context
from app.agent.chat.turn.event_recorder import TurnAgentEventRecorder
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.observation import TurnObservationContext
from app.agent.chat.turn.observation_store import save_turn_observation_snapshot
from app.agent.chat.turn.persistence import TurnPersistence, persist_user_message
from app.agent.chat.turn.recovery_hook import ChatRecoveryHook
from app.agent.chat.turn.session import ChatTurnSession
from app.agent.chat.turn.subscribers import build_chat_lifecycle_subscribers
from app.agent.chat.turn.trace import log_stage
from app.agent.chat.turn.usage_log import TurnUsageCollector
from app.agent.chat.workspace import conversation_workspace
from app.agent.chat.workspace.session import ensure_workspace_session
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.memory.inject import MemoryInjectionRequest, build_memory_injection
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.mounts.spec import AgentMountSpec
from app.agent.runtime.ports import get_user_skill_port
from app.agent.runtime.skills.prompt_format import (
    format_selected_skill_bodies_text,
    format_user_skill_index_text,
)
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.contracts.metadata import ToolAuditMetadata, TurnContextMetadata
from app.contracts.turn_content import (
    CompiledTurnInput,
    TurnMediaType,
    TurnUserInput,
    compile_turn_input,
    input_snapshot_dict,
)
from app.server.chat.persistence.attachments import ChatAttachments
from app.server.chat.persistence.conversations import ChatConversations
from app.server.chat.services.attachments.service import chat_attachment_service
from app.server.chat.services.attachments.status import attachment_status_label
from app.server.chat.services.constants import CHAT_CHECKPOINT_THREAD_PREFIX
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings
from app.server.ports.product import SelectedSkillDTO
from app.server.skills.domain.enums import SkillSurface


def _workspace_path_for_attachment(
    *,
    row: ChatAttachments,
    turn_asset_ids: frozenset[int],
    materialized_by_asset: dict[int, str],
) -> str:
    """解析附件在本轮工作区路径; 本 turn 引用资产必须已 materialize"""
    if row.asset_id is None:
        return ""
    asset_id = int(row.asset_id)
    if asset_id not in turn_asset_ids:
        return ""
    path = materialized_by_asset.get(asset_id)
    if path is None:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            f"turn asset {asset_id} missing workspace materialization",
        )
    return path


@dataclass
class ChatMountContext:
    user_id: int
    conversation_id: int
    turn_id: str
    cancel_event: asyncio.Event
    checkpointer: BaseCheckpointSaver
    conversation: ChatConversations
    content: str
    model_key: str
    enable_tools: bool
    user_input: TurnUserInput
    selected_skills: tuple[SelectedSkillDTO, ...] = ()
    project_id: int | None = None
    client_turn_id: str | None = None
    compiled_input: CompiledTurnInput | None = None
    persistence: TurnPersistence | None = None
    guards: TurnGuards | None = None
    usage_collector: TurnUsageCollector | None = None
    observation: TurnObservationContext | None = None
    recorder: TurnAgentEventRecorder | None = None
    session: ChatTurnSession | None = None
    agent: CompiledStateGraph | None = None
    runnable_config: RunnableConfig | None = None
    input_messages: list[BaseMessage] | None = None
    turn_start_messages: list[BaseMessage] | None = None
    recovery_hook: ChatRecoveryHook | None = None
    workspace: Path | None = None
    sse_attribution: dict[str, str | None] | None = None


def _thread_id(ctx: ChatMountContext) -> str:
    """解析 Chat checkpoint thread id"""
    return f"{CHAT_CHECKPOINT_THREAD_PREFIX}-{ctx.conversation_id}"


async def _prepare_turn(ctx: ChatMountContext) -> ChatMountContext:
    """组装 Chat turn 的 agent、prompt 与持久化快照"""
    if ctx.user_input is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "enriched turn user input missing")
    compiled = compile_turn_input(ctx.user_input)
    ctx.compiled_input = compiled
    ctx.content = compiled.human_message

    persistence = TurnPersistence(user_id=ctx.user_id, conversation_id=ctx.conversation_id)
    ctx.persistence = persistence
    attachment_rows = await ChatAttachments.filter(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        is_attached=True,
    )
    has_turn_assets = bool(compiled.tool_asset_ids)
    turn_ctx = build_turn_context(
        has_attachments=bool(attachment_rows) or has_turn_assets,
        enable_tools=ctx.enable_tools,
    )
    log_stage(
        "turn.context",
        has_attachments=turn_ctx.has_attachments,
        enable_tools=turn_ctx.enable_tools,
    )
    turn_context_meta = TurnContextMetadata(
        has_attachments=turn_ctx.has_attachments,
        enable_tools=turn_ctx.enable_tools,
    )
    workspace = conversation_workspace(ctx.user_id, ctx.conversation_id)
    ensure_workspace_session(workspace)
    ctx.workspace = workspace
    materialized = []
    materialized_by_asset: dict[int, str] = {}
    if turn_ctx.enable_materialize and compiled.tool_asset_ids:
        import time as _time

        mat_started = _time.perf_counter()
        materialized = await chat_attachment_service.materialize_turn_assets_to_workspace(
            workspace,
            user_id=ctx.user_id,
            conversation_id=ctx.conversation_id,
            asset_ids=compiled.tool_asset_ids,
        )
        materialized_by_asset = {item.asset_id: item.workspace_path for item in materialized}
        log_stage("turn.materialize", started=mat_started, count=len(materialized))

    guards = TurnGuards(
        max_model_steps=settings.CHAT_MAX_ITERATIONS,
        max_tool_calls=settings.CHAT_MAX_TOOL_CALLS,
        wall_clock_sec=settings.CHAT_TURN_WALL_CLOCK_SEC,
        tool_repeat_guard=settings.CHAT_TOOL_REPEAT_GUARD,
    )
    ctx.guards = guards
    tool_audit: list[ToolAuditMetadata] = []
    usage_collector = TurnUsageCollector(
        conversation_id=ctx.conversation_id,
        turn_id=ctx.turn_id,
        model_key=ctx.model_key,
        limits={
            "max_iterations": settings.CHAT_MAX_ITERATIONS,
            "max_tool_calls": settings.CHAT_MAX_TOOL_CALLS,
            "wall_clock_sec": settings.CHAT_TURN_WALL_CLOCK_SEC,
        },
        attachments=[item.workspace_path for item in materialized],
    )
    ctx.usage_collector = usage_collector
    observation = TurnObservationContext.for_main_turn(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        turn_id=ctx.turn_id,
        model_key=ctx.model_key,
        persistence=persistence,
        usage_collector=usage_collector,
        turn_context=turn_context_meta,
        tool_audit=tool_audit,
    )
    ctx.observation = observation
    recorder = TurnAgentEventRecorder(observation=observation)
    ctx.recorder = recorder
    await save_turn_observation_snapshot(
        conversation_id=ctx.conversation_id,
        turn_id=ctx.turn_id,
        model_key=ctx.model_key,
        turn_context=turn_context_meta,
        limits=usage_collector.limits,
        attachments=usage_collector.attachments,
    )
    loop_guard = TurnToolLoopGuard(surface=SkillSurface.CHAT)
    tool_ctx = ChatToolContext(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        workspace=workspace,
        audit=tool_audit,
        guards=guards,
        cancel_event=ctx.cancel_event,
        loop_guard=loop_guard,
    )
    plan_enable_tools = ctx.enable_tools and turn_ctx.enable_tools
    asset_media_types = {
        asset.asset_id: asset.media_type for asset in compiled.tool_asset_index
    }
    tools = build_chat_turn_tools(
        tool_ctx,
        enable_tools=plan_enable_tools,
        user_id=ctx.user_id,
        tool_asset_ids=frozenset(compiled.tool_asset_ids),
        asset_media_types=asset_media_types,
    )
    selected_expert_key = getattr(ctx.conversation, "selected_expert_key", None)
    expert_identity_block = ""
    if selected_expert_key:
        allowed_names = profile_tool_names_for_chat(selected_expert_key)
        tools = intersect_chat_tools_with_profile(tools, allowed_names)
        expert_identity_block = build_expert_identity_block(selected_expert_key)
        ctx.sse_attribution = {
            "speaker_role": "expert",
            "expert_id": selected_expert_key,
            "task_id": None,
        }
    attachment_briefs = [
        AttachmentBrief(
            attachment_id=row.id,
            filename=row.filename,
            mime_type=row.mime_type,
            status=attachment_status_label(row.status),
            is_attached=bool(row.is_attached),
            workspace_path=_workspace_path_for_attachment(
                row=row,
                turn_asset_ids=frozenset(compiled.tool_asset_ids),
                materialized_by_asset=materialized_by_asset,
            ),
        )
        for row in attachment_rows
    ]
    workspace_hint = f"会话工作区相对路径根目录：{workspace}"
    has_visual_refs = any(
        asset.media_type in {TurnMediaType.IMAGE, TurnMediaType.VIDEO}
        for asset in compiled.tool_asset_index
    )
    prompt_ctx = TurnPromptContext(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        model_key=ctx.model_key,
        enable_tools=plan_enable_tools,
        tool_names=[tool.name for tool in tools],
        has_turn_media_refs=has_visual_refs,
    )
    memory_tools_enabled = any(
        name in {"manage_user_memory", "recall_user_memory"} for name in prompt_ctx.tool_names
    )
    injection = await build_memory_injection(
        MemoryInjectionRequest(
            domain="chat",
            user_id=ctx.user_id,
            user_message=ctx.content,
            is_resume=False,
            memory_tools_enabled=memory_tools_enabled,
            store=get_memory_store(),
        )
    )
    selected_paths = {item.path for item in ctx.selected_skills}
    index_items = await get_user_skill_port().list_enabled_for_index(
        surface=SkillSurface.CHAT,
        user_id=ctx.user_id,
        project_id=ctx.project_id,
    )
    user_skill_index_text = format_user_skill_index_text(index_items, selected_paths)
    selected_bodies_text = format_selected_skill_bodies_text(ctx.selected_skills)
    attachment_context = build_attachment_context_block(
        attachments=attachment_briefs,
        workspace_hint=workspace_hint,
    )
    system_prompt = PromptComposer.build_turn_system(
        prompt_ctx,
        memory_blocks_text=injection.memory_blocks_text,
        memory_ops_brief=injection.ops_brief_text,
        user_skill_index_text=user_skill_index_text,
        selected_bodies_text=selected_bodies_text,
        turn_references_block=compiled.reference_index,
        attachment_context_block=attachment_context if turn_ctx.has_attachments else "",
    )
    if expert_identity_block:
        system_prompt = f"{expert_identity_block}\n\n{system_prompt}"
    spec = get_model_spec(ctx.model_key)
    turn_human = HumanMessage(content=compiled.human_message)
    bind_attachment_ids = await chat_attachment_service.attachment_ids_for_asset_ids(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        asset_ids=compiled.tool_asset_ids,
    )
    await persist_user_message(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        content=ctx.content,
        turn_human=turn_human,
        bind_attachment_ids=bind_attachment_ids,
        turn_id=ctx.turn_id,
        client_turn_id=ctx.client_turn_id,
        input_snapshot=input_snapshot_dict(ctx.user_input),
    )
    llm = GatewayChatModel(
        model_key=ctx.model_key,
        spec=spec,
        cancel_event=ctx.cancel_event,
    )
    checkpointer = ctx.checkpointer or get_chat_checkpointer()
    agent = build_chat_agent(
        llm,
        tools,
        checkpointer,
        system_prompt=system_prompt,
        store=get_memory_store(),
    )
    config = chat_runnable_config(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        workspace=str(workspace),
        turn_id=ctx.turn_id,
    )
    ctx.agent = agent
    ctx.runnable_config = config
    ctx.input_messages = [turn_human]
    ctx.turn_start_messages = await capture_turn_checkpoint_messages(agent, config)
    recovery_hook = ChatRecoveryHook(
        llm=llm,
        system_prompt=system_prompt,
        config=config,
        guards=guards,
        cancel_event=ctx.cancel_event,
        model_key=ctx.model_key,
        turn_id=ctx.turn_id,
        persistence=persistence,
        recorder=recorder,
        usage_collector=usage_collector,
        ctx=tool_ctx,
        turn_context_meta=turn_context_meta,
        tool_audit=tool_audit,
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        agent=agent,
    )
    ctx.recovery_hook = recovery_hook
    ctx.session = ChatTurnSession(
        persistence=persistence,
        recorder=recorder,
        usage_collector=usage_collector,
        observation=observation,
        guards=guards,
        ctx=tool_ctx,
        turn_context_meta=turn_context_meta,
        tool_audit=tool_audit,
        turn_id=ctx.turn_id,
        conversation_id=ctx.conversation_id,
        user_id=ctx.user_id,
        model_key=ctx.model_key,
        content=ctx.content,
        turn_asset_ids=compiled.tool_asset_ids,
        conversation=ctx.conversation,
        workspace=workspace,
        cancel_event=ctx.cancel_event,
        recovery_hook=recovery_hook,
        agent=agent,
        runnable_config=config,
    )
    return ctx


async def _build_agent(ctx: ChatMountContext) -> CompiledStateGraph:
    """返回已准备好的 Chat agent 图"""
    assert ctx.agent is not None
    return ctx.agent


def _build_guards(ctx: ChatMountContext) -> TurnGuards:
    """返回 turn 守卫配置"""
    assert ctx.guards is not None
    return ctx.guards


def _build_subscribers(ctx: ChatMountContext):
    """组装 Chat 生命周期订阅者"""
    assert ctx.session is not None
    return build_chat_lifecycle_subscribers(ctx.session)


def _build_runnable_config(ctx: ChatMountContext) -> RunnableConfig:
    """返回 LangGraph runnable_config"""
    assert ctx.runnable_config is not None
    return ctx.runnable_config


def _terminal_policy(ctx: ChatMountContext) -> SseTerminalPolicy:
    """构造 SSE 终态策略"""
    assert ctx.persistence is not None
    persistence = ctx.persistence
    return SseTerminalPolicy(
        emit_done_on_completed=True,
        emit_done_after_failure=True,
        message_ids=lambda: persistence.message_ids,
    )


def _recovery(ctx: ChatMountContext):
    """返回恢复钩子"""
    return ctx.recovery_hook


def _preview(_ctx: ChatMountContext):
    """返回工具结果预览函数"""
    return lambda name, result, ok: sanitize_tool_step_preview(name, result, ok=ok)


def _input_messages(ctx: ChatMountContext):
    """返回本轮输入消息"""
    return ctx.input_messages


def _heartbeat(_ctx: ChatMountContext) -> int:
    """返回心跳间隔秒数"""
    return int(settings.CHAT_HEARTBEAT_INTERVAL_SEC)


def _start_repair(ctx: ChatMountContext):
    """返回 turn 开始时的 checkpoint 修复回调"""
    async def repair(agent, runnable_config, **kwargs):
        """按原因修复 Chat checkpoint"""
        reason = kwargs.get("reason")
        await repair_chat_checkpoint_if_needed(
            agent,
            runnable_config,
            conversation_id=ctx.conversation_id,
            turn_id=ctx.turn_id,
            reason="stale_unresolved" if reason == "turn_start" else (reason or "stale_unresolved"),
        )

    return repair


def _cleanup_repair(ctx: ChatMountContext):
    """返回 turn 清理时的 checkpoint 修复回调"""
    return _start_repair(ctx)


def _sse_attribution(ctx: ChatMountContext) -> dict[str, str | None]:
    """返回 SSE 发言归因字段"""
    return dict(ctx.sse_attribution or {})


CHAT_MOUNT = AgentMountSpec(
    name=SkillSurface.CHAT,
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
    resolve_client_turn_id=lambda ctx: ctx.client_turn_id,
    resolve_sse_attribution=_sse_attribution,
)
