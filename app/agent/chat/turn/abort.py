"""Unified turn abort: one path for user stop, gate cancel, and in-flight stream cancel."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.chat.agent.events import AgentEventType
from app.agent.chat.agent.factory import build_chat_agent
from app.agent.chat.agent.runner import run_agent_turn_stream
from app.agent.chat.gate import assets as gate_assets
from app.agent.chat.gate import meta as gate_meta_store
from app.agent.chat.gate import turn_auth as turn_auth_store
from app.agent.chat.gate.pending import clear_gate_pending, get_gate_pending
from app.agent.chat.llm.gateway_chat_model import GatewayChatModel
from app.agent.chat.llm.registry import get_model_spec
from app.agent.chat.memory.store import chat_runnable_config
from app.agent.chat.mcp.client import load_mcp_tools
from app.agent.chat.tools.lc_tools import ChatToolContext, build_langchain_tools
from app.agent.chat.tools.result import GATE_CANCELLED
from app.agent.chat.turn.cancel_signal import turn_cancel_signal
from app.agent.chat.turn.checkpoint import (
    capture_turn_checkpoint_messages,
    repair_turn_checkpoint_on_cancel,
    restore_turn_checkpoint,
)
from app.agent.chat.turn.lifecycle import clear_turn_active
from app.agent.chat.turn.lock import conversation_turn_lock
from app.agent.chat.turn.persistence import TurnPersistence
from app.agent.chat.turn.trace import log_stage
from app.agent.chat.workspace import conversation_workspace
from app.agent.chat.workspace.session import ensure_workspace_session
from app.agent.runtime.checkpointer import get_chat_checkpointer
from app.server.infra.logger import logger
from app.agent.runtime.memory_store import get_memory_store
from app.agent.runtime.turn.tool_loop_guard import TurnToolLoopGuard
from app.server.chat.persistence.conversations import ChatConversations

_GATE_CANCEL_TOOL = "request_user_gate"
_GATE_RESOLVE_TIMEOUT_SEC = 60


class TurnAbortReason(StrEnum):
    USER_CANCEL = "user_cancel"
    GATE_CANCEL = "gate_cancel"


@dataclass(frozen=True)
class TurnAbortResult:
    turn_id: str | None
    message_id: int | None = None


def trim_gate_interrupt_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Drop the in-flight request_user_gate call so the next turn starts clean."""
    if len(messages) >= 2:
        prev = messages[-2]
        last = messages[-1]
        if isinstance(prev, AIMessage) and isinstance(last, ToolMessage):
            if any(str(call.get("name") or "") == _GATE_CANCEL_TOOL for call in (prev.tool_calls or [])):
                if str(last.tool_call_id or "") in {
                    str(call.get("id") or "") for call in (prev.tool_calls or [])
                }:
                    return messages[:-2]
    if messages:
        last = messages[-1]
        if isinstance(last, AIMessage) and any(
            str(call.get("name") or "") == _GATE_CANCEL_TOOL for call in (last.tool_calls or [])
        ):
            return messages[:-1]
    return messages


async def clear_stale_gate_ephemeral(conversation_id: int, *, user_id: int | None = None) -> None:
    """Drop orphan gate Redis state (e.g. stale resuming) without touching the graph."""
    pending = await get_gate_pending(conversation_id)
    if pending is None:
        return
    status = str(pending.get("status") or "pending")
    if status == "pending":
        return
    gate_id = str(pending.get("gate_id") or "")
    await clear_gate_pending(conversation_id)
    if gate_id and user_id is not None:
        workspace = conversation_workspace(user_id, conversation_id)
        gate_assets.delete_gate_assets(workspace, gate_id)
    if gate_id:
        await gate_meta_store.clear_gate_meta(gate_id)
    logger.info(
        "chat.turn.abort.stale_gate_cleared",
        conversation_id=conversation_id,
        gate_id=gate_id or None,
        status=status,
    )


async def _resolve_turn_id(
    *,
    conversation: ChatConversations,
    conversation_id: int,
    explicit_turn_id: str | None,
) -> str | None:
    if explicit_turn_id:
        return explicit_turn_id
    if conversation.active_turn_id:
        return str(conversation.active_turn_id)
    pending = await get_gate_pending(conversation_id)
    if pending and pending.get("turn_id"):
        return str(pending["turn_id"])
    lock_turn = await conversation_turn_lock.peek(conversation_id)
    return lock_turn


def _should_resolve_gate_interrupt(
    *,
    reason: TurnAbortReason,
    gate_id: str | None,
    turn_id: str | None,
    pending: dict[str, Any] | None,
) -> bool:
    if not gate_id or not turn_id:
        return False
    if reason == TurnAbortReason.GATE_CANCEL:
        return True
    if pending is None:
        return False
    status = str(pending.get("status") or "pending")
    return status in {"pending", "submitted"}


async def resolve_gate_interrupt(
    *,
    user_id: int,
    conversation_id: int,
    model_key: str,
    turn_id: str,
    gate_id: str,
) -> bool:
    """Bounded LangGraph resume(action=cancel) + checkpoint trim. Does not persist DB close."""
    workspace = conversation_workspace(user_id, conversation_id)
    ensure_workspace_session(workspace)
    loop_guard = TurnToolLoopGuard(surface="chat")
    ctx = ChatToolContext(
        user_id=user_id,
        conversation_id=conversation_id,
        workspace=workspace,
        audit=[],
        loop_guard=loop_guard,
    )
    tools = build_langchain_tools(ctx, enable_tools=True)
    tools.extend(load_mcp_tools())
    spec = get_model_spec(model_key)
    llm = GatewayChatModel(model_key=model_key, spec=spec)
    agent = build_chat_agent(
        llm,
        tools,
        get_chat_checkpointer(),
        store=get_memory_store(),
    )
    config: RunnableConfig = chat_runnable_config(
        user_id=user_id,
        conversation_id=conversation_id,
        workspace=str(workspace),
        turn_id=turn_id,
    )
    resume_payload: dict[str, Any] = {"action": "cancel", "gate_id": gate_id}
    log_stage(
        "chat.turn.abort.resolve_gate",
        conversation_id=conversation_id,
        turn_id=turn_id,
        gate_id=gate_id,
    )

    stream = run_agent_turn_stream(
        agent,
        Command(resume=resume_payload),
        turn_id=turn_id,
        config=config,
        tools_by_name={tool.name: tool for tool in tools},
    )
    gate_cancel_seen = False
    try:
        async with asyncio.timeout(_GATE_RESOLVE_TIMEOUT_SEC):
            async for event in stream:
                if event.type != AgentEventType.TOOL_FINISHED:
                    continue
                if event.error_class == GATE_CANCELLED or event.tool_name == _GATE_CANCEL_TOOL:
                    gate_cancel_seen = True
                    break
    except TimeoutError:
        logger.warning(
            "chat.turn.abort.resolve_gate_timeout",
            conversation_id=conversation_id,
            turn_id=turn_id,
            gate_id=gate_id,
        )
    finally:
        await stream.aclose()

    current_messages = await capture_turn_checkpoint_messages(agent, config)
    trimmed = trim_gate_interrupt_messages(current_messages)
    if trimmed != current_messages:
        await restore_turn_checkpoint(
            agent,
            config,
            trimmed,
            conversation_id=conversation_id,
            turn_id=turn_id,
            reason="gate_cancel",
        )
    if not gate_cancel_seen:
        logger.warning(
            "chat.turn.abort.resolve_gate_no_tool_finish",
            conversation_id=conversation_id,
            turn_id=turn_id,
            gate_id=gate_id,
        )
    return gate_cancel_seen


async def clear_turn_ephemeral_state(
    *,
    conversation_id: int,
    conversation: ChatConversations,
    turn_id: str | None,
) -> None:
    pending = await get_gate_pending(conversation_id)
    gate_id = str(pending.get("gate_id") or "") if pending else ""
    await clear_gate_pending(conversation_id)
    if turn_id:
        await turn_auth_store.clear_turn_auth(conversation_id, turn_id)
    if gate_id:
        workspace = conversation_workspace(conversation.user_id, conversation_id)
        gate_assets.delete_gate_assets(workspace, gate_id)
        await gate_meta_store.clear_gate_meta(gate_id)
    await conversation_turn_lock.force_cancel(conversation_id)
    if turn_id:
        with contextlib.suppress(Exception):
            await turn_cancel_signal.clear(conversation_id, turn_id)
    await clear_turn_active(conversation)


async def abort_user_turn(
    *,
    user_id: int,
    conversation: ChatConversations,
    reason: TurnAbortReason,
    turn_id: str | None = None,
    gate_id: str | None = None,
    step_index: int = 0,
    persist_close_message: bool = True,
) -> TurnAbortResult:
    """API entry: /turn/cancel and /gate/cancel."""
    conversation_id = conversation.id
    pending = await get_gate_pending(conversation_id)
    resolved_turn_id = await _resolve_turn_id(
        conversation=conversation,
        conversation_id=conversation_id,
        explicit_turn_id=turn_id,
    )
    resolved_gate_id = gate_id or (str(pending.get("gate_id") or "") if pending else None) or None

    log_stage(
        "chat.turn.abort.start",
        conversation_id=conversation_id,
        turn_id=resolved_turn_id,
        gate_id=resolved_gate_id,
        reason=str(reason),
    )

    if resolved_turn_id:
        await turn_cancel_signal.signal(conversation_id, resolved_turn_id)

    if _should_resolve_gate_interrupt(
        reason=reason,
        gate_id=resolved_gate_id,
        turn_id=resolved_turn_id,
        pending=pending,
    ):
        assert resolved_turn_id is not None
        assert resolved_gate_id is not None
        pending_model = str(pending.get("model_key") or "").strip() if pending else ""
        if not pending_model:
            logger.error(
                "chat.turn.abort.missing_turn_model",
                conversation_id=conversation_id,
                turn_id=resolved_turn_id,
                gate_id=resolved_gate_id,
            )
        else:
            try:
                await resolve_gate_interrupt(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    model_key=pending_model,
                    turn_id=resolved_turn_id,
                    gate_id=resolved_gate_id,
                )
            except Exception:
                logger.exception(
                    "chat.turn.abort.resolve_gate_failed",
                    conversation_id=conversation_id,
                    turn_id=resolved_turn_id,
                    gate_id=resolved_gate_id,
                )

    await clear_turn_ephemeral_state(
        conversation_id=conversation_id,
        conversation=conversation,
        turn_id=resolved_turn_id,
    )

    message_id: int | None = None
    if persist_close_message and resolved_turn_id:
        persistence = TurnPersistence(user_id=user_id, conversation_id=conversation_id)
        message_id = await persistence.persist_cancelled_close(
            turn_id=resolved_turn_id,
            step_index=step_index,
        )

    logger.info(
        "chat.turn.abort.done",
        conversation_id=conversation_id,
        turn_id=resolved_turn_id,
        message_id=message_id,
        reason=str(reason),
    )
    return TurnAbortResult(turn_id=resolved_turn_id, message_id=message_id)


async def finalize_inflight_turn_abort(
    *,
    user_id: int,
    conversation: ChatConversations,
    conversation_id: int,
    turn_id: str,
    step_index: int,
    persistence: TurnPersistence,
    agent: CompiledStateGraph | None = None,
    config: RunnableConfig | None = None,
    turn_start_messages: list[BaseMessage] | None = None,
    already_persisted_close: bool = False,
) -> TurnAbortResult:
    """Stream/resume loop saw cancel_event; agent may still be in memory."""
    message_id: int | None = None
    if not already_persisted_close:
        message_id = await persistence.persist_cancelled_close(
            turn_id=turn_id,
            step_index=step_index,
        )

    if agent is not None and config is not None:
        with contextlib.suppress(Exception):
            await repair_turn_checkpoint_on_cancel(
                agent,
                config,
                conversation_id=conversation_id,
                turn_id=turn_id,
                turn_start_messages=turn_start_messages,
            )

    await clear_stale_gate_ephemeral(conversation_id, user_id=user_id)
    await clear_turn_ephemeral_state(
        conversation_id=conversation_id,
        conversation=conversation,
        turn_id=turn_id,
    )

    return TurnAbortResult(turn_id=turn_id, message_id=message_id)
