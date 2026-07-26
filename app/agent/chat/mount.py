"""Chat AgentMountSpec — surface assembly via mount callables."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.agent.factory import build_chat_agent
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.chat.mcp.client import load_mcp_tools
from app.agent.chat.memory.store import chat_runnable_config
from app.agent.chat.memory.turn_input import build_turn_human_message
from app.agent.chat.prompt.composer import PromptComposer
from app.agent.chat.prompt.types import AttachmentBrief, TurnPromptContext
from app.agent.chat.tools.lc_tools import ChatToolContext, build_langchain_tools
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
from app.agent.chat.vision.gate import assert_vision_turn_allowed, is_image_mime
from app.agent.chat.workspace import conversation_workspace
from app.agent.chat.workspace.session import ensure_workspace_session
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.memory.inject import MemoryInjectionRequest, build_memory_injection
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.mounts.spec import AgentMountSpec
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.contracts.metadata import ToolAuditMetadata, TurnContextMetadata
from app.server.chat.persistence.attachments import ChatAttachments
from app.server.chat.persistence.conversations import ChatConversations
from app.server.chat.services.attachments.service import chat_attachment_service
from app.server.chat.services.attachments.status import attachment_status_label
from app.server.chat.services.attachments.turn_prep import apply_attachment_intent
from app.server.chat.services.constants import CHAT_CHECKPOINT_THREAD_PREFIX
from app.server.infra.config import settings


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
    attachment_ids: list[int]
    enable_tools: bool
    client_turn_id: str | None = None
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


def _thread_id(ctx: ChatMountContext) -> str:
    return f"{CHAT_CHECKPOINT_THREAD_PREFIX}-{ctx.conversation_id}"


async def _prepare_turn(ctx: ChatMountContext) -> ChatMountContext:
    persistence = TurnPersistence(user_id=ctx.user_id, conversation_id=ctx.conversation_id)
    ctx.persistence = persistence
    await apply_attachment_intent(ctx.user_id, ctx.conversation_id, ctx.attachment_ids)
    attachment_rows = await ChatAttachments.filter(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        is_attached=True,
    )
    turn_ctx = build_turn_context(
        has_attachments=bool(attachment_rows),
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
    materialized: list = []
    materialized_by_id: dict = {}
    if turn_ctx.enable_materialize:
        import time as _time

        mat_started = _time.perf_counter()
        materialized = await chat_attachment_service.materialize_to_workspace(
            workspace,
            list(attachment_rows),
        )
        materialized_by_id = {item.attachment_id: item for item in materialized}
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
    loop_guard = TurnToolLoopGuard(surface="chat")
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
    tools = build_langchain_tools(tool_ctx, enable_tools=plan_enable_tools)
    tools = tools + load_mcp_tools()
    attachment_briefs = [
        AttachmentBrief(
            attachment_id=row.id,
            filename=row.filename,
            mime_type=row.mime_type or "application/octet-stream",
            status=attachment_status_label(row.status),
            is_attached=bool(row.is_attached),
            workspace_path=(mat.workspace_path if (mat := materialized_by_id.get(row.id)) else ""),
        )
        for row in attachment_rows
    ]
    workspace_hint = f"会话工作区相对路径根目录：{workspace}"
    requested_ids = set(ctx.attachment_ids)
    image_attachment_ids = [
        row.id
        for row in attachment_rows
        if row.id in requested_ids and is_image_mime(row.mime_type or "")
    ]
    spec = get_model_spec(ctx.model_key)
    assert_vision_turn_allowed(spec=spec, image_attachment_ids=image_attachment_ids)
    prompt_ctx = TurnPromptContext(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        model_key=ctx.model_key,
        enable_tools=plan_enable_tools,
        tool_names=[tool.name for tool in tools],
        has_vision_images=bool(image_attachment_ids),
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
    system_prompt = PromptComposer.build_turn_system(
        prompt_ctx,
        memory_blocks_text=injection.memory_blocks_text,
        memory_ops_brief=injection.ops_brief_text,
    )
    turn_human = build_turn_human_message(
        ctx.content,
        attachments=attachment_briefs,
        workspace_hint=workspace_hint,
        image_attachment_ids=image_attachment_ids,
    )
    await persist_user_message(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        content=ctx.content,
        turn_human=turn_human,
        attachment_ids=ctx.attachment_ids,
        turn_id=ctx.turn_id,
        client_turn_id=ctx.client_turn_id,
    )
    attachment_binary_paths = {
        item.attachment_id: item.workspace_path
        for item in materialized
        if item.attachment_id in image_attachment_ids
    }
    llm = GatewayChatModel(
        model_key=ctx.model_key,
        spec=spec,
        cancel_event=ctx.cancel_event,
        vision_hydrate_attachment_ids=image_attachment_ids,
        vision_attachment_binary_paths=attachment_binary_paths,
        vision_conversation_workspace=str(workspace),
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
        attachment_ids=ctx.attachment_ids,
        conversation=ctx.conversation,
        workspace=workspace,
        cancel_event=ctx.cancel_event,
        recovery_hook=recovery_hook,
        agent=agent,
        runnable_config=config,
    )
    return ctx


async def _build_agent(ctx: ChatMountContext) -> CompiledStateGraph:
    assert ctx.agent is not None
    return ctx.agent


def _build_guards(ctx: ChatMountContext) -> TurnGuards:
    assert ctx.guards is not None
    return ctx.guards


def _build_subscribers(ctx: ChatMountContext):
    assert ctx.session is not None
    return build_chat_lifecycle_subscribers(ctx.session)


def _build_runnable_config(ctx: ChatMountContext) -> RunnableConfig:
    assert ctx.runnable_config is not None
    return ctx.runnable_config


def _terminal_policy(ctx: ChatMountContext) -> SseTerminalPolicy:
    assert ctx.persistence is not None
    persistence = ctx.persistence
    return SseTerminalPolicy(
        emit_done_on_completed=True,
        emit_done_after_failure=True,
        message_ids=lambda: persistence.message_ids,
    )


def _recovery(ctx: ChatMountContext):
    return ctx.recovery_hook


def _preview(_ctx: ChatMountContext):
    return lambda name, result, ok: sanitize_tool_step_preview(name, result, ok=ok)


def _input_messages(ctx: ChatMountContext):
    return ctx.input_messages


def _heartbeat(_ctx: ChatMountContext) -> int:
    return int(settings.CHAT_HEARTBEAT_INTERVAL_SEC)


def _start_repair(ctx: ChatMountContext):
    async def repair(agent, runnable_config, **kwargs):
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
    return _start_repair(ctx)


CHAT_MOUNT = AgentMountSpec(
    name="chat",
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
)
