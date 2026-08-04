"""Chat 表会话的 agent turn 生命周期壳（活跃标记 / 取消监视 / 清理）。

Chat 与 prompt_assistant 共用；禁止再复制一套 finally 清理路径。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Protocol

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from app.agent.chat.gate import turn_auth as turn_auth_store
from app.agent.chat.stream.encoder import encode_sse_frame
from app.agent.chat.stream.frames import StreamFrameType, create_stream_frame
from app.agent.chat.turn.abort import finalize_inflight_turn_abort
from app.agent.chat.turn.cancel_watch import watch_turn_cancel
from app.agent.chat.turn.event_recorder import TurnAgentEventRecorder
from app.agent.chat.turn.guards import TurnGuards
from app.agent.chat.turn.lifecycle import clear_turn_active, mark_turn_active, touch_conversation_updated
from app.agent.chat.turn.lock import conversation_turn_lock
from app.agent.chat.turn.observation import TurnObservationContext
from app.agent.chat.turn.observation_store import clear_turn_observation_snapshot
from app.agent.chat.turn.persistence import TurnPersistence
from app.agent.chat.turn.session import ChatTurnSession
from app.agent.chat.turn.trace import bind_turn_trace
from app.agent.chat.turn.usage_finalize import finalize_observation_segment
from app.agent.chat.turn.usage_log import TurnUsageCollector
from app.agent.runtime.mounts.context import MountTurnContext
from app.agent.runtime.mounts.spec import AgentMountSpec
from app.agent.runtime.turn.runner import stream_agent_turn
from app.server.chat.domain.stream_enums import StreamErrorCode
from app.server.chat.persistence.conversations import ChatConversations
from app.server.infra.logger import bind_context, log_exception, logger


class ConversationAgentTurnContext(MountTurnContext, Protocol):
    """会话 turn 生命周期壳所需的 mount 上下文字段。"""

    persistence: TurnPersistence | None
    guards: TurnGuards | None
    usage_collector: TurnUsageCollector | None
    observation: TurnObservationContext | None
    session: ChatTurnSession | None
    recorder: TurnAgentEventRecorder | None
    agent: CompiledStateGraph | None
    runnable_config: RunnableConfig | None
    turn_start_messages: list[BaseMessage] | None


async def stream_conversation_agent_turn(
    *,
    mount: AgentMountSpec,
    ctx: ConversationAgentTurnContext,
    conversation: ChatConversations,
    user_id: int,
    conversation_id: int,
    turn_id: str,
    cancel_event: asyncio.Event,
    log_event_prefix: str,
) -> AsyncIterator[str]:
    """对已构造的 mount 上下文执行标准会话 turn 生命周期。"""
    bind_context(
        user_id=user_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
    cancel_watch_task: asyncio.Task | None = None
    last_step_index = 0
    terminated_by = "error"
    gate_interrupted = False

    try:
        await mark_turn_active(conversation, turn_id)
        cancel_watch_task = asyncio.create_task(
            watch_turn_cancel(
                conversation_id=conversation_id,
                turn_id=turn_id,
                cancel_event=cancel_event,
            )
        )
        bind_turn_trace(turn_id=turn_id, conversation_id=conversation_id)

        async for chunk in stream_agent_turn(mount, ctx):
            yield chunk

        if ctx.session is not None:
            terminated_by = ctx.session.terminated_by
            gate_interrupted = ctx.session.gate_interrupted
        if ctx.recorder is not None:
            last_step_index = ctx.recorder.last_step_index
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log_exception(
            f"{log_event_prefix}.failed",
            exc=exc,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )
        usage_collector = ctx.usage_collector
        if usage_collector is not None:
            usage_collector.note_failure(exc)
            usage_collector.note_termination(
                terminated_by="error",
                message=str(exc) or "turn failed",
            )
        yield encode_sse_frame(
            create_stream_frame(
                type=StreamFrameType.ERROR,
                code=StreamErrorCode.INTERNAL.value,
                message="turn failed",
            )
        )
    finally:
        if cancel_watch_task is not None:
            cancel_watch_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_watch_task
        if cancel_event.is_set():
            with contextlib.suppress(Exception):
                await finalize_inflight_turn_abort(
                    user_id=user_id,
                    conversation=conversation,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    step_index=last_step_index,
                    persistence=ctx.persistence,
                    agent=ctx.agent,
                    config=ctx.runnable_config,
                    turn_start_messages=ctx.turn_start_messages,
                    already_persisted_close=bool(
                        ctx.recorder is not None and ctx.recorder.final_persisted
                    ),
                )
        elif not gate_interrupted:
            try:
                await turn_auth_store.clear_turn_auth(conversation_id, turn_id)
                await clear_turn_active(conversation)
                await clear_turn_observation_snapshot(conversation_id, turn_id)
            except Exception:
                logger.exception(
                    f"{log_event_prefix}.clear_active_failed",
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                )
        try:
            await touch_conversation_updated(conversation)
        except Exception:
            logger.exception(
                f"{log_event_prefix}.touch_failed",
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
        try:
            guards = ctx.guards
            usage_collector = ctx.usage_collector
            observation = ctx.observation
            session = ctx.session
            if guards is not None and usage_collector is not None and observation is not None:
                if session is not None:
                    terminated_by = session.terminated_by
                if usage_collector.terminated_by != terminated_by:
                    usage_collector.terminated_by = terminated_by
                finalize_observation_segment(
                    observation,
                    model_steps_used=guards.model_steps_used,
                    tool_calls_used=guards.tool_calls_used,
                    wall_clock_seconds=guards.wall_clock_seconds(),
                    terminated_by=terminated_by,
                    termination_message=usage_collector.termination_message,
                )
        except Exception:
            logger.exception(
                f"{log_event_prefix}.usage_record_failed",
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
        try:
            await conversation_turn_lock.release(conversation_id, turn_id)
        except Exception:
            logger.exception(
                f"{log_event_prefix}.release_failed",
                conversation_id=conversation_id,
                turn_id=turn_id,
            )
