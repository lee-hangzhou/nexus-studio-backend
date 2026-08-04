"""Chat 会话类 mount 共用的 turn 脚手架（compile / 附件材料化 / observation）。

Chat 与 prompt_assistant 共用此段后各自挂载工具与 system prompt；禁止再整段复制。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from app.agent.chat.memory.turn_input import build_attachment_context_block
from app.agent.chat.prompt.types import AttachmentBrief
from app.agent.chat.turn.context import TurnContext, build_turn_context
from app.agent.chat.turn.event_recorder import TurnAgentEventRecorder
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.observation import TurnObservationContext
from app.agent.chat.turn.observation_store import save_turn_observation_snapshot
from app.agent.chat.turn.persistence import TurnPersistence
from app.agent.chat.turn.trace import log_stage
from app.agent.chat.turn.usage_log import TurnUsageCollector
from app.agent.chat.workspace import conversation_workspace
from app.agent.chat.workspace.session import ensure_workspace_session
from app.contracts.metadata import ToolAuditMetadata, TurnContextMetadata
from app.contracts.turn_content import (
    CompiledTurnInput,
    TurnMediaType,
    TurnUserInput,
    compile_turn_input,
)
from app.server.chat.persistence.attachments import ChatAttachments
from app.server.chat.services.attachments.service import chat_attachment_service
from app.server.chat.services.attachments.status import attachment_status_label
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.infra.config import settings


@dataclass(slots=True)
class ConversationTurnScaffold:
    """会话 turn 公共脚手架产物。"""

    compiled: CompiledTurnInput
    content: str
    persistence: TurnPersistence
    attachment_rows: list[ChatAttachments]
    turn_ctx: TurnContext
    turn_context_meta: TurnContextMetadata
    workspace: Path
    materialized_by_asset: dict[int, str]
    materialized_paths: list[str]
    guards: TurnGuards
    tool_audit: list[ToolAuditMetadata]
    usage_collector: TurnUsageCollector
    observation: TurnObservationContext
    recorder: TurnAgentEventRecorder
    attachment_briefs: list[AttachmentBrief]
    has_visual_refs: bool
    plan_enable_tools: bool


def _workspace_path_for_attachment(
    *,
    row: ChatAttachments,
    turn_asset_ids: frozenset[int],
    materialized_by_asset: dict[int, str],
) -> str:
    """解析附件在本轮工作区路径；本 turn 引用资产必须已 materialize。"""
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


async def prepare_conversation_turn_scaffold(
    *,
    user_id: int,
    conversation_id: int,
    turn_id: str,
    model_key: str,
    enable_tools: bool,
    user_input: TurnUserInput,
    log_prefix: str,
) -> ConversationTurnScaffold:
    """组装会话 turn 公共脚手架直至 observation / 附件简报。"""
    if user_input is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "enriched turn user input missing")
    compiled = compile_turn_input(user_input)
    content = compiled.human_message

    persistence = TurnPersistence(user_id=user_id, conversation_id=conversation_id)
    attachment_rows = await ChatAttachments.filter(
        user_id=user_id,
        conversation_id=conversation_id,
        is_attached=True,
    )
    has_turn_assets = bool(compiled.tool_asset_ids)
    turn_ctx = build_turn_context(
        has_attachments=bool(attachment_rows) or has_turn_assets,
        enable_tools=enable_tools,
    )
    log_stage(
        f"{log_prefix}.context",
        has_attachments=turn_ctx.has_attachments,
        enable_tools=turn_ctx.enable_tools,
    )
    turn_context_meta = TurnContextMetadata(
        has_attachments=turn_ctx.has_attachments,
        enable_tools=turn_ctx.enable_tools,
    )
    workspace = conversation_workspace(user_id, conversation_id)
    ensure_workspace_session(workspace)
    materialized_by_asset: dict[int, str] = {}
    materialized_paths: list[str] = []
    if turn_ctx.enable_materialize and compiled.tool_asset_ids:
        mat_started = time.perf_counter()
        materialized = await chat_attachment_service.materialize_turn_assets_to_workspace(
            workspace,
            user_id=user_id,
            conversation_id=conversation_id,
            asset_ids=compiled.tool_asset_ids,
        )
        materialized_by_asset = {item.asset_id: item.workspace_path for item in materialized}
        materialized_paths = [item.workspace_path for item in materialized]
        log_stage(f"{log_prefix}.materialize", started=mat_started, count=len(materialized))

    guards = TurnGuards(
        max_model_steps=settings.CHAT_MAX_ITERATIONS,
        max_tool_calls=settings.CHAT_MAX_TOOL_CALLS,
        wall_clock_sec=settings.CHAT_TURN_WALL_CLOCK_SEC,
        tool_repeat_guard=settings.CHAT_TOOL_REPEAT_GUARD,
    )
    tool_audit: list[ToolAuditMetadata] = []
    usage_collector = TurnUsageCollector(
        conversation_id=conversation_id,
        turn_id=turn_id,
        model_key=model_key,
        limits={
            "max_iterations": settings.CHAT_MAX_ITERATIONS,
            "max_tool_calls": settings.CHAT_MAX_TOOL_CALLS,
            "wall_clock_sec": settings.CHAT_TURN_WALL_CLOCK_SEC,
        },
        attachments=materialized_paths,
    )
    observation = TurnObservationContext.for_main_turn(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        model_key=model_key,
        persistence=persistence,
        usage_collector=usage_collector,
        turn_context=turn_context_meta,
        tool_audit=tool_audit,
    )
    recorder = TurnAgentEventRecorder(observation=observation)
    await save_turn_observation_snapshot(
        conversation_id=conversation_id,
        turn_id=turn_id,
        model_key=model_key,
        turn_context=turn_context_meta,
        limits=usage_collector.limits,
        attachments=usage_collector.attachments,
    )
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
    has_visual_refs = any(
        asset.media_type in {TurnMediaType.IMAGE, TurnMediaType.VIDEO}
        for asset in compiled.tool_asset_index
    )
    plan_enable_tools = enable_tools and turn_ctx.enable_tools
    return ConversationTurnScaffold(
        compiled=compiled,
        content=content,
        persistence=persistence,
        attachment_rows=attachment_rows,
        turn_ctx=turn_ctx,
        turn_context_meta=turn_context_meta,
        workspace=workspace,
        materialized_by_asset=materialized_by_asset,
        materialized_paths=materialized_paths,
        guards=guards,
        tool_audit=tool_audit,
        usage_collector=usage_collector,
        observation=observation,
        recorder=recorder,
        attachment_briefs=attachment_briefs,
        has_visual_refs=has_visual_refs,
        plan_enable_tools=plan_enable_tools,
    )


def attachment_context_for_scaffold(scaffold: ConversationTurnScaffold) -> str:
    """按脚手架生成附件上下文块；无附件时为空串。"""
    if not scaffold.turn_ctx.has_attachments:
        return ""
    return build_attachment_context_block(
        attachments=scaffold.attachment_briefs,
        workspace_hint=f"会话工作区相对路径根目录：{scaffold.workspace}",
    )
