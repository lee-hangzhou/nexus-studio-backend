"""创作提示词助手 AgentMountSpec：复用会话脚手架与 chat 守卫/recovery，独立工具与 system prompt。"""

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
from app.agent.chat.prompt.types import TurnPromptContext
from app.agent.chat.tools.lc_tools import ChatToolContext
from app.agent.chat.tools.ui_preview import sanitize_tool_step_preview
from app.agent.chat.turn.checkpoint import (
    capture_turn_checkpoint_messages,
    repair_chat_checkpoint_if_needed,
)
from app.agent.chat.turn.conversation_turn_scaffold import (
    attachment_context_for_scaffold,
    prepare_conversation_turn_scaffold,
)
from app.agent.chat.turn.event_recorder import TurnAgentEventRecorder
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.observation import TurnObservationContext
from app.agent.chat.turn.persistence import TurnPersistence, persist_user_message
from app.agent.chat.turn.recovery_hook import ChatRecoveryHook
from app.agent.chat.turn.session import ChatTurnSession
from app.agent.chat.turn.subscribers import build_chat_lifecycle_subscribers
from app.agent.chat.turn.usage_log import TurnUsageCollector
from app.agent.prompt_assistant.prompt import build_prompt_assistant_system
from app.agent.prompt_assistant.subscribers import PromptAssistantComposerPromptSubscriber
from app.agent.prompt_assistant.tools.build import build_prompt_assistant_tools
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.mounts.spec import AgentMountSpec
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.agent.runtime.turn_engine.terminal_policy import SseTerminalPolicy
from app.contracts.composer_prompt import GenerateComposerContext
from app.contracts.turn_content import CompiledTurnInput, TurnMediaType, TurnUserInput, input_snapshot_dict
from app.server.chat.persistence.conversations import ChatConversations
from app.server.chat.services.attachments.service import chat_attachment_service
from app.server.chat.services.constants import CHAT_CHECKPOINT_THREAD_PREFIX
from app.server.infra.config import settings
from app.server.skills.domain.enums import SkillSurface


@dataclass
class PromptAssistantMountContext:
    """创作提示词助手 mount 上下文。"""

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
    composer_context: GenerateComposerContext | None = None
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


def _thread_id(ctx: PromptAssistantMountContext) -> str:
    """与 chat 共用 session 前缀；conversation_id 隔离线程。"""
    return f"{CHAT_CHECKPOINT_THREAD_PREFIX}-{ctx.conversation_id}"


async def _prepare_turn(ctx: PromptAssistantMountContext) -> PromptAssistantMountContext:
    """组装提示词助手 turn：公共脚手架 + PA 工具与 system prompt。"""
    scaffold = await prepare_conversation_turn_scaffold(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        turn_id=ctx.turn_id,
        model_key=ctx.model_key,
        enable_tools=ctx.enable_tools,
        user_input=ctx.user_input,
        log_prefix="prompt_assistant.turn",
    )
    compiled = scaffold.compiled
    ctx.compiled_input = compiled
    ctx.content = scaffold.content
    ctx.persistence = scaffold.persistence
    ctx.workspace = scaffold.workspace
    ctx.guards = scaffold.guards
    ctx.usage_collector = scaffold.usage_collector
    ctx.observation = scaffold.observation
    ctx.recorder = scaffold.recorder

    loop_guard = TurnToolLoopGuard(surface=SkillSurface.PROMPT_ASSISTANT)
    tool_ctx = ChatToolContext(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        workspace=scaffold.workspace,
        audit=scaffold.tool_audit,
        guards=scaffold.guards,
        cancel_event=ctx.cancel_event,
        loop_guard=loop_guard,
        source_user_text=compiled.human_message,
    )
    asset_media_types: dict[int, TurnMediaType] = {
        asset.asset_id: asset.media_type for asset in compiled.tool_asset_index
    }
    tools = build_prompt_assistant_tools(
        user_id=ctx.user_id,
        enable_tools=scaffold.plan_enable_tools,
        tool_asset_ids=frozenset(compiled.tool_asset_ids),
        asset_media_types=asset_media_types,
    )
    prompt_ctx = TurnPromptContext(
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        model_key=ctx.model_key,
        enable_tools=scaffold.plan_enable_tools,
        tool_names=[tool.name for tool in tools],
        has_turn_media_refs=scaffold.has_visual_refs,
    )
    system_prompt = build_prompt_assistant_system(
        prompt_ctx,
        composer_context=ctx.composer_context,
        turn_references_block=compiled.reference_index,
        attachment_context_block=attachment_context_for_scaffold(scaffold),
    )
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
        workspace=str(scaffold.workspace),
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
        guards=scaffold.guards,
        cancel_event=ctx.cancel_event,
        model_key=ctx.model_key,
        turn_id=ctx.turn_id,
        persistence=scaffold.persistence,
        recorder=scaffold.recorder,
        usage_collector=scaffold.usage_collector,
        ctx=tool_ctx,
        turn_context_meta=scaffold.turn_context_meta,
        tool_audit=scaffold.tool_audit,
        user_id=ctx.user_id,
        conversation_id=ctx.conversation_id,
        agent=agent,
    )
    ctx.recovery_hook = recovery_hook
    ctx.session = ChatTurnSession(
        persistence=scaffold.persistence,
        recorder=scaffold.recorder,
        usage_collector=scaffold.usage_collector,
        observation=scaffold.observation,
        guards=scaffold.guards,
        ctx=tool_ctx,
        turn_context_meta=scaffold.turn_context_meta,
        tool_audit=scaffold.tool_audit,
        turn_id=ctx.turn_id,
        conversation_id=ctx.conversation_id,
        user_id=ctx.user_id,
        model_key=ctx.model_key,
        content=ctx.content,
        turn_asset_ids=compiled.tool_asset_ids,
        conversation=ctx.conversation,
        workspace=scaffold.workspace,
        cancel_event=ctx.cancel_event,
        recovery_hook=recovery_hook,
        agent=agent,
        runnable_config=config,
    )
    return ctx


async def _build_agent(ctx: PromptAssistantMountContext) -> CompiledStateGraph:
    """返回已准备好的 agent 图。"""
    if ctx.agent is None:
        raise RuntimeError("prompt assistant agent missing after prepare_turn")
    return ctx.agent


def _build_guards(ctx: PromptAssistantMountContext) -> TurnGuards:
    """返回 turn 守卫。"""
    if ctx.guards is None:
        raise RuntimeError("prompt assistant guards missing after prepare_turn")
    return ctx.guards


def _build_subscribers(ctx: PromptAssistantMountContext):
    """组装会话生命周期订阅者与写回帧订阅者。"""
    if ctx.session is None:
        raise RuntimeError("prompt assistant session missing after prepare_turn")
    return [
        *build_chat_lifecycle_subscribers(ctx.session),
        PromptAssistantComposerPromptSubscriber(),
    ]


def _build_runnable_config(ctx: PromptAssistantMountContext) -> RunnableConfig:
    """返回 LangGraph runnable_config。"""
    if ctx.runnable_config is None:
        raise RuntimeError("prompt assistant runnable_config missing after prepare_turn")
    return ctx.runnable_config


def _terminal_policy(ctx: PromptAssistantMountContext) -> SseTerminalPolicy:
    """构造 SSE 终态策略。"""
    if ctx.persistence is None:
        raise RuntimeError("prompt assistant persistence missing after prepare_turn")
    persistence = ctx.persistence
    return SseTerminalPolicy(
        emit_done_on_completed=True,
        emit_done_after_failure=True,
        message_ids=lambda: persistence.message_ids,
    )


def _recovery(ctx: PromptAssistantMountContext):
    """返回 recovery hook。"""
    return ctx.recovery_hook


def _preview(_ctx: PromptAssistantMountContext):
    """工具预览清洗函数。"""
    return lambda name, result, ok: sanitize_tool_step_preview(name, result, ok=ok)


def _input_messages(ctx: PromptAssistantMountContext):
    """本轮输入消息。"""
    return ctx.input_messages


def _heartbeat(_ctx: PromptAssistantMountContext) -> int:
    """SSE heartbeat 间隔秒数。"""
    return int(settings.CHAT_HEARTBEAT_INTERVAL_SEC)


def _start_repair(ctx: PromptAssistantMountContext):
    """checkpoint 修复闭包。"""

    async def repair(agent, runnable_config, **kwargs):
        """修复 stale checkpoint。"""
        reason = kwargs.get("reason")
        await repair_chat_checkpoint_if_needed(
            agent,
            runnable_config,
            conversation_id=ctx.conversation_id,
            turn_id=ctx.turn_id,
            reason="stale_unresolved" if reason == "turn_start" else (reason or "stale_unresolved"),
        )

    return repair


def _sse_attribution(ctx: PromptAssistantMountContext) -> dict[str, str | None]:
    """SSE 归因字段。"""
    return dict(ctx.sse_attribution or {})


PROMPT_ASSISTANT_MOUNT = AgentMountSpec(
    name=SkillSurface.PROMPT_ASSISTANT,
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
    resolve_on_turn_cleanup_repair=_start_repair,
    resolve_client_turn_id=lambda ctx: ctx.client_turn_id,
    resolve_sse_attribution=_sse_attribution,
)
